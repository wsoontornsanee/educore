"""Edge device services: staged event ingestion and health monitoring.

Implements the two cron commands declared in `deploy/crontab` (spec/01 §5.1)
and spec/12 §3 (`HW-005`..`HW-013`): `ingest_device_events` and
`check_device_health`.
"""
import datetime
import logging
import zoneinfo

from django.db.models import Q
from django.utils import timezone

from apps.core.services import audit
from apps.hardware.crypto import encrypt_template
from apps.hardware.models import BiometricTemplate, BiometricTemplateStatus, DeviceClass

logger = logging.getLogger(__name__)

# HW-013's "school hours" has no per-school configurable window anywhere in
# this codebase yet (no opening/closing time field on `identity.School`) -
# a fixed default is used as a documented simplification; a real per-school
# configuration is tracked as its own Open Item rather than built here.
OPERATIONAL_HOURS_START = datetime.time(6, 0, 0)
OPERATIONAL_HOURS_END = datetime.time(18, 0, 0)


def ingest_device_events(school_id=None, limit: int = 200) -> dict:
    """Apply pending `DeviceEventStaging` rows to domain tables (HW-005).

    Reuses `attendance.ingest_gate_events` (the exact same idempotent
    dedup/debounce/attendance-derivation logic already exercised by the
    real-time `POST /gate/events/` endpoint) so neither ingestion path can
    drift from the other. Each staged row is applied one at a time so a
    single bad row (unexpected DB error) never blocks the rest of the batch.
    """
    from apps.attendance.services import ingest_gate_events
    from apps.hardware.models import DeviceEventStaging, DeviceEventStagingStatus
    from educore.middleware.tenancy import tenant_context

    checked = applied = failed = 0

    qs = DeviceEventStaging.all_tenants.filter(
        status=DeviceEventStagingStatus.PENDING,
        deleted_at__isnull=True,
    )
    if school_id:
        qs = qs.filter(school_id=school_id)

    rows = list(qs.select_related('device').order_by('created_at', 'id')[:limit])

    for row in rows:
        checked += 1
        try:
            with tenant_context(row.foundation_id):
                ingest_gate_events(
                    foundation_id=row.foundation_id,
                    school_id=row.school_id,
                    events_data=[row.payload],
                )
            row.status = DeviceEventStagingStatus.APPLIED
            row.applied_at = timezone.now()
            row.save(update_fields=['status', 'applied_at', 'updated_at'])
            applied += 1
        except Exception as exc:
            failed += 1
            row.status = DeviceEventStagingStatus.FAILED
            row.error_text = str(exc)[:500]
            row.save(update_fields=['status', 'error_text', 'updated_at'])
            logger.warning("ingest_device_events: staging row %s failed: %s", row.id, exc)

    return {'checked': checked, 'applied': applied, 'failed': failed}


def _is_within_operational_hours(now: datetime.datetime, school) -> bool:
    school_tz_str = getattr(school, 'timezone', None) or 'Asia/Jakarta'
    try:
        school_tz = zoneinfo.ZoneInfo(school_tz_str)
    except Exception:
        school_tz = zoneinfo.ZoneInfo('Asia/Jakarta')

    now_local = now.astimezone(school_tz)
    if now_local.weekday() == 6:  # Sunday (ATT-005's own non-school-day convention)
        return False
    return OPERATIONAL_HOURS_START <= now_local.time() < OPERATIONAL_HOURS_END


def _get_school_admin_recipients(school) -> list:
    """HW-013's "school admin": see apps.identity.recipients.get_school_admin_users."""
    from apps.identity.recipients import get_school_admin_users

    return get_school_admin_users(school)


