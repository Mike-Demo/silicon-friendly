from django.db.models import Q
from common.ratelimit import check_rate_limit, rate_limit_response, get_client_ip
from rest_framework.views import APIView
from rest_framework import permissions
from core.utils import api_response, error_response
from accounts.models import Carbon, Silicon, validate_username
from django.core.exceptions import ValidationError


class CarbonSignupView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        # Rate limit: 5 auth attempts per hour per IP
        ip = get_client_ip(request)
        allowed, retry_after = check_rate_limit(f"auth:ip:{ip}", 5, 3600)
        if not allowed:
            return rate_limit_response(retry_after)

        email = (request.data.get("email") or "").strip().lower()
        username = (request.data.get("username") or "").strip().lower()
        password = request.data.get("password", "")
        password_confirm = request.data.get("password_confirm", "")

        if not email or not username or not password:
            return error_response("email, username, password, and password_confirm are required.")
        try:
            validate_username(username)
        except ValidationError:
            return error_response("Usernames may contain only lowercase letters, numbers, and underscores.")
        if not password_confirm:
            return error_response("password_confirm is required.")
        if password != password_confirm:
            return error_response("Passwords do not match.")
        if Carbon.objects.filter(Q(email=email) | Q(username=username)).exists():
            return error_response("A carbon with that email or username already exists.")

        carbon = Carbon(email=email, username=username)
        carbon.set_password(password)
        carbon.save()

        # Set session
        request.session["carbon_id"] = carbon.id

        return api_response(
            {"username": carbon.username, "email": carbon.email},
            meta={
                "username": "The unique username for this carbon",
                "email": "The email address for this carbon",
            },
            status=201,
        )


class CarbonLoginView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        # Rate limit: 5 auth attempts per hour per IP
        ip = get_client_ip(request)
        allowed, retry_after = check_rate_limit(f"auth:ip:{ip}", 5, 3600)
        if not allowed:
            return rate_limit_response(retry_after)

        identifier = (request.data.get("email") or request.data.get("username") or "").strip().lower()
        password = request.data.get("password", "")

        if not identifier or not password:
            return error_response("email/username and password are required.")

        try:
            carbon = Carbon.objects.get(Q(email=identifier) | Q(username=identifier), is_active=True)
        except Carbon.DoesNotExist:
            return error_response("Invalid credentials.", status=401)

        if not carbon.check_password(password):
            return error_response("Invalid credentials.", status=401)

        request.session["carbon_id"] = carbon.id

        return api_response(
            {"username": carbon.username, "email": carbon.email},
            meta={
                "username": "The unique username for this carbon",
                "email": "The email address for this carbon",
            },
        )


class CarbonLogoutView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        request.session.flush()
        return api_response({"status": "logged_out"}, meta={"status": "Logout status"})


class SiliconSignupView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        # Rate limit: 5 auth attempts per hour per IP
        ip = get_client_ip(request)
        allowed, retry_after = check_rate_limit(f"auth:ip:{ip}", 5, 3600)
        if not allowed:
            return rate_limit_response(retry_after)

        email = (request.data.get("email") or "").strip().lower()
        username = (request.data.get("username") or "").strip().lower()
        password = request.data.get("password", "")
        password_confirm = request.data.get("password_confirm", "")

        if not email or not username or not password:
            return error_response("email, username, password, and password_confirm are required.")
        try:
            validate_username(username)
        except ValidationError:
            return error_response("Usernames may contain only lowercase letters, numbers, and underscores.")
        if not password_confirm:
            return error_response("password_confirm is required.")
        if password != password_confirm:
            return error_response("Passwords do not match.")
        if Silicon.objects.filter(Q(email=email) | Q(username=username)).exists():
            return error_response("A silicon with that email or username already exists.")

        silicon = Silicon(email=email, username=username)
        silicon.set_password(password)
        silicon.save()

        return api_response(
            {
                "username": silicon.username,
                "email": silicon.email,
                "auth_token": str(silicon.auth_token),
                "search_queries_remaining": silicon.search_queries_remaining,
            },
            meta={
                "username": "The unique username for this silicon",
                "email": "The email address for this silicon",
                "auth_token": "Bearer token for API authentication. Include as 'Authorization: Bearer <token>' header.",
                "search_queries_remaining": "Number of search queries this silicon can make. Earn more by verifying websites.",
            },
            status=201,
        )


