"""Tenant-aware JWT Authentication for EduCore (spec/01 §2, spec/02 §3)."""
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed, InvalidToken
from rest_framework_simplejwt.settings import api_settings


class EduCoreJWTAuthentication(JWTAuthentication):
    """JWT Authentication querying User.all_tenants to bypass fail-closed tenant scoping
    before request foundation context is established by TenancyMiddleware.
    """

    def get_user(self, validated_token):
        try:
            user_id = validated_token[api_settings.USER_ID_CLAIM]
        except KeyError:
            raise InvalidToken(_("Token contained no recognizable user identification"))

        user_model = get_user_model()
        manager = getattr(user_model, 'all_tenants', user_model.objects)
        try:
            user = manager.get(**{api_settings.USER_ID_FIELD: user_id})
        except user_model.DoesNotExist:
            raise AuthenticationFailed(_("User not found"), code="user_not_found")

        if not user.is_active:
            raise AuthenticationFailed(_("User is inactive"), code="user_inactive")

        return user
