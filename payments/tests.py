from types import SimpleNamespace
from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework.test import APIRequestFactory

from payments.models import PaymentRequest, VerificationRequest
from payments.tasks import _mark_completed
from payments.views import CryptoSubmitView
from websites.models import Website


class CryptoPaymentHashTests(TestCase):
    def setUp(self):
        self.website = Website.objects.create(url="example.com", name="Example")

    def create_payment(self, tx_hash):
        return PaymentRequest.objects.create(
            website=self.website,
            payment_method="crypto",
            chain="base",
            tx_hash=tx_hash,
        )

    def test_transaction_hash_is_unique_case_insensitively(self):
        self.create_payment("0xAbC123")

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.create_payment("0xabc123")

    def test_submission_rejects_hash_already_attached_to_payment(self):
        self.create_payment("0xAbC123")
        request = APIRequestFactory().post(
            "/api/payments/crypto/submit/",
            {
                "chain": "base",
                "tx_hash": "0xabc123",
                "website_url": self.website.url,
            },
            format="json",
        )
        request.session = {}
        request.silicon = SimpleNamespace(id=1)

        with patch("payments.views.check_rate_limit", return_value=(True, None)):
            response = CryptoSubmitView.as_view()(request)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(PaymentRequest.objects.count(), 1)

    @patch("common.mail.send_email")
    def test_completion_consumes_payment_once(self, send_email):
        payment = self.create_payment("0xabc123")

        self.assertTrue(_mark_completed(payment))
        self.assertFalse(_mark_completed(payment))

        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentRequest.Status.COMPLETED)
        self.assertEqual(VerificationRequest.objects.filter(payment=payment).count(), 1)
        self.assertEqual(send_email.call_count, 2)
