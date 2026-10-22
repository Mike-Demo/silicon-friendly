from django.db import migrations, models
from django.db.models.functions import Lower


def clear_duplicate_transaction_hashes(apps, schema_editor):
    PaymentRequest = apps.get_model("payments", "PaymentRequest")
    seen_hashes = set()
    duplicates = []

    payments = PaymentRequest.objects.exclude(tx_hash__isnull=True).exclude(
        tx_hash=""
    ).order_by("created_at", "pk")
    for payment_id, tx_hash, status in payments.values_list(
        "pk", "tx_hash", "status"
    ).iterator():
        normalized_hash = tx_hash.lower()
        if normalized_hash in seen_hashes:
            duplicates.append((payment_id, status))
        else:
            seen_hashes.add(normalized_hash)

    for payment_id, status in duplicates:
        updates = {"tx_hash": None}
        if status == "pending":
            updates["status"] = "failed"
        PaymentRequest.objects.filter(pk=payment_id).update(**updates)


class Migration(migrations.Migration):

    dependencies = [
        ("payments", "0006_alter_paymentrequest_chain"),
    ]

    operations = [
        migrations.RunPython(
            clear_duplicate_transaction_hashes,
            migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="paymentrequest",
            constraint=models.UniqueConstraint(
                Lower("tx_hash"),
                condition=models.Q(tx_hash__isnull=False) & ~models.Q(tx_hash=""),
                name="unique_payment_tx_hash",
            ),
        ),
    ]
