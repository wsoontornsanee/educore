"""Administrasi console: Mitra & kunci API, mounted under /web/admin/partners/.

Session-auth HTML surface over the same services as the JSON
partner-admin API (apps.partners.admin_views): issue, rotate and revoke
partner API keys. Gated by is_foundation_admin, exactly like that API —
which is why the nav item declares requires_foundation_admin instead of a
permission key. The plaintext secret exists only in the response to the
POST that created it (never a redirect, session, or flash message).
"""
import ipaddress

from django.contrib import messages
from django.shortcuts import redirect
from django.utils.translation import gettext as _
from django.views import View
from django.views.generic import TemplateView

from apps.identity.console_access import ConsolePermissionMixin
from apps.identity.models import School
from apps.partners import services
from apps.partners.models import PartnerApiKey


class _PartnerConsoleMixin(ConsolePermissionMixin):
    foundation_admin_only = True

    def _keys(self):
        return PartnerApiKey.all_tenants.filter(
            foundation_id=self.foundation_id, deleted_at__isnull=True,
        ).order_by('-id')

    def _schools(self):
        return list(School.all_tenants.filter(
            foundation_id=self.foundation_id, deleted_at__isnull=True,
        ).order_by('name'))

    def get_context_data(self, **kwargs):
        keys = list(self._keys())
        for key in keys:
            key.can_rotate = key.is_active and key.expires_at is None
            key.can_revoke = key.status == PartnerApiKey.STATUS_ACTIVE
        return super().get_context_data(
            keys=keys,
            schools=self._schools(),
            allowed_scopes=PartnerApiKey.ALLOWED_SCOPES,
            max_active_keys=services.MAX_ACTIVE_KEYS_PER_FOUNDATION,
            **kwargs,
        )


class PartnerKeysView(_PartnerConsoleMixin, TemplateView):
    template_name = 'pages/admin_partners.html'

    def post(self, request, *args, **kwargs):
        """Issue a new key; on success the secret is rendered once, directly."""
        label = request.POST.get('label', '').strip()
        scopes = request.POST.getlist('scopes')
        schools = self._schools()
        valid_school_ids = {school.id for school in schools}
        school_ids = [int(v) for v in request.POST.getlist('school_ids') if v.isdigit()]
        raw_ips = request.POST.get('ip_allowlist', '')
        ip_allowlist = [part.strip() for part in raw_ips.replace(',', '\n').splitlines() if part.strip()]

        error = None
        if not label:
            error = _("Nama mitra wajib diisi.")
        elif not set(school_ids) <= valid_school_ids:
            error = _("Sekolah yang dipilih tidak valid.")
        else:
            try:
                for entry in ip_allowlist:
                    ipaddress.ip_network(entry, strict=False)
            except ValueError:
                error = _("Daftar IP tidak valid. Gunakan alamat IP atau CIDR, satu per baris.")

        secret = key = None
        if error is None:
            try:
                key, secret = services.issue_api_key_audited(
                    foundation_id=self.foundation_id, label=label, scopes=scopes,
                    school_ids=school_ids, ip_allowlist=ip_allowlist, created_by=str(request.user.pk),
                )
            except services.KeyLimitExceeded:
                error = _("Batas kunci aktif tercapai. Cabut atau putar kunci yang ada terlebih dahulu.")
            except ValueError as exc:
                error = str(exc)

        context = self.get_context_data(
            form_error=error,
            new_secret=secret,
            new_key_id=key.key_id if key else None,
            form_values={'label': label, 'scopes': scopes, 'school_ids': school_ids, 'ip_allowlist': raw_ips} if error else None,
        )
        return self.render_to_response(context, status=400 if error else 200)


class _KeyActionView(_PartnerConsoleMixin, View):
    http_method_names = ['post']

    def _get_key(self, key_id):
        return PartnerApiKey.all_tenants.filter(
            foundation_id=self.foundation_id, key_id=key_id, deleted_at__isnull=True,
        ).first()


class PartnerKeyRotateView(_KeyActionView, TemplateView):
    """Rotate: issue a successor and start the 23d/30d countdown on the old key.
    Renders the listing page directly so the new secret is shown exactly once."""
    template_name = 'pages/admin_partners.html'

    def post(self, request, key_id):
        key = self._get_key(key_id)
        if key is None or not (key.is_active and key.expires_at is None):
            messages.error(request, _("Kunci ini tidak dapat diputar."))
            return redirect('admin-partners')
        try:
            new_key, secret = services.rotate_api_key(key, created_by=str(request.user.pk))
        except services.KeyLimitExceeded:
            messages.error(request, _("Batas kunci aktif tercapai. Cabut salah satu kunci terlebih dahulu."))
            return redirect('admin-partners')
        context = self.get_context_data(new_secret=secret, new_key_id=new_key.key_id)
        return self.render_to_response(context)


class PartnerKeyRevokeView(_KeyActionView):
    def post(self, request, key_id):
        key = self._get_key(key_id)
        if key is None or key.status != PartnerApiKey.STATUS_ACTIVE:
            messages.error(request, _("Kunci ini tidak dapat dicabut."))
        else:
            services.revoke_api_key(key, revoked_by=str(request.user.pk))
            messages.success(request, _("Kunci %(key_id)s dicabut.") % {'key_id': key.key_id})
        return redirect('admin-partners')
