import logging
from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task
def check_pending_payments():
    """
    Runs every minute via celery beat.
    For each pending PaymentRequest:
      - crypto: try on-chain verification
      - dodo: try status check via API
      - check max wait timeout
    """
    from payments.models import PaymentRequest, VerificationRequest
    from payments.config import PAYMENT_MAX_WAIT
    from payments.verify_crypto import verify_crypto_payment
    from common.mail import send_email
    import env

    pending = PaymentRequest.objects.filter(status="pending")
    now = timezone.now()

    for payment in pending:
        age_minutes = (now - payment.created_at).total_seconds() / 60
        website_name = payment.website.url

        # -- Crypto verification --
        if payment.payment_method == "crypto" and payment.tx_hash and payment.chain:
            try:
                result = verify_crypto_payment(
                    chain=payment.chain,
                    tx_hash=payment.tx_hash,
                    wallet_address=env.USDC_EVM_ADDRESS,
                )
                if result["verified"]:
                    _mark_completed(payment)
                    logger.info(f"Payment #{payment.id} verified on-chain ({payment.chain})")
                    continue
                elif result["reason"] and "mismatch" in result["reason"]:
                    # Definitive failure (wrong amount, wrong recipient)
                    _mark_failed(payment, result["reason"])
                    logger.info(f"Payment #{payment.id} failed: {result['reason']}")
                    continue
                # Otherwise: not found yet, keep waiting
            except Exception as e:
                logger.warning(f"Crypto verify error for payment #{payment.id}: {e}")

        # -- Dodo status check --
        if payment.payment_method == "dodo" and payment.dodo_session_id:
            try:
                dodo_status = _check_dodo_status(payment.dodo_session_id)
                if dodo_status == "succeeded":
                    _mark_completed(payment)
                    logger.info(f"Payment #{payment.id} confirmed via Dodo API")
                    continue
                elif dodo_status in ("failed", "expired", "cancelled"):
                    _mark_failed(payment, f"dodo status: {dodo_status}")
                    logger.info(f"Payment #{payment.id} failed via Dodo: {dodo_status}")
                    continue
            except Exception as e:
                logger.warning(f"Dodo status check error for payment #{payment.id}: {e}")

        # -- Timeout check --
        if payment.payment_method == "crypto":
            max_wait = PAYMENT_MAX_WAIT.get(payment.chain, 10)
        else:
            max_wait = PAYMENT_MAX_WAIT.get(payment.payment_method, 20)

        if age_minutes > max_wait:
            _mark_failed(payment, f"timed out after {int(age_minutes)} minutes")
            logger.info(f"Payment #{payment.id} timed out ({int(age_minutes)}m > {max_wait}m)")


def _check_dodo_status(dodo_session_id):
    """Check payment status via DodoPayments API. Returns status string or None."""
    from dodopayments import DodoPayments
    import env

    try:
        client = DodoPayments(bearer_token=env.DODOPAYMENTS_API_KEY)
        payment = client.payments.retrieve(dodo_session_id)
        return getattr(payment, "status", None)
    except Exception as e:
        logger.warning(f"Dodo API retrieve failed for {dodo_session_id}: {e}")
        return None


