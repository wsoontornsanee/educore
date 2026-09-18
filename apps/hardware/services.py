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
    """Resolves who counts as "the school admin" for HW-013's offline alert:
    whoever currently holds `school_admin` at that school's scope, or
    `foundation_admin` at the foundation scope - mirrors
    `apps.academic.services._get_principal_staff`'s role-assignment-is-the-
    source-of-truth pattern, generalized to possibly several recipients.
    """
    from apps.identity.models import RoleAssignment, User

    user_ids = set(RoleAssignment.all_tenants.filter(
        foundation_id=school.foundation_id,
        role=RoleAssignment.ROLE_SCHOOL_ADMIN,
        scope_type=RoleAssignment.SCOPE_SCHOOL,
        scope_id=school.id,
        deleted_at__isnull=True,
    ).values_list('user_id', flat=True))
    user_ids |= set(RoleAssignment.all_tenants.filter(
        foundation_id=school.foundation_id,
        role=RoleAssignment.ROLE_FOUNDATION_ADMIN,
        scope_type=RoleAssignment.SCOPE_FOUNDATION,
        deleted_at__isnull=True,
    ).values_list('user_id', flat=True))
    if not user_ids:
        return []
    return list(User.all_tenants.filter(id__in=user_ids, deleted_at__isnull=True))


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
