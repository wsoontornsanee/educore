"""Operations alerts for printed static QR sheets (spec 18 QRS-041).

A sheet that is photographed and shared shows up as abnormal volume or as charges outside the
merchant's operating hours. The school admin can act (revoke or reprint the sheet), so each
anomaly notifies them once per sheet, reason and day — never per charge — through the standard
notification pipeline, and is audit-logged.
"""
import logging
from datetime import time, timedelta

from django.utils import timezone

from apps.core.services import audit
from apps.identity.recipients import get_school_admin_users
from apps.notifications.models import NotificationCategory, NotificationIntent
from apps.notifications.services import dispatch_intent
from apps.wallet.models import POSEntryMode, POSTransaction, POSTransactionStatus
from educore.middleware.tenancy import tenant_context

logger = logging.getLogger(__name__)

REASON_VOLUME = 'VOLUME'
REASON_OUT_OF_HOURS = 'OUT_OF_HOURS'


def _within_hours(local_time: time, start: time, end: time) -> bool:
    """True when ``local_time`` is inside [start, end]; a window that crosses midnight is handled."""
    if start <= end:
        return start <= local_time <= end
    return local_time >= start or local_time <= end


def _alert_once(decal, merchant, school, reason: str, reason_text: str, local_day, diff: dict) -> int:
    """Notify every school admin once per (sheet, reason, day). Returns how many intents were dispatched."""
    prefix = f"decal_alert:{decal.id}:{reason}:{local_day.isoformat()}"
    if NotificationIntent.objects.filter(
        foundation_id=decal.foundation_id, dedupe_key__startswith=prefix, deleted_at__isnull=True,
    ).exists():
        return 0
    point = decal.payment_point
    sent = 0
    for admin in get_school_admin_users(school):
        dispatch_intent(
            foundation_id=decal.foundation_id,
            category=NotificationCategory.QR_DECAL_ALERT,
            template_key='wallet.decal_alert',
            payload={
                'human_id': decal.human_id, 'payment_point_name': point.name, 'merchant_name': merchant.name,
                'reason_text': reason_text, 'reason': reason,
            },
            school_id=school.id,
            recipient_user=admin,
            # Per recipient: dispatch_intent dedupes on the key alone, so a shared key would reach only one admin.
            dedupe_key=f"{prefix}:{admin.id}",
        )
        sent += 1
    audit(
        action='wallet.decal.alert', entity_type='POSQRDecal', entity_id=decal.id,
        foundation_id=decal.foundation_id, school_id=school.id, role='SYSTEM',
        diff={'reason': reason, 'human_id': decal.human_id, 'recipients': sent, **diff},
    )
    return sent


def check_static_charge_anomalies(pos_tx: POSTransaction) -> list:
    """Run after a completed static charge. Returns the reasons that raised an alert (usually empty)."""
    from apps.attendance.services import get_school_timezone

    decal = pos_tx.qr_decal
    if decal is None or pos_tx.status != POSTransactionStatus.COMPLETED:
        return []
    merchant = decal.payment_point.merchant
    school = merchant.school
    local = pos_tx.occurred_at.astimezone(get_school_timezone(school))
    raised = []

    with tenant_context(decal.foundation_id):
        if merchant.operating_start and merchant.operating_end and not _within_hours(
            local.time(), merchant.operating_start, merchant.operating_end,
        ):
            text = (
                f"menerima pembayaran pukul {local:%H:%M}, di luar jam operasional "
                f"({merchant.operating_start:%H:%M}–{merchant.operating_end:%H:%M})."
            )
            if _alert_once(decal, merchant, school, REASON_OUT_OF_HOURS, text, local.date(), {'at': local.isoformat()}):
                raised.append(REASON_OUT_OF_HOURS)

        day_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
        today = POSTransaction.objects.filter(
            foundation_id=decal.foundation_id, qr_decal=decal, entry_mode=POSEntryMode.SELF_ENTERED,
            status=POSTransactionStatus.COMPLETED, occurred_at__gte=day_start,
            occurred_at__lt=day_start + timedelta(days=1), deleted_at__isnull=True,
        ).count()
        if today > merchant.static_decal_daily_alert:
            text = (
                f"sudah menerima {today} pembayaran hari ini (batas wajar {merchant.static_decal_daily_alert}); "
                "lembar mungkin difoto dan dibagikan."
            )
            if _alert_once(decal, merchant, school, REASON_VOLUME, text, local.date(), {'count': today}):
                raised.append(REASON_VOLUME)
    return raised
