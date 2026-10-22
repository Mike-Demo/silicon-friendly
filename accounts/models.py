import uuid
from django.db import models
from django.contrib.auth.hashers import make_password, check_password
from django.core.validators import RegexValidator


validate_username = RegexValidator(
    regex=r"\A[a-z0-9_]+\Z",
    message="Usernames may contain only lowercase letters, numbers, and underscores.",
)


class Carbon(models.Model):
    email = models.EmailField(unique=True)
    username = models.CharField(max_length=64, unique=True, validators=[validate_username])
    password = models.CharField(max_length=128, blank=True, default="")
    name = models.CharField(max_length=128, blank=True, default="")
    auth_provider = models.CharField(max_length=32, default="google")
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "carbons"

    def set_password(self, raw):
        self.password = make_password(raw)

    def check_password(self, raw):
        return check_password(raw, self.password)

    def __str__(self):
        return self.username


class Silicon(models.Model):
    email = models.EmailField(unique=True)
    username = models.CharField(max_length=64, unique=True, validators=[validate_username])
    password = models.CharField(max_length=128)
    auth_token = models.UUIDField(default=uuid.uuid4, unique=True)
    token_last_used = models.DateTimeField(auto_now_add=True)
    search_queries_remaining = models.IntegerField(default=10)
    is_trusted_verifier = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "silicons"

    def set_password(self, raw):
        self.password = make_password(raw)

    def check_password(self, raw):
        return check_password(raw, self.password)

    def regenerate_token(self):
        self.auth_token = uuid.uuid4()
        self.save(update_fields=["auth_token"])

    def __str__(self):
        return self.username
