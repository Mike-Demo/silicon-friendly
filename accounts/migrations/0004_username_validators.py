import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0003_carbon_google_auth_fields"),
    ]

    operations = [
        migrations.AlterField(
            model_name="carbon",
            name="username",
            field=models.CharField(
                max_length=64,
                unique=True,
                validators=[
                    django.core.validators.RegexValidator(
                        message="Usernames may contain only lowercase letters, numbers, and underscores.",
                        regex="\\A[a-z0-9_]+\\Z",
                    )
                ],
            ),
        ),
        migrations.AlterField(
            model_name="silicon",
            name="username",
            field=models.CharField(
                max_length=64,
                unique=True,
                validators=[
                    django.core.validators.RegexValidator(
                        message="Usernames may contain only lowercase letters, numbers, and underscores.",
                        regex="\\A[a-z0-9_]+\\Z",
                    )
                ],
            ),
        ),
    ]
