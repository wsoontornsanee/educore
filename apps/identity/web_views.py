"""Web session-authentication views for EduCore (spec/14 §6, TASK-036, spec/17).

Provides session-based login, SSO provider exchange (Google Workspace / Microsoft 365),
and logout for the HTMX/HTML web console surfaces.
"""
import json
import logging
from django.contrib.auth import authenticate, login, logout
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views import View

from educore.middleware.tenancy import set_current_foundation_id
from .landing import resolve_post_login_redirect
from .models import RoleAssignment
from .rbac import has_permission, SCOPE_SCHOOL
from .social_auth import (
    AccountNotLinkedError,
    SocialAuthError,
    TokenVerificationError,
    social_login,
)

logger = logging.getLogger(__name__)

DEFAULT_REDIRECT_URL = '/web/academic/permission-slips/'
# Fallback for roles with no dedicated web console page yet (e.g. Finance
# Officer, Canteen Operator, Clinic Officer) — DEFAULT_REDIRECT_URL requires
# grades.read, which those roles don't hold, so sending them there is a 403
# dead end instead of a landing page.
NO_CONSOLE_PAGE_URL = '/web/home/'


def _default_landing_url(user, foundation_id):
    """Best default landing page for a just-logged-in user.

    Only one web console page exists today (the permission-slip console,
    gated on grades.read); everyone else gets a minimal holding page rather
    than hitting that page's 403. Mirrors HasRequiredPermission's own
    foundation-then-any-assigned-school fallback (apps/identity/permissions.py)
    since a login has no request-scoped school_id to check against.
    """
    if not foundation_id:
        return NO_CONSOLE_PAGE_URL

    if has_permission(user, 'grades.read', foundation_id, school_id=None):
        return DEFAULT_REDIRECT_URL

    assigned_schools = RoleAssignment.all_tenants.filter(
        foundation_id=foundation_id,
        user=user,
        scope_type=SCOPE_SCHOOL,
        deleted_at__isnull=True,
    ).values_list('scope_id', flat=True)
    for school_id in assigned_schools:
        if has_permission(user, 'grades.read', foundation_id, school_id=school_id):
            return DEFAULT_REDIRECT_URL

    return NO_CONSOLE_PAGE_URL


class WebLoginView(View):
    """GET /web/login/ — display login page with credentials form and SSO buttons.
    POST /web/login/ — process standard username/phone/email and password credentials.
    """

    def get(self, request):
        if request.user.is_authenticated:
            next_url = request.GET.get('next') or resolve_post_login_redirect(
                request.user, getattr(request.user, 'foundation_id', None),
            )
            return redirect(next_url)

        next_url = request.GET.get('next', '')
        return self._render_login(request, {
            'next': next_url,
            'error_message': None,
            **self._sso_context(),
        })

    def _render_login(self, request, context, status=200):
        # SecurityMiddleware defaults Cross-Origin-Opener-Policy to same-origin,
        # which severs window.opener on the Google Sign-In popup this page opens —
        # it can never postMessage the credential back, so the popup hangs blank
        # forever at accounts.google.com/gsi/transform. Relax it on this page only.
        response = render(request, 'pages/login.html', context, status=status)
        response['Cross-Origin-Opener-Policy'] = 'same-origin-allow-popups'
        return response

    def _sso_context(self):
        from django.conf import settings
        return {
            'sso_foundation_id': self._resolve_pre_auth_foundation_id(),
            'google_client_id': settings.SOCIAL_AUTH_GOOGLE_CLIENT_ID,
        }

    def _resolve_pre_auth_foundation_id(self):
        """Best-effort foundation for the SSO button before the user is known.

        There is no per-foundation subdomain/slug in this single-host deployment,
        so a pre-auth SSO click can only pass a foundation_id when the whole
        install currently serves exactly one active Foundation (true today —
        the P0 pilot only seeds one). A genuine multi-foundation SaaS rollout
        needs a real tenant-selection step before this button is usable and is
        out of scope here.
        """
        from apps.identity.models import Foundation

        active = Foundation.objects.filter(status=Foundation.STATUS_ACTIVE)[:2]
        if len(active) == 1:
            return active[0].id
        return None

    def post(self, request):
        identifier = request.POST.get('identifier', '').strip()
        password = request.POST.get('password', '')
        next_url = request.POST.get('next') or DEFAULT_REDIRECT_URL

        if not identifier or not password:
            return self._render_login(request, {
                'next': next_url,
                'identifier': identifier,
                'error_message': _("Nomor HP / Email dan kata sandi wajib diisi."),
                **self._sso_context(),
            }, status=400)

        user = authenticate(request, username=identifier, password=password)
        if user is None:
            from apps.identity.models import User
            from django.db.models import Q
            try:
                found_user = User.all_tenants.get(Q(phone_e164=identifier) | Q(email__iexact=identifier))
                if not found_user.is_active and found_user.check_password(password):
                    return self._render_login(request, {
                        'next': next_url,
                        'identifier': identifier,
                        'error_message': _("Akun pengguna tidak aktif. Hubungi Tata Usaha."),
                        **self._sso_context(),
                    }, status=403)
            except (User.DoesNotExist, User.MultipleObjectsReturned):
                pass

            return self._render_login(request, {
                'next': next_url,
                'identifier': identifier,
                'error_message': _("Kredensial tidak valid atau akun terkunci. Periksa kembali data Anda."),
                **self._sso_context(),
            }, status=401)

        if not user.is_active:
            return self._render_login(request, {
                'next': next_url,
                'identifier': identifier,
                'error_message': _("Akun pengguna tidak aktif. Hubungi Tata Usaha."),
                **self._sso_context(),
            }, status=403)

        # Establish Django session
        login(request, user, backend='apps.identity.backends.DualAuthBackend')
        if getattr(user, 'foundation_id', None):
            set_current_foundation_id(user.foundation_id)

        if not request.POST.get('next'):
            next_url = resolve_post_login_redirect(user, user.foundation_id)

        return redirect(next_url)


