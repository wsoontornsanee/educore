"""Tenant-aware JWT Authentication for EduCore (spec/01 §2, spec/02 §3)."""
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed, InvalidToken
from rest_framework_simplejwt.settings import api_settings

from apps.identity.activity import record_activity
from educore.middleware.tenancy import get_current_foundation_id, set_current_foundation_id


class EduCoreJWTAuthentication(JWTAuthentication):
    """JWT Authentication querying User.all_tenants to bypass fail-closed tenant scoping
    before request foundation context is established by TenancyMiddleware.
    """

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return result

        user, _token = result
        foundation_id = getattr(user, 'foundation_id', None)

        # Validate X-Foundation-ID header against authenticated user's foundation.
        # If present and non-matching, reject the request — prevents a JWT user
        # from using the header to access a different foundation's context.
        header_fid = request.headers.get('X-Foundation-ID')
        if header_fid and header_fid.isdigit():
            header_foundation = int(header_fid)
            if foundation_id and foundation_id != header_foundation:
                raise AuthenticationFailed(
                    _("X-Foundation-ID header value does not match the authenticated user's foundation."),
                    code='foundation_mismatch',
                )

        # ARC-002: TenancyMiddleware runs before DRF authentication, so a Bearer
        # JWT request reaches it as AnonymousUser and the thread-local foundation
        # context is left unset. Every TenantManager queryset then fails closed and
        # returns nothing (and TenantModel writes have no tenant). Establish the
        # context here — the first point at which the token's user is known.
        # TenancyMiddleware still clears the thread-local when the response is done.
        if foundation_id and get_current_foundation_id() is None:
            set_current_foundation_id(foundation_id)
            request.foundation_id = foundation_id
            underlying = getattr(request, '_request', None)
            if underlying is not None:
                underlying.foundation_id = foundation_id

        record_activity(user)

        return result

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
