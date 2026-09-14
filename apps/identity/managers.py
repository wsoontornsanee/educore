"""Custom managers for Identity models."""
from django.contrib.auth.base_user import BaseUserManager
from apps.core.managers import TenantManager, AllTenantsManager

class UserManager(BaseUserManager, TenantManager):
    """Multi-tenant User Manager supporting phone E.164 and email authentication."""

    def create_user(self, phone_e164, password=None, **extra_fields):
        if not phone_e164:
            raise ValueError("Nomor telepon (E.164) wajib diisi.")

        email = extra_fields.get('email')
        if email:
            extra_fields['email'] = self.normalize_email(email)

        user = self.model(phone_e164=phone_e164, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()

        user.save(using=self._db)
        return user

    def create_superuser(self, phone_e164, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('status', 'ACTIVE')
        if not extra_fields.get('foundation_id'):
            extra_fields['foundation_id'] = 1

        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')

        return self.create_user(phone_e164, password, **extra_fields)

class AllUsersManager(BaseUserManager, AllTenantsManager):
    """Unscoped user manager for global authentication lookup and admin ops."""
    pass