class WebSSOLoginView(View):
    """POST /web/auth/sso/login/ — verify Google or Microsoft ID token and log into session."""

    def post(self, request):
        try:
            if request.content_type == 'application/json':
                body = json.loads(request.body.decode('utf-8'))
            else:
                body = request.POST
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JsonResponse({'error': _("Format data tidak valid."), 'code': 'INVALID_REQUEST'}, status=400)

        provider = body.get('provider')
        id_token = body.get('id_token')
        requested_next = body.get('next') or request.GET.get('next')

        if not provider or not id_token:
            return JsonResponse({
                'error': _("Provider dan id_token wajib diisi."),
                'code': 'INVALID_PAYLOAD',
            }, status=400)

        foundation_id = request.headers.get('X-Foundation-ID') or body.get('foundation_id')
        if foundation_id:
            try:
                set_current_foundation_id(int(foundation_id))
            except (ValueError, TypeError):
                pass

        try:
            user, claims = social_login(provider, id_token)
        except AccountNotLinkedError as exc:
            return JsonResponse({
                'error': str(exc),
                'code': 'ACCOUNT_NOT_LINKED',
                'detail': _("Akun Google/Microsoft belum terhubung dengan akun staf EduCore."),
            }, status=404)
        except (TokenVerificationError, SocialAuthError) as exc:
            return JsonResponse({
                'error': str(exc),
                'code': 'TOKEN_INVALID',
            }, status=400)

        if not user.is_active:
            return JsonResponse({
                'error': _("Akun pengguna tidak aktif."),
                'code': 'ACCOUNT_INACTIVE',
            }, status=403)

        # Log into session
        login(request, user, backend='apps.identity.backends.DualAuthBackend')
        if getattr(user, 'foundation_id', None):
            set_current_foundation_id(user.foundation_id)

        return JsonResponse({
            'success': True,
            'redirect_url': requested_next or resolve_post_login_redirect(user, user.foundation_id),
            'user': {
                'id': user.id,
                'full_name': user.full_name,
                'email': user.email,
            },
        }, status=200)


class WebConsoleHomeView(View):
    """GET /web/home/ — default landing for roles with no dedicated web console page yet.

    Only the permission-slip console (grades.read) exists as a real page today
    (see backlog: "Web Console: Build Global Navigation Menu & Role-Aware
    Post-Login Landing"). Roles without that permission — Finance Officer,
    Canteen Operator, Clinic Officer — land here instead of hitting a 403.
    """

    def get(self, request):
        if not request.user.is_authenticated:
            return redirect(f"/web/login/?next={request.path}")
        return render(request, 'pages/console_home.html', {})


class WebLogoutView(View):
    """GET/POST /web/auth/logout/ — clear session and redirect to login."""

    def get(self, request):
        logout(request)
        return redirect('/web/login/')

    def post(self, request):
        logout(request)
        return redirect('/web/login/')


