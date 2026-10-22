from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from rest_framework.test import APIRequestFactory

from accounts.models import Carbon, Silicon
from accounts.views import CarbonSignupView, SiliconSignupView


class UsernameValidationTests(SimpleTestCase):
    def test_username_model_validators_allow_lowercase_letters_digits_and_underscores(self):
        for model in (Carbon, Silicon):
            model._meta.get_field("username").run_validators("agent_123")

    def test_username_model_validators_reject_other_characters(self):
        for model in (Carbon, Silicon):
            with self.subTest(model=model.__name__):
                with self.assertRaises(ValidationError):
                    model._meta.get_field("username").run_validators("Agent-123")

    @patch("accounts.views.check_rate_limit", return_value=(True, 0))
    def test_signup_apis_reject_invalid_usernames(self, _check_rate_limit):
        factory = APIRequestFactory()
        payload = {
            "email": "user@example.com",
            "username": "bad.name",
            "password": "password",
            "password_confirm": "password",
        }

        for view in (CarbonSignupView, SiliconSignupView):
            with self.subTest(view=view.__name__):
                request = factory.post("/signup/", payload, format="json")
                response = view.as_view()(request)
                self.assertEqual(response.status_code, 400)
                self.assertIn("lowercase letters", response.data["error"])
