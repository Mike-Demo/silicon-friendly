from rest_framework.views import APIView
from django.db import IntegrityError, transaction
from common.ratelimit import check_rate_limit, rate_limit_response, get_client_ip
from rest_framework import permissions
from dodopayments import DodoPayments
from core.utils import api_response, error_response
from accounts.models import Carbon
from websites.models import Website
from payments.models import PaymentRequest, VerificationRequest, can_create_verification_request, remaining_verification_requests
import env


class DodoCreateView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        carbon_id = request.session.get("carbon_id")
        if not carbon_id:
            return error_response("Carbon authentication required.", status=401)
        try:
            carbon = Carbon.objects.get(id=carbon_id, is_active=True)
        except Carbon.DoesNotExist:
            return error_response("Carbon not found.", status=401)

        # Rate limit: 5 payments per hour per user
        allowed, retry_after = check_rate_limit(f"payment:carbon:{carbon.id}", 5, 3600)
        if not allowed:
            return rate_limit_response(retry_after)

        website_url = request.data.get("website_url", "").strip()
        if not website_url:
            return error_response("website_url is required.")

        try:
            website = Website.objects.get(url=website_url)
        except Website.DoesNotExist:
            return error_response("Website not found.", status=404)

        client = DodoPayments(bearer_token=env.DODOPAYMENTS_API_KEY)
        amount_cents = 1000  # $10

        payment = PaymentRequest.objects.create(
            website=website,
            requested_by_carbon=carbon,
            amount_usd=10.00,
            payment_method="dodo",
        )

        session = client.checkout_sessions.create(
            product_cart=[{
                "product_id": "pdt_0NYiXePm40uSt6H6x3aGn",
                "quantity": 1,
                "amount": amount_cents,
            }],
            return_url=f"{env.FRONTEND_BASE_URL}/w/{website.url}/?status=succeeded&payment_id={payment.id}&email={carbon.email}",
            customer={"email": carbon.email, "name": carbon.username},
            metadata={
                "payment_id": str(payment.id),
                "website_id": str(website.id),
            },
        )

        payment.dodo_session_id = session.session_id
        payment.save(update_fields=["dodo_session_id"])

        return api_response(
            {
                "checkout_url": session.checkout_url,
                "session_id": session.session_id,
                "payment_id": payment.id,
            },
            meta={
                "checkout_url": "Redirect the user to this URL to complete payment",
                "session_id": "Dodo checkout session ID for tracking",
                "payment_id": "Internal payment request ID",
            },
        )


class DodoWebhookView(APIView):
    permission_classes = [permissions.AllowAny]

    OUR_PRODUCT_ID = "pdt_0NYiXePm40uSt6H6x3aGn"

    def post(self, request):
        payload = request.data
        event_type = payload.get("event_type", "")
        data = payload.get("data", {})
        metadata = data.get("metadata", {})

        # Filter: only process successful payments for our product
        product_cart = data.get("product_cart", [])
        is_ours = any(item.get("product_id") == self.OUR_PRODUCT_ID for item in product_cart)
        if not is_ours:
            return api_response({"status": "ignored", "reason": "not our product"}, meta={"status": "Webhook processing status"})

        if event_type != "payment.succeeded" and data.get("status") != "succeeded":
            return api_response({"status": "ignored", "reason": "not a success event"}, meta={"status": "Webhook processing status"})

        payment_id = metadata.get("payment_id")
        if not payment_id:
            return api_response({"status": "no_payment_id"}, meta={"status": "Webhook processing status"})

        try:
            payment = PaymentRequest.objects.get(id=payment_id)
        except PaymentRequest.DoesNotExist:
            return error_response("Payment not found.", status=404)

        payment.status = "completed"
        if payment.requested_by_carbon and payment.requested_by_carbon.email:
            payment.email = payment.requested_by_carbon.email
        payment.save(update_fields=["status", "email"])

        # Auto-create first verification request
        VerificationRequest.objects.create(
            payment=payment,
            website=payment.website,
            requested_by_carbon=payment.requested_by_carbon,
            requested_by_silicon=payment.requested_by_silicon,
            status="pending",
        )

        # Send confirmation emails
        from common.mail import send_email
        website_name = payment.website.url
        customer_email = payment.email

        if customer_email:
            send_email(
                to_email=customer_email,
                subject=f"Payment received - {website_name} verification",
                html_body=f"""<pre style="font-family: Courier New, monospace; white-space: pre-wrap;">&gt; siliconfriendly
---------------------------

payment received.

&gt; website:  {website_name}
&gt; amount:   ${payment.amount_usd}
&gt; status:   completed

a verified silicon will evaluate your
website and send you a detailed report.

you can track progress at:
https://siliconfriendly.com/w/{website_name}/

---------------------------
siliconfriendly.com
</pre>""",
            )

        team_emails = ["shubhastro2@gmail.com", "saketdev12@gmail.com"]
        for team_email in team_emails:
            send_email(
                to_email=team_email,
                subject=f"New payment: {website_name} verification",
                html_body=f"""<pre style="font-family: Courier New, monospace; white-space: pre-wrap;">&gt; new payment
---------------------------

&gt; website:  {website_name}
&gt; email:    {customer_email or unknown}
&gt; amount:   ${payment.amount_usd}
&gt; method:   {payment.payment_method}

---------------------------
</pre>""",
            )

        return api_response({"status": "processed"}, meta={"status": "Webhook processing status"})


