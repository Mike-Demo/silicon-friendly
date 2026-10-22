from django.db import models
from django.db.models.functions import Lower
from accounts.models import Carbon, Silicon
from websites.models import Website


class PaymentRequest(models.Model):
    class PaymentMethod(models.TextChoices):
        DODO = "dodo"
        CRYPTO = "crypto"

    class Status(models.TextChoices):
        PENDING = "pending"
        COMPLETED = "completed"
        FAILED = "failed"

    class Chain(models.TextChoices):
        BASE = "base"
        POLYGON = "polygon"
        ARBITRUM = "arbitrum"
        ETHEREUM = "ethereum"
        BSC = "bsc"
        AVALANCHE = "avalanche"

    website = models.ForeignKey(Website, on_delete=models.CASCADE, related_name="payments")
    requested_by_carbon = models.ForeignKey(Carbon, on_delete=models.SET_NULL, null=True, blank=True)
    requested_by_silicon = models.ForeignKey(Silicon, on_delete=models.SET_NULL, null=True, blank=True)
    amount_usd = models.DecimalField(max_digits=10, decimal_places=2, default=10.00)
    payment_method = models.CharField(max_length=16, choices=PaymentMethod.choices)
    chain = models.CharField(max_length=16, choices=Chain.choices, null=True, blank=True)
    tx_hash = models.CharField(max_length=128, null=True, blank=True)
    dodo_session_id = models.CharField(max_length=255, null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    max_verification_requests = models.IntegerField(default=3)
    email = models.EmailField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "payment_requests"
        constraints = [
            models.UniqueConstraint(
                Lower("tx_hash"),
                condition=models.Q(tx_hash__isnull=False) & ~models.Q(tx_hash=""),
                name="unique_payment_tx_hash",
            ),
        ]

    def __str__(self):
        return f"Payment {self.id} for {self.website.url} ({self.status})"


class VerificationRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending"
        SERVED = "served"

    payment = models.ForeignKey(PaymentRequest, on_delete=models.CASCADE, related_name="verification_requests")
    website = models.ForeignKey("websites.Website", on_delete=models.CASCADE, related_name="verification_requests")
    requested_by_carbon = models.ForeignKey("accounts.Carbon", on_delete=models.SET_NULL, null=True, blank=True)
    requested_by_silicon = models.ForeignKey("accounts.Silicon", on_delete=models.SET_NULL, null=True, blank=True, related_name="requested_verifications")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    detailed_report = models.TextField(blank=True, default="")
    verified_by_silicon = models.ForeignKey("accounts.Silicon", on_delete=models.SET_NULL, null=True, blank=True, related_name="served_verification_requests")
    level_at_verification = models.CharField(max_length=8, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    served_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "verification_requests"

    def __str__(self):
        return f"VR#{self.id} - {self.website.url} ({self.status})"


def can_create_verification_request(payment):
    """Check if this payment still has verification requests available."""
    used = payment.verification_requests.count()
    return used < payment.max_verification_requests


def remaining_verification_requests(payment):
    return payment.max_verification_requests - payment.verification_requests.count()