def check_device_health(timeout_minutes: int = 15) -> dict:
    """Marks devices with a stale heartbeat OFFLINE and alerts the school
    admin when a gate/face terminal goes offline during operational hours
    (spec/12 §3 HW-007, HW-013). Restoring to ONLINE happens on the very next
    heartbeat via `DeviceViewSet.heartbeat` - already true today, no cron
    involvement needed for that half.
    """
    from apps.hardware.models import Device, DeviceClass, DeviceStatus
    from apps.notifications.models import NotificationCategory
    from apps.notifications.services import dispatch_intent
    from educore.middleware.tenancy import tenant_context

    now = timezone.now()
    cutoff = now - datetime.timedelta(minutes=timeout_minutes)

    stale_qs = Device.all_tenants.filter(
        deleted_at__isnull=True,
        status__in=[DeviceStatus.ONLINE, DeviceStatus.DEGRADED],
    ).filter(
        Q(last_heartbeat_at__lt=cutoff) | Q(last_heartbeat_at__isnull=True, created_at__lt=cutoff)
    ).select_related('school')

    checked = marked_offline = alerts_dispatched = 0

    for device in stale_qs:
        checked += 1
        with tenant_context(device.foundation_id):
            device.status = DeviceStatus.OFFLINE
            device.save(update_fields=['status', 'updated_at'])
            audit(
                action='hardware.device.offline',
                entity_type='Device',
                entity_id=device.id,
                foundation_id=device.foundation_id,
                school_id=device.school_id,
                role='SYSTEM_CRON',
                diff={'status': 'OFFLINE', 'last_heartbeat_at': str(device.last_heartbeat_at)},
            )
            marked_offline += 1

            is_critical_gate = device.device_class in (DeviceClass.GATE_READER, DeviceClass.FACE_TERMINAL)
            if is_critical_gate and _is_within_operational_hours(now, device.school):
                for recipient in _get_school_admin_recipients(device.school):
                    dispatch_intent(
                        foundation_id=device.foundation_id,
                        category=NotificationCategory.DEVICE_OFFLINE,
                        template_key='device.offline',
                        payload={
                            'device_id': str(device.id),
                            'device_name': device.name,
                            'device_class': device.device_class,
                            'school_name': device.school.name,
                            'location': device.location,
                        },
                        school_id=device.school_id,
                        recipient_user=recipient,
                        dedupe_key=f"device_offline:{device.id}:{now.date().isoformat()}",
                    )
                    alerts_dispatched += 1
            elif is_critical_gate:
                # EduCore support channel escalation (HW-013's other half) has
                # no integration anywhere in this codebase yet - logged only,
                # tracked as its own Open Item rather than faked here.
                logger.warning(
                    "check_device_health: critical device %s offline outside operational hours; "
                    "school-admin alert skipped, support-channel escalation not implemented.",
                    device.id,
                )

    return {'checked': checked, 'marked_offline': marked_offline, 'alerts_dispatched': alerts_dispatched}


# --- Biometric enrollment & consent-linked deletion (spec/12 §6, spec/14 CMP-009/010/012) ---

class BiometricConsentRequiredError(ValueError):
    """Raised when enrollment is attempted without an active BIOMETRIC ConsentRecord.

    CMP-009: "Consent is per-purpose, never bundled" — an enrollment call
    MUST reference a specific, currently-active BIOMETRIC-purpose consent
    row; enrolling against a withdrawn or wrong-purpose consent is refused.
    """


def enroll_biometric_template(subject_type: str, subject_id: int, foundation_id: int, raw_template: str,
                               consent, device_class: str = DeviceClass.FACE_TERMINAL) -> BiometricTemplate:
    """Encrypt and store a face-recognition template (HW-021/024, CMP-003).

    `raw_template` is the plaintext template payload from the enrollment
    device/SDK — never persisted as-is (HW-010). `consent` is the
    `apps.compliance.ConsentRecord` this enrollment relies on; the actual
    face-recognition capture/matching pipeline (spec/00 §6 P2 milestone) is
    out of scope here — this is the storage + deletion-path slice only.
    """
    if consent.purpose != 'BIOMETRIC' or not consent.is_active:
        raise BiometricConsentRequiredError(
            f"Consent record #{consent.id} is not an active BIOMETRIC consent for "
            f"{subject_type}#{subject_id}."
        )

    template = BiometricTemplate.objects.create(
        foundation_id=foundation_id,
        subject_type=subject_type,
        subject_id=subject_id,
        device_class=device_class,
        consent_id=consent.id,
        template_ciphertext=encrypt_template(raw_template),
        status=BiometricTemplateStatus.ACTIVE,
        enrolled_at=timezone.now(),
    )

    audit(
        action='hardware.biometric.enroll',
        entity_type='BiometricTemplate',
        entity_id=str(template.id),
        foundation_id=foundation_id,
        diff={'subject_type': subject_type, 'subject_id': subject_id, 'consent_id': consent.id},
    )
    return template


def delete_biometric_template(template: BiometricTemplate, reason_status: str, actor_id: str = None) -> None:
    """Blank the ciphertext and flip status immediately (CMP-010: "within 24
    hours"; done synchronously here so the clock is trivially met).
    `reason_status` is BiometricTemplateStatus.WITHDRAWN or .PURGED."""
    now = timezone.now()
    template.template_ciphertext = ''
    template.status = reason_status
    if reason_status == BiometricTemplateStatus.WITHDRAWN:
        template.withdrawn_at = now
    else:
        template.purged_at = now
    template.save(update_fields=['template_ciphertext', 'status', 'withdrawn_at', 'purged_at', 'updated_at'])

    audit(
        action='hardware.biometric.delete',
        entity_type='BiometricTemplate',
        entity_id=str(template.id),
        actor_id=actor_id,
        foundation_id=template.foundation_id,
        diff={'subject_type': template.subject_type, 'subject_id': template.subject_id, 'reason': reason_status},
    )


def active_biometric_templates_for_subject(subject_type: str, subject_id: int, foundation_id: int):
    """all_tenants is deliberate: callers (compliance's withdrawal service,
    identity's exit hooks) may run outside a thread-local tenant context,
    and the queryset is already pinned to one (foundation, subject)."""
    return BiometricTemplate.all_tenants.filter(
        foundation_id=foundation_id, subject_type=subject_type, subject_id=subject_id,
        status=BiometricTemplateStatus.ACTIVE,
    )