def _mark_completed(payment):
    """Mark payment as completed, send success emails, create first VerificationRequest."""
    from django.db import transaction
    from payments.models import PaymentRequest, VerificationRequest
    from common.mail import send_email

    email = payment.requested_by_carbon.email if payment.requested_by_carbon else ""
    with transaction.atomic():
        if not PaymentRequest.objects.filter(pk=payment.pk, status="pending").update(
            status="completed",
            email=email or payment.email,
        ):
            return False
        payment.refresh_from_db(fields=["status", "email"])

        # Auto-create first verification request
        VerificationRequest.objects.create(
            payment=payment,
            website=payment.website,
            requested_by_carbon=payment.requested_by_carbon,
            requested_by_silicon=payment.requested_by_silicon,
            status="pending",
        )

    website_name = payment.website.url
    customer_email = payment.email
    method = payment.payment_method
    chain = payment.chain or ""
    method_display = f"crypto ({chain})" if method == "crypto" else "card/upi"

    if customer_email:
        send_email(
            to_email=customer_email,
            subject=f"Payment received - {website_name} verification",
            html_body=(
                '<pre style="font-family: Courier New, monospace; white-space: pre-wrap;">'
                '&gt; siliconfriendly\n'
                '---------------------------\n\n'
                'payment received.\n\n'
                f'&gt; website:  {website_name}\n'
                f'&gt; amount:   ${payment.amount_usd}\n'
                f'&gt; method:   {method_display}\n'
                '&gt; status:   completed\n\n'
                'a verified silicon will evaluate your\n'
                'website and send you a detailed report.\n\n'
                'you can track progress at:\n'
                f'https://siliconfriendly.com/w/{website_name}/\n\n'
                '---------------------------\n'
                'siliconfriendly.com\n'
                '</pre>'
            ),
        )

    team_emails = ["shubhastro2@gmail.com", "saketdev12@gmail.com"]
    for team_email in team_emails:
        send_email(
            to_email=team_email,
            subject=f"Payment verified: {website_name}",
            html_body=(
                '<pre style="font-family: Courier New, monospace; white-space: pre-wrap;">'
                '&gt; payment verified\n'
                '---------------------------\n\n'
                f'&gt; website:  {website_name}\n'
                f'&gt; email:    {customer_email or "unknown"}\n'
                f'&gt; amount:   ${payment.amount_usd}\n'
                f'&gt; method:   {method_display}\n\n'
                '---------------------------\n'
                '</pre>'
            ),
        )
    return True


def _send_abandoned_payment_email(payment):
    """Send a followup email if carbon's payment failed and no other succeeded in the last hour."""
    from common.mail import send_email
    from payments.models import PaymentRequest
    from django.utils import timezone
    from datetime import timedelta
    import html as html_mod

    carbon = payment.requested_by_carbon
    if not carbon or not carbon.email:
        return

    one_hour_ago = timezone.now() - timedelta(hours=1)

    # Check if they had any successful payment in the last hour
    has_success = PaymentRequest.objects.filter(
        requested_by_carbon=carbon,
        status="completed",
        created_at__gte=one_hour_ago,
    ).exists()

    if has_success:
        return

    # Check we haven't already sent this (idempotency via cache)
    from django.core.cache import cache
    cache_key = f"abandoned_payment_email_{payment.id}"
    if cache.get(cache_key):
        return

    domain = payment.website.url
    name = html_mod.escape(payment.website.name or domain)
    page_url = f"https://siliconfriendly.com/w/{domain}/"

    email_html = '<div style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;max-width:600px;margin:0 auto;background:#ede8e0;">'
    email_html += '<div style="padding:32px;text-align:center;"><a href="https://siliconfriendly.com" style="font-family:Courier New,monospace;font-size:11px;color:#999;text-decoration:none;text-transform:uppercase;letter-spacing:0.15em;">siliconfriendly.com</a></div>'
    email_html += '<div style="padding:0 32px 32px;">'
    email_html += '<p style="font-size:15px;color:#1a1a1a;line-height:1.7;margin:0 0 16px;">Hey,</p>'
    email_html += f'<p style="font-size:15px;color:#1a1a1a;line-height:1.7;margin:0 0 16px;">We noticed you tried to re-verify <strong><a href="{page_url}" style="color:#1a1a1a;">{name}</a></strong>, but the payment didn\'t go through.</p>'
    email_html += '<p style="font-size:15px;color:#1a1a1a;line-height:1.7;margin:0 0 8px;">Was this because:</p>'
    email_html += '<ul style="font-size:15px;color:#1a1a1a;line-height:1.8;margin:0 0 16px;padding-left:20px;">'
    email_html += '<li>The payment failed?</li>'
    email_html += '<li>You changed your mind?</li>'
    email_html += '<li>Something else we need to improve?</li>'
    email_html += '</ul>'
    email_html += '<p style="font-size:15px;color:#1a1a1a;line-height:1.7;margin:0 0 16px;">We\'d love to hear from you. Just reply to this email and let us know.</p>'
    email_html += '<p style="font-size:14px;color:#666;line-height:1.7;margin:0 0 8px;">If you decide to pay, it helps us offer free first verifications and keep Silicon Friendly running.</p>'
    email_html += '<p style="font-size:14px;color:#666;line-height:1.7;margin:0 0 20px;">And your feedback helps us ensure that the Silicon Friendly open standard becomes a global standard that stays up-to-date with the latest in AI.</p>'
    email_html += f'<p style="font-size:15px;color:#1a1a1a;line-height:1.7;margin:0 0 24px;">You can re-verify from <a href="{page_url}" style="color:#1a1a1a;font-weight:700;">your website\'s page</a>.</p>'
    email_html += f'<div style="text-align:center;margin:28px 0;"><a href="{page_url}" style="display:inline-block;background:#1a1a1a;color:#ede8e0;padding:14px 32px;text-decoration:none;font-size:15px;font-weight:700;">Visit SiliconFriendly</a></div>'
    email_html += '</div>'
    email_html += '<div style="padding:20px 32px;border-top:1px solid #d4cfc7;text-align:center;"><a href="https://siliconfriendly.com" style="font-family:Courier New,monospace;font-size:11px;color:#666;text-decoration:none;font-weight:700;">siliconfriendly.com</a><span style="color:#d4cfc7;margin:0 6px;">&middot;</span><a href="https://unlikefraction.com" style="font-family:Courier New,monospace;font-size:11px;color:#999;text-decoration:none;">unlikefraction.com</a></div>'
    email_html += '</div>'

    send_email(
        to_email=carbon.email,
        subject="Did something go wrong with Silicon Friendly?",
        html_body=email_html,
    )

    cache.set(cache_key, True, timeout=86400)  # Don't re-send for 24h