class FoundationMicrosoftTenantSettingsView(View):
    """Foundation-admin settings surface for Microsoft Entra tenant pinning (spec/17, PR #117).
    
    Mounted at /web/auth/sso/microsoft-tenant/ and /web/foundation/settings/microsoft-tenant/.
    Restricted to authenticated Foundation Admins. Renders the full page shell or the HTMX
    fragment for in-place updates.
    """

    def _resolve_foundation_id(self, request):
        from educore.middleware.tenancy import get_current_foundation_id
        return get_current_foundation_id() or getattr(request.user, 'foundation_id', None)

    def _is_htmx(self, request):
        return bool(getattr(request, 'htmx', False) or request.headers.get('HX-Request') == 'true')

    def _build_context(self, request, foundation_id, form_error=None, success_message=None, input_value=None):
        from django.conf import settings
        from apps.core.models import AuditEvent
        from apps.identity.models import MicrosoftTenantConfig, User
        from .social_auth import resolve_microsoft_tenant_id

        config = MicrosoftTenantConfig.all_tenants.filter(
            foundation_id=foundation_id,
            deleted_at__isnull=True,
        ).first()

        is_pinned = config is not None
        pinned_tenant_id = config.tenant_id if config else None
        fallback_tenant_id = settings.SOCIAL_AUTH_MICROSOFT_TENANT_ID
        effective_tenant_id = resolve_microsoft_tenant_id(foundation_id)

        audit_events = list(AuditEvent.objects.filter(
            foundation_id=foundation_id,
            action__in=['identity.sso.microsoft_tenant.set', 'identity.sso.microsoft_tenant.removed'],
        ).order_by('-timestamp')[:20])

        actor_ids = [evt.actor_id for evt in audit_events if evt.actor_id and evt.actor_id.isdigit()]
        actor_map = {}
        if actor_ids:
            users = User.all_tenants.filter(id__in=actor_ids)
            for u in users:
                actor_map[str(u.id)] = u.full_name or u.email or u.phone_e164

        for evt in audit_events:
            evt.actor_name = actor_map.get(str(evt.actor_id), evt.actor_id or 'Sistem')

        return {
            'foundation_id': foundation_id,
            'config': config,
            'is_pinned': is_pinned,
            'pinned_tenant_id': pinned_tenant_id,
            'fallback_tenant_id': fallback_tenant_id,
            'effective_tenant_id': effective_tenant_id,
            'audit_events': audit_events,
            'form_error': form_error,
            'success_message': success_message,
            'tenant_id_input': input_value if input_value is not None else (pinned_tenant_id or ''),
        }

    def _render_response(self, request, context, status=200):
        if self._is_htmx(request):
            return render(request, 'components/_microsoft_tenant_settings.html', context, status=status)
        return render(request, 'pages/foundation_microsoft_tenant_page.html', context, status=status)

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"/web/login/?next={request.path}")

        from apps.identity.rbac import is_foundation_admin
        foundation_id = self._resolve_foundation_id(request)
        if not foundation_id or not is_foundation_admin(request.user, foundation_id):
            if self._is_htmx(request):
                return render(request, 'components/_microsoft_tenant_settings.html', {
                    'error': _("Hanya Administrator Yayasan yang berwenang mengakses pengaturan ini."),
                }, status=403)
            return render(request, 'pages/foundation_microsoft_tenant_page.html', {
                'error': _("Hanya Administrator Yayasan yang berwenang mengakses pengaturan ini."),
            }, status=403)

        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        foundation_id = self._resolve_foundation_id(request)
        context = self._build_context(request, foundation_id)
        return self._render_response(request, context, status=200)

    def post(self, request):
        from django.core.exceptions import ValidationError as DjangoValidationError
        from apps.core.services import audit
        from apps.identity.models import MicrosoftTenantConfig, validate_microsoft_tenant_id

        action = request.POST.get('action', '').strip().lower()
        if action in ('delete', 'unpin', 'remove'):
            return self.delete(request)

        foundation_id = self._resolve_foundation_id(request)
        tenant_id = (request.POST.get('tenant_id') or '').strip()

        try:
            validate_microsoft_tenant_id(tenant_id)
        except DjangoValidationError as e:
            error_msg = '; '.join(e.messages)
            ctx = self._build_context(request, foundation_id, form_error=error_msg, input_value=tenant_id)
            return self._render_response(request, ctx, status=400)

        config = MicrosoftTenantConfig.all_tenants.filter(
            foundation_id=foundation_id,
            deleted_at__isnull=True,
        ).first()

        previous_tenant_id = config.tenant_id if config else None
        if config:
            config.tenant_id = tenant_id
            config.save(update_fields=['tenant_id', 'updated_at', 'updated_by'])
        else:
            config = MicrosoftTenantConfig.all_tenants.create(
                foundation_id=foundation_id,
                tenant_id=tenant_id,
                created_by=str(request.user.id),
            )

        audit(
            action="identity.sso.microsoft_tenant.set",
            entity_type="MicrosoftTenantConfig",
            entity_id=str(config.id),
            actor_id=str(request.user.id),
            foundation_id=foundation_id,
            ip_address=request.META.get('REMOTE_ADDR'),
            diff={"tenant_id": {"before": previous_tenant_id, "after": tenant_id}},
        )

        ctx = self._build_context(
            request,
            foundation_id,
            success_message=_("Penyematan tenant Microsoft Entra berhasil disimpan."),
        )
        return self._render_response(request, ctx, status=200)

    def delete(self, request):
        from django.utils import timezone
        from apps.core.services import audit
        from apps.identity.models import MicrosoftTenantConfig

        foundation_id = self._resolve_foundation_id(request)
        deleted = MicrosoftTenantConfig.all_tenants.filter(
            foundation_id=foundation_id,
            deleted_at__isnull=True,
        ).update(deleted_at=timezone.now())

        if deleted:
            audit(
                action="identity.sso.microsoft_tenant.removed",
                entity_type="MicrosoftTenantConfig",
                entity_id=str(foundation_id),
                actor_id=str(request.user.id),
                foundation_id=foundation_id,
                ip_address=request.META.get('REMOTE_ADDR'),
                diff={"tenant_id": {"after": None}},
            )

        ctx = self._build_context(
            request,
            foundation_id,
            success_message=_("Penyematan tenant Microsoft Entra berhasil dilepas. SSO kembali menggunakan mode multi-tenant standar."),
        )
        return self._render_response(request, ctx, status=200)