class CryptoSubmitView(APIView):

    def post(self, request):
        carbon_id = request.session.get("carbon_id")
        silicon = getattr(request, "silicon", None)
        carbon = None
        if carbon_id:
            try:
                carbon = Carbon.objects.get(id=carbon_id, is_active=True)
            except Carbon.DoesNotExist:
                pass

        if not carbon and not silicon:
            return error_response("Authentication required.", status=401)

        # Rate limit: 5 payments per hour per user
        user_key = f"carbon:{carbon.id}" if carbon else f"silicon:{silicon.id}"
        allowed, retry_after = check_rate_limit(f"payment:{user_key}", 5, 3600)
        if not allowed:
            return rate_limit_response(retry_after)

        chain = request.data.get("chain", "").lower()
        tx_hash = request.data.get("tx_hash", "").strip().lower()
        website_url = request.data.get("website_url", "").strip()

        if chain not in ("base", "polygon", "arbitrum", "ethereum", "bsc", "avalanche"):
            return error_response("chain must be one of: base, polygon, arbitrum, ethereum, bsc, avalanche")
        if not tx_hash:
            return error_response("tx_hash is required.")
        if not website_url:
            return error_response("website_url is required.")

        if PaymentRequest.objects.filter(tx_hash__iexact=tx_hash).exists():
            return error_response("This transaction hash has already been submitted.", status=409)

        try:
            website = Website.objects.get(url=website_url)
        except Website.DoesNotExist:
            return error_response("Website not found.", status=404)

        wallet = env.USDC_EVM_ADDRESS

        try:
            with transaction.atomic():
                payment = PaymentRequest.objects.create(
                    website=website,
                    requested_by_carbon=carbon,
                    requested_by_silicon=silicon,
                    amount_usd=10.00,
                    payment_method="crypto",
                    chain=chain,
                    tx_hash=tx_hash,
                )
        except IntegrityError:
            if PaymentRequest.objects.filter(tx_hash__iexact=tx_hash).exists():
                return error_response("This transaction hash has already been submitted.", status=409)
            raise

        return api_response(
            {
                "payment_id": payment.id,
                "wallet_address": wallet,
                "chain": chain,
                "tx_hash": tx_hash,
                "status": payment.status,
            },
            meta={
                "payment_id": "Internal payment request ID",
                "wallet_address": "The wallet address to send USDC to",
                "chain": "The blockchain network",
                "tx_hash": "The transaction hash submitted",
                "status": "Current payment verification status (pending until admin verifies)",
            },
        )