def _mark_failed(payment, reason):
    """Mark payment as failed and send failure emails."""
    from common.mail import send_email

    payment.status = "failed"
    payment.save(update_fields=["status"])

    # Send abandoned payment followup
    try:
        _send_abandoned_payment_email(payment)
    except Exception as e:
        logger.warning(f"Failed to send abandoned payment email: {e}")

    website_name = payment.website.url
    customer_email = payment.email or (
        payment.requested_by_carbon.email if payment.requested_by_carbon else None
    )
    method = payment.payment_method
    chain = payment.chain or ""
    method_display = f"crypto ({chain})" if method == "crypto" else "card/upi"

    if customer_email:
        send_email(
            to_email=customer_email,
            subject=f"Payment could not be verified - {website_name}",
            html_body=(
                '<pre style="font-family: Courier New, monospace; white-space: pre-wrap;">'
                '&gt; siliconfriendly\n'
                '---------------------------\n\n'
                'payment could not be verified.\n\n'
                f'&gt; website:  {website_name}\n'
                f'&gt; method:   {method_display}\n'
                '&gt; status:   failed\n'
                f'&gt; reason:   {reason}\n\n'
                'if this was a mistake, please contact\n'
                'team@unlikefraction.com with your\n'
                'transaction details.\n\n'
                '---------------------------\n'
                'siliconfriendly.com\n'
                '</pre>'
            ),
        )

    team_emails = ["shubhastro2@gmail.com", "saketdev12@gmail.com"]
    for team_email in team_emails:
        send_email(
            to_email=team_email,
            subject=f"Payment failed: {website_name} (#{payment.id})",
            html_body=(
                '<pre style="font-family: Courier New, monospace; white-space: pre-wrap;">'
                '&gt; payment failed\n'
                '---------------------------\n\n'
                f'&gt; payment:  #{payment.id}\n'
                f'&gt; website:  {website_name}\n'
                f'&gt; email:    {customer_email or "unknown"}\n'
                f'&gt; method:   {method_display}\n'
                f'&gt; reason:   {reason}\n\n'
                '---------------------------\n'
                '</pre>'
            ),
        )
