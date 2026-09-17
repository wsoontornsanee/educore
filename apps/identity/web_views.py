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
from .social_auth import (
    AccountNotLinkedError,
    SocialAuthError,
    TokenVerificationError,
    social_login,
)

logger = logging.getLogger(__name__)

DEFAULT_REDIRECT_URL = '/web/academic/permission-slips/'


class WebLoginView(View):
    """GET /web/login/ — display login page with credentials form and SSO buttons.
    POST /web/login/ — process standard username/phone/email and password credentials.
    """

    def get(self, request):
        if request.user.is_authenticated:
            next_url = request.GET.get('next') or DEFAULT_REDIRECT_URL
            return redirect(next_url)

        next_url = request.GET.get('next', '')
        return render(request, 'pages/login.html', {
            'next': next_url,
            'error_message': None,
        })

    def post(self, request):
        identifier = request.POST.get('identifier', '').strip()
        password = request.POST.get('password', '')
        next_url = request.POST.get('next') or DEFAULT_REDIRECT_URL

        if not identifier or not password:
            return render(request, 'pages/login.html', {
                'next': next_url,
                'identifier': identifier,
                'error_message': _("Nomor HP / Email dan kata sandi wajib diisi."),
            }, status=400)

        user = authenticate(request, username=identifier, password=password)
        if user is None:
            from apps.identity.models import User
            from django.db.models import Q
            try:
                found_user = User.all_tenants.get(Q(phone_e164=identifier) | Q(email__iexact=identifier))
                if not found_user.is_active and found_user.check_password(password):
                    return render(request, 'pages/login.html', {
                        'next': next_url,
                        'identifier': identifier,
                        'error_message': _("Akun pengguna tidak aktif. Hubungi Tata Usaha."),
                    }, status=403)
            except (User.DoesNotExist, User.MultipleObjectsReturned):
                pass

            return render(request, 'pages/login.html', {
                'next': next_url,
                'identifier': identifier,
                'error_message': _("Kredensial tidak valid atau akun terkunci. Periksa kembali data Anda."),
            }, status=401)

        if not user.is_active:
            return render(request, 'pages/login.html', {
                'next': next_url,
                'identifier': identifier,
                'error_message': _("Akun pengguna tidak aktif. Hubungi Tata Usaha."),
            }, status=403)

        # Establish Django session
        login(request, user, backend='apps.identity.backends.DualAuthBackend')
        if getattr(user, 'foundation_id', None):
            set_current_foundation_id(user.foundation_id)

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
        next_url = body.get('next') or request.GET.get('next') or DEFAULT_REDIRECT_URL

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
            'redirect_url': next_url,
            'user': {
                'id': user.id,
                'full_name': user.full_name,
                'email': user.email,
            },
        }, status=200)


class WebLogoutView(View):
    """GET/POST /web/auth/logout/ — clear session and redirect to login."""

    def get(self, request):
        logout(request)
        return redirect('/web/login/')

    def post(self, request):
        logout(request)
        return redirect('/web/login/')