class CryptoVerifyView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request, tx_hash):
        try:
            payment = PaymentRequest.objects.get(tx_hash=tx_hash)
        except PaymentRequest.DoesNotExist:
            return error_response("Payment not found.", status=404)

        return api_response(
            {
                "tx_hash": payment.tx_hash,
                "chain": payment.chain,
                "status": payment.status,
                "website": payment.website.url,
            },
            meta={
                "tx_hash": "The transaction hash",
                "chain": "The blockchain network",
                "status": "Current payment verification status",
                "website": "The website this payment is for",
            },
        )


class PaymentStatusView(APIView):
    """Returns payment requests and their verification status."""

    def get(self, request):
        silicon = getattr(request, "silicon", None)
        if not silicon:
            return error_response("Silicon authentication required.", status=401)

        if silicon.is_trusted_verifier:
            payments = PaymentRequest.objects.all().order_by("-created_at")[:50]
        else:
            payments = PaymentRequest.objects.filter(requested_by_silicon=silicon).order_by("-created_at")[:50]

        results = []
        for p in payments:
            vrs = p.verification_requests.all().order_by("-created_at")

            entry = {
                "website": p.website.url,
                "status": p.status,
                "created_at": p.created_at.isoformat(),
                "payment_method": p.payment_method,
                "amount_usd": str(p.amount_usd),
                "max_verification_requests": p.max_verification_requests,
                "remaining_verification_requests": remaining_verification_requests(p),
                "verification_requests": [],
            }

            for vr in vrs:
                vr_data = {
                    "id": vr.id,
                    "status": vr.status,
                    "created_at": vr.created_at.isoformat(),
                }
                if vr.status == "served":
                    vr_data["detailed_report"] = vr.detailed_report
                    vr_data["verified_by"] = vr.verified_by_silicon.username if vr.verified_by_silicon else None
                    vr_data["level_at_verification"] = vr.level_at_verification
                    vr_data["served_at"] = vr.served_at.isoformat() if vr.served_at else None
                entry["verification_requests"].append(vr_data)

            results.append(entry)

        return api_response(
            {"payments": results},
            meta={
                "payments": "List of payment requests with verification request details",
            },
        )


class CreateVerificationRequestView(APIView):
    """Create a new verification request for a website with a completed payment."""

    def post(self, request, domain):
        from websites.views import _normalize_url

        carbon, silicon = None, getattr(request, "silicon", None)
        carbon_id = request.session.get("carbon_id")
        if carbon_id:
            try:
                carbon = Carbon.objects.get(id=carbon_id, is_active=True)
            except Carbon.DoesNotExist:
                pass

        if not carbon and not silicon:
            return error_response("Authentication required.", status=401)

        domain = _normalize_url(domain)
        try:
            website = Website.objects.get(url=domain)
        except Website.DoesNotExist:
            return error_response("Website not found.", status=404)

        active_payment = PaymentRequest.objects.filter(
            website=website, status="completed"
        ).order_by("-created_at").first()

        if not active_payment:
            return error_response("No completed payment found for this website. Purchase verification first.", status=403)

        # Check for existing pending verification request
        existing_pending = VerificationRequest.objects.filter(
            payment=active_payment,
            website=website,
            status="pending",
        ).exists()
        if existing_pending:
            return error_response(
                "A verification request is already pending for this website. Please wait for it to be served.",
                status=400,
            )

        if not can_create_verification_request(active_payment):
            return error_response(
                f"Verification request quota exceeded. This payment allows {active_payment.max_verification_requests} requests.",
                status=400,
            )

        vr = VerificationRequest.objects.create(
            payment=active_payment,
            website=website,
            requested_by_carbon=carbon,
            requested_by_silicon=silicon,
            status="pending",
        )

        return api_response(
            {
                "verification_request_id": vr.id,
                "website": website.url,
                "status": "pending",
                "remaining_requests": remaining_verification_requests(active_payment),
            },
            meta={
                "verification_request_id": "ID of the new verification request",
                "website": "The website domain",
                "status": "Current status of the verification request",
                "remaining_requests": "How many verification requests remain on this payment",
            },
        )
