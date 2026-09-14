"""Authentication backends for EduCore."""
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q
from .models import User

class DualAuthBackend(ModelBackend):
    """Allows authentication with either phone_e164 or email (IAM-001).
    
    Enforces account lockout policy (IAM-008: 10 failed attempts locks for 15 mins).
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        identifier = username or kwargs.get('phone_e164') or kwargs.get('email')
        if not identifier or not password:
            return None

        # Clean identifier
        identifier = identifier.strip()

        try:
            # Query across all tenants during authentication
            user = User.all_tenants.get(
                Q(phone_e164=identifier) | Q(email__iexact=identifier)
            )
        except User.DoesNotExist:
            # Run password hasher to prevent timing attacks
            User().set_password(password)
            return None
        except User.MultipleObjectsReturned:
            return None

        if user.is_locked:
            return None

        if user.check_password(password) and self.user_can_authenticate(user):
            user.record_login_success()
            return user
        else:
            user.record_login_failure()
            return None

    def get_user(self, user_id):
        try:
            return User.all_tenants.get(pk=user_id)
        except User.DoesNotExist:
            return None
