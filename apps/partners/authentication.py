"""HMAC-SHA256 authentication for the partner API surface (spec/18 §2).

Every request carries:
    X-EduCore-Key-Id:    ak_live_7Qp2R9
    X-EduCore-Signature: t=<unix seconds>,v1=<hex hmac>

The signature is HMAC-SHA256(secret, "{timestamp}:{METHOD}:{path_with_query}:{body}")
over the raw request body. The signed path includes the query string, so
filter parameters are tamper-proof. Verification order is deliberate (§9
AC#1): timestamp freshness (±300s, PVA-010) is checked BEFORE any scope or
business logic runs.
"""
import hashlib
import hmac
import ipaddress
import time

from django.conf import settings
from django.utils import timezone
from rest_framework.authentication import BaseAuthentication

from apps.partners.crypto import decrypt_secret
from apps.partners.errors import PartnerAPIError
from apps.partners.models import PartnerApiKey

SIGNATURE_MAX_AGE_SECONDS = 300  # PVA-010


def compute_signature(secret: str, timestamp: str, method: str, path: str, body: bytes) -> str:
    mac = hmac.new(secret.encode(), digestmod=hashlib.sha256)
    mac.update(timestamp.encode())
    mac.update(b':')
    mac.update(method.upper().encode())
    mac.update(b':')
    mac.update(path.encode())
    mac.update(b':')
    mac.update(body or b'')
    return mac.hexdigest()


def _parse_signature_header(value: str):
    """Parse 't=<unix>,v1=<hex>' -> (timestamp_str, hexdigest) or raise."""
    parts = {}
    for chunk in (value or '').split(','):
        chunk = chunk.strip()
        if '=' in chunk:
            k, v = chunk.split('=', 1)
            parts[k.strip()] = v.strip()
    ts, sig = parts.get('t'), parts.get('v1')
    if not ts or not sig:
        raise PartnerAPIError(401, 'SIGNATURE_INVALID', "Signature header must be 't=<unix>,v1=<hex>'.")
    return ts, sig


def _client_ip(request) -> str:
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


def _ip_allowed(key: PartnerApiKey, request) -> bool:
    allowlist = key.ip_allowlist or []
    if not allowlist:
        return True
    ip = ipaddress.ip_address(_client_ip(request))
    for entry in allowlist:
        try:
            network = ipaddress.ip_network(entry, strict=False)
        except ValueError:
            continue
        if ip in network:
            return True
    return False


class PartnerHMACAuthentication(BaseAuthentication):
    """DRF authentication resolving a PartnerApiKey from signed headers.

    Sets `request.auth` to the PartnerApiKey and stamps `request.partner_key`.
    Every failure path raises PartnerAPIError, rendered as problem+json by
    PartnerAPIView.dispatch.
    """

    def authenticate(self, request):
        key_id = request.headers.get('X-EduCore-Key-Id', '')
        signature_header = request.headers.get('X-EduCore-Signature', '')
        if not key_id or not signature_header:
            raise PartnerAPIError(401, 'SIGNATURE_INVALID', 'Missing X-EduCore-Key-Id or X-EduCore-Signature header.')

        timestamp, provided_sig = _parse_signature_header(signature_header)

        # PVA-010: replay-window check FIRST — before DB lookup, scope checks,
        # or any business logic (spec §9 AC#1).
        try:
            ts_val = int(timestamp)
        except ValueError:
            raise PartnerAPIError(401, 'SIGNATURE_INVALID', 'Signature timestamp is not an integer.')
        now = time.time()
        if abs(now - ts_val) > SIGNATURE_MAX_AGE_SECONDS:
            raise PartnerAPIError(401, 'SIGNATURE_INVALID', f'Signature timestamp is older than {SIGNATURE_MAX_AGE_SECONDS}s.')

        # all_tenants: the key's foundation becomes the request's tenant
        # context (partner requests carry no session user). The manager itself
        # is what guarantees a cross-foundation key can never be resolved by
        # guessing — key_id is globally unique.
        key = PartnerApiKey.all_tenants.filter(key_id=key_id).first()
        if key is None:
            raise PartnerAPIError(401, 'SIGNATURE_INVALID', 'Unknown key id.')

        if key.is_deleted:
            raise PartnerAPIError(401, 'SIGNATURE_INVALID', 'Key has been revoked.')
        if key.status != PartnerApiKey.STATUS_ACTIVE:
            raise PartnerAPIError(401, 'SIGNATURE_INVALID', 'Key is not active.')
        if key.expires_at is not None and key.expires_at <= timezone.now():
            raise PartnerAPIError(401, 'SIGNATURE_INVALID', 'Key has expired (30-day rotation window elapsed, PVA-012).')

        secret = decrypt_secret(key.secret_encrypted)
        body = getattr(request, '_body', None)
        if body is None:
            try:
                body = request.body
            except Exception:
                body = b''
        # The signed path INCLUDES the query string (get_full_path): query
        # params change results (?school_id=), so they must be covered by the
        # signature — otherwise a tamperer could rewrite filters under a
        # valid signature for the bare path.
        expected = compute_signature(secret, timestamp, request.method, request.get_full_path(), body or b'')
        if not hmac.compare_digest(expected, provided_sig):
            raise PartnerAPIError(401, 'SIGNATURE_INVALID', 'Signature does not match.')

        if not _ip_allowed(key, request):
            raise PartnerAPIError(403, 'IP_NOT_ALLOWED', 'Client IP is not on this key allow-list.')

        key.last_used_at = timezone.now()
        key.save(update_fields=['last_used_at', 'updated_at'])

        request.partner_key = key
        # Tenant context for the TenantManager on this request.
        from educore.middleware.tenancy import set_current_foundation_id
        set_current_foundation_id(key.foundation_id)

        return (None, key)