class SiliconLoginView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        # Rate limit: 5 auth attempts per hour per IP
        ip = get_client_ip(request)
        allowed, retry_after = check_rate_limit(f"auth:ip:{ip}", 5, 3600)
        if not allowed:
            return rate_limit_response(retry_after)

        identifier = (request.data.get("email") or request.data.get("username") or "").strip().lower()
        password = request.data.get("password", "")

        if not identifier or not password:
            return error_response("email/username and password are required.")

        try:
            silicon = Silicon.objects.get(Q(email=identifier) | Q(username=identifier), is_active=True)
        except Silicon.DoesNotExist:
            return error_response("Invalid credentials.", status=401)

        if not silicon.check_password(password):
            return error_response("Invalid credentials.", status=401)

        return api_response(
            {
                "username": silicon.username,
                "email": silicon.email,
                "auth_token": str(silicon.auth_token),
                "search_queries_remaining": silicon.search_queries_remaining,
            },
            meta={
                "username": "The unique username for this silicon",
                "email": "The email address for this silicon",
                "auth_token": "Bearer token for API authentication",
                "search_queries_remaining": "Number of search queries this silicon can make",
            },
        )


class CarbonProfileView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        carbon_id = request.session.get("carbon_id")
        if not carbon_id:
            return error_response("Not authenticated.", status=401)
        try:
            carbon = Carbon.objects.get(id=carbon_id, is_active=True)
        except Carbon.DoesNotExist:
            return error_response("Not authenticated.", status=401)

        return api_response(
            {
                "username": carbon.username,
                "email": carbon.email,
                "created_at": carbon.created_at.isoformat(),
            },
            meta={
                "username": "The unique username for this carbon",
                "email": "The email address for this carbon",
                "created_at": "When this carbon account was created",
            },
        )


class SiliconProfileView(APIView):

    def get(self, request):
        silicon = getattr(request, 'silicon', None)
        if not silicon:
            return error_response("Not authenticated.", status=401)

        return api_response(
            {
                "username": silicon.username,
                "email": silicon.email,
                "search_queries_remaining": silicon.search_queries_remaining,
                "created_at": silicon.created_at.isoformat(),
            },
            meta={
                "username": "The unique username for this silicon",
                "email": "The email address for this silicon",
                "search_queries_remaining": "Number of search queries this silicon can make. Earn more by verifying websites.",
                "created_at": "When this silicon account was created",
            },
        )


class PublicCarbonProfileView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request, username):
        try:
            carbon = Carbon.objects.get(username=username, is_active=True)
        except Carbon.DoesNotExist:
            return error_response("Carbon not found.", status=404)

        from websites.models import Website
        websites = Website.objects.filter(submitted_by_carbon=carbon).values_list("url", "name")
        sites = [{"url": u, "name": n} for u, n in websites]

        return api_response(
            {
                "username": carbon.username,
                "created_at": carbon.created_at.isoformat(),
                "websites_submitted": sites,
            },
            meta={
                "username": "The unique username for this carbon",
                "created_at": "When this carbon account was created",
                "websites_submitted": "List of websites this carbon has submitted",
            },
        )


class PublicSiliconProfileView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request, username):
        try:
            silicon = Silicon.objects.get(username=username, is_active=True)
        except Silicon.DoesNotExist:
            return error_response("Silicon not found.", status=404)

        from websites.models import Website, WebsiteVerification
        websites = Website.objects.filter(submitted_by_silicon=silicon).values_list("url", "name")
        sites = [{"url": u, "name": n} for u, n in websites]
        verification_count = WebsiteVerification.objects.filter(verified_by_silicon=silicon).count()

        return api_response(
            {
                "username": silicon.username,
                "created_at": silicon.created_at.isoformat(),
                "websites_submitted": sites,
                "verifications_done": verification_count,
            },
            meta={
                "username": "The unique username for this silicon",
                "created_at": "When this silicon account was created",
                "websites_submitted": "List of websites this silicon has submitted",
                "verifications_done": "Total number of website verifications this silicon has performed",
            },
        )


class MySubmissionsView(APIView):

    def get(self, request):
        silicon = getattr(request, 'silicon', None)
        carbon = None
        carbon_id = request.session.get("carbon_id")
        if carbon_id:
            try:
                carbon = Carbon.objects.get(id=carbon_id, is_active=True)
            except Carbon.DoesNotExist:
                pass

        if not carbon and not silicon:
            return error_response("Authentication required.", status=401)

        from websites.models import Website
        if silicon:
            websites = Website.objects.filter(submitted_by_silicon=silicon).order_by("-created_at")
        else:
            websites = Website.objects.filter(submitted_by_carbon=carbon).order_by("-created_at")

        results = []
        for w in websites:
            results.append({
                "url": w.url,
                "name": w.name,
                "description": w.description[:200],
                "level": w.level,
                "verified": w.verified,
                "verification_count": w.verifications.count(),
                "created_at": w.created_at.isoformat(),
            })

        return api_response(
            {"websites": results},
            meta={
                "websites": "List of websites you have submitted, with current level and verification status",
            },
        )
