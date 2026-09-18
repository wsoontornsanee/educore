"""Real per-provider health probes for apps.status.check_service_health.

Each probe returns None (no real signal for this environment/deployment —
caller falls back to the shared DB-connectivity heartbeat, exactly like
every ServiceComponent not listed in PROBES) or (status, latency_ms).
"""
import time

import requests
from django.conf import settings

from .models import ServiceComponent


def probe_payments():
    """GET /balance: cheapest authenticated read-only Xendit endpoint —
    confirms API key validity + connectivity + latency without creating or
    mutating anything. Skipped (returns None) when XENDIT_API_KEY isn't
    configured — no single global "active payment provider" setting exists
    (apps.finance.services.payment_providers.get_payment_provider is chosen
    per-call), so an unset key means this deployment isn't actually relying
    on Xendit and the DB-only fallback applies instead.
    """
    api_key = getattr(settings, 'XENDIT_API_KEY', '')
    if not api_key:
        return None
    base_url = getattr(settings, 'XENDIT_BASE_URL', 'https://api.xendit.co')
    start = time.monotonic()
    try:
        response = requests.get(f"{base_url}/balance", auth=(api_key, ''), timeout=5)
        ok = response.ok
    except requests.RequestException:
        ok = False
    latency_ms = int((time.monotonic() - start) * 1000)
    status = ServiceComponent.STATUS_OPERATIONAL if ok else ServiceComponent.STATUS_DOWN
    return status, latency_ms


def probe_whatsapp():
    """GET our own registered WhatsApp Business phone number resource from
    Meta's Graph API — confirms token validity + connectivity. Skipped
    (returns None) unless both WHATSAPP_API_TOKEN and WHATSAPP_PHONE_NUMBER_ID
    are configured, same "unconfigured means not actually in use" contract
    as probe_payments.
    """
    token = getattr(settings, 'WHATSAPP_API_TOKEN', '')
    phone_number_id = getattr(settings, 'WHATSAPP_PHONE_NUMBER_ID', '')
    if not (token and phone_number_id):
        return None
    start = time.monotonic()
    try:
        response = requests.get(
            f"https://graph.facebook.com/v19.0/{phone_number_id}",
            params={'fields': 'id'},
            headers={'Authorization': f'Bearer {token}'},
            timeout=5,
        )
        ok = response.ok
    except requests.RequestException:
        ok = False
    latency_ms = int((time.monotonic() - start) * 1000)
    status = ServiceComponent.STATUS_OPERATIONAL if ok else ServiceComponent.STATUS_DOWN
    return status, latency_ms


def probe_canteen_pos():
    """Derive canteen_pos status from real apps.hardware.Device POS-terminal
    heartbeats, aggregated across every foundation (apps.status is
    platform-wide — see apps/status/models.py module docstring). Device is a
    TenantModel; this cron process has no ambient thread-local
    foundation_id, so it must use .all_tenants with no implicit tenant
    filtering (same cross-tenant precedent as the clinic/library modules).

    Skipped (returns None) when there are zero POS-terminal devices
    registered anywhere yet, falling back to the DB-only signal rather than
    reporting a fabricated status for a component with no real data.
    """
    from apps.hardware.models import Device, DeviceClass, DeviceStatus

    devices = Device.all_tenants.filter(device_class=DeviceClass.POS_TERMINAL).exclude(
        status=DeviceStatus.RETIRED,
    )
    total = devices.count()
    if total == 0:
        return None
    offline = devices.filter(status=DeviceStatus.OFFLINE).count()
    ratio = offline / total
    if ratio > 0.5:
        status = ServiceComponent.STATUS_DOWN
    elif offline > 0:
        status = ServiceComponent.STATUS_DEGRADED
    else:
        status = ServiceComponent.STATUS_OPERATIONAL
    return status, None


PROBES = {
    'payments': probe_payments,
    'notifications': probe_whatsapp,
    'canteen_pos': probe_canteen_pos,
}
