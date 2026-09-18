import datetime
import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import AuditEvent, DomainEvent
from apps.core.services import audit, enqueue_task, record_domain_event
from apps.identity.models import GuardianLink, School, Student, User
from apps.notifications.models import (
    CATEGORY_CONFIG,
    ChannelType,
    DeliveryStatus,
    IntentStatus,
    NotificationCategory,
    NotificationDelivery,
    NotificationIntent,
    NotificationPreference,
    NotificationPriority,
    NotificationTemplate,
    TemplateApprovalStatus,
)
from apps.notifications.providers import CircuitBreakerOpenException, get_provider
from educore.middleware.tenancy import get_current_foundation_id, tenant_context

logger = logging.getLogger(__name__)

# Fallback cascade ladder per NTF-001, NTF-006
FALLBACK_CHANNEL_LADDER = [
    ChannelType.WHATSAPP,
    ChannelType.PUSH,
    ChannelType.SMS,
    ChannelType.EMAIL,
]


def resolve_recipient_channels(
    user: Optional[User],
    category: str,
    foundation_id: int,
) -> List[str]:
    """Resolve ordered channels based on user preferences and category defaults (NTF-001)."""
    if user:
        pref = NotificationPreference.objects.filter(
            foundation_id=foundation_id,
            user=user,
            category=category,
            deleted_at__isnull=True,
        ).first()
        if pref and pref.channels:
            return pref.channels

    cat_config = CATEGORY_CONFIG.get(category, {})
    return cat_config.get('default_channels', [ChannelType.WHATSAPP, ChannelType.PUSH])


def is_in_quiet_hours(
    now_time: datetime.time,
    start_time: datetime.time,
    end_time: datetime.time,
) -> bool:
    """Check whether a given time falls within quiet hours (e.g. 21:00 - 06:00)."""
    if start_time < end_time:
        return start_time <= now_time < end_time
    else:
        # Crosses midnight (e.g. 21:00 to 06:00)
        return now_time >= start_time or now_time < end_time


def calculate_next_quiet_hours_end(
    dt: datetime.datetime,
    end_time: datetime.time,
) -> datetime.datetime:
    """Calculate the next timestamp when quiet hours end."""
    candidate = dt.replace(hour=end_time.hour, minute=end_time.minute, second=0, microsecond=0)
    if candidate <= dt:
        candidate += datetime.timedelta(days=1)
    return candidate


@transaction.atomic
def dispatch_intent(
    foundation_id: int,
    category: str,
    template_key: str,
    payload: Dict[str, Any],
    school_id: Optional[int] = None,
    recipient_user: Optional[User] = None,
    recipient_phone: str = '',
    recipient_email: str = '',
    recipient_name: str = '',
    priority: Optional[str] = None,
    scheduled_for: Optional[datetime.datetime] = None,
    dedupe_key: Optional[str] = None,
    condition_validator: Optional[str] = None,
    immediate: bool = False,
) -> NotificationIntent:
    """
    Publish an outbound notification intent (spec/13 §2).
    
    Invariants:
    - NTF-003: Deduplication via dedupe_key prevents multiple identical notifications.
    - NTF-012: Maximum 20 notifications per recipient per day (excluding EMERGENCY).
    - NTF-013: Opt-out respected per category unless EMERGENCY or statutory notice.
    """
    if not scheduled_for:
        scheduled_for = timezone.now()

    recipient_phone = recipient_phone or ''
    recipient_email = recipient_email or ''
    recipient_name = recipient_name or ''

    cat_config = CATEGORY_CONFIG.get(category, {})
    if not priority:
        priority = cat_config.get('priority', NotificationPriority.NORMAL)

    # 1. Deduplication check (NTF-003)
    if dedupe_key:
        existing_intent = NotificationIntent.objects.filter(
            foundation_id=foundation_id,
            dedupe_key=dedupe_key,
            deleted_at__isnull=True,
        ).first()
        if existing_intent:
            logger.info(f"Notification intent deduplicated by key '{dedupe_key}': id={existing_intent.id}")
            return existing_intent

    # 2. Opt-out check (NTF-013)
    opt_out_allowed = cat_config.get('opt_out_allowed', True)
    if recipient_user and opt_out_allowed:
        pref = NotificationPreference.objects.filter(
            foundation_id=foundation_id,
            user=recipient_user,
            category=category,
            deleted_at__isnull=True,
        ).first()
        if pref and not pref.enabled:
            logger.info(f"Recipient {recipient_user.id} opted out of category {category}.")
            intent = NotificationIntent.objects.create(
                foundation_id=foundation_id,
                school_id=school_id,
                recipient_user=recipient_user,
                recipient_phone=recipient_phone,
                recipient_email=recipient_email,
                recipient_name=recipient_name,
                category=category,
                template_key=template_key,
                payload=payload,
                priority=priority,
                scheduled_for=scheduled_for,
                status=IntentStatus.CANCELLED,
                cancellation_reason=_("Pengguna memilih tidak menerima notifikasi kategori ini (Opt-out)"),
                dedupe_key=dedupe_key,
            )
            return intent

    # 3. Daily Rate Limit Check (NTF-012: max 20 per day, except EMERGENCY and
    # WALLET_RECONCILIATION, which is a per-incident debt notice that cannot be
    # starved by a noisy announcement day (spec/17 §2).
    if category not in (NotificationCategory.EMERGENCY, NotificationCategory.WALLET_RECONCILIATION):
        today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
        phone_filter = {'recipient_phone': recipient_phone} if recipient_phone else {}
        user_filter = {'recipient_user': recipient_user} if recipient_user else {}
        
        if phone_filter or user_filter:
            recent_count = NotificationIntent.objects.filter(
                foundation_id=foundation_id,
                created_at__gte=today_start,
                status__in=[IntentStatus.DISPATCHED, IntentStatus.PENDING, IntentStatus.PROCESSING],
                deleted_at__isnull=True,
                **phone_filter if phone_filter else user_filter
            ).exclude(category=NotificationCategory.EMERGENCY).count()

            if recent_count >= 20:
                logger.warning(f"Rate limit exceeded (>=20) for recipient {recipient_phone or recipient_user}. Skipping/collapsing to digest.")
                intent = NotificationIntent.objects.create(
                    foundation_id=foundation_id,
                    school_id=school_id,
                    recipient_user=recipient_user,
                    recipient_phone=recipient_phone,
                    recipient_email=recipient_email,
                    recipient_name=recipient_name,
                    category=category,
                    template_key=template_key,
                    payload=payload,
                    priority=priority,
                    scheduled_for=scheduled_for,
                    status=IntentStatus.CANCELLED,
                    cancellation_reason=_("Batas harian tercapai (maksimal 20 pesan per hari per NTF-012)"),
                    dedupe_key=dedupe_key,
                )
                return intent

    intent = NotificationIntent.objects.create(
        foundation_id=foundation_id,
        school_id=school_id,
        recipient_user=recipient_user,
        recipient_phone=recipient_phone,
        recipient_email=recipient_email,
        recipient_name=recipient_name,
        category=category,
        template_key=template_key,
        payload=payload,
        priority=priority,
        scheduled_for=scheduled_for,
        status=IntentStatus.PENDING,
        dedupe_key=dedupe_key,
    )

    # 4. Enqueue or immediate dispatch
    # Emergency and High priority intents are picked up immediately by TaskQueue (NTF-014b)
    if immediate or priority in [NotificationPriority.CRITICAL, NotificationPriority.HIGH]:
        enqueue_task(
            task_type='notifications.process_intent',
            payload={'intent_id': intent.id},
            foundation_id=foundation_id,
        )
        if immediate:
            process_intent(intent.id)
            intent.refresh_from_db()

    return intent


def render_template_message(
    template_key: str,
    channel: str,
    foundation_id: int,
    payload: Dict[str, Any],
    locale: str = 'id-ID',
) -> Dict[str, str]:
    """Render notification subject and body using template with variable substitution (NTF-005, NTF-008)."""
    # 1. Try specific channel
    template = NotificationTemplate.objects.filter(
        foundation_id=foundation_id,
        key=template_key,
        channel=channel,
        locale=locale,
        is_active=True,
        deleted_at__isnull=True,
    ).order_by('-version').first()

    # 2. Fallback to WhatsApp template or any channel template
    if not template:
        template = NotificationTemplate.objects.filter(
            foundation_id=foundation_id,
            key=template_key,
            locale=locale,
            is_active=True,
            deleted_at__isnull=True,
        ).order_by('-version').first()

    if not template:
        # Built-in sensible fallbacks if no template seeded yet
        if template_key == 'attendance.arrival':
            body_fmt = "Ananda {student_name} telah tiba di sekolah ({school_name}) melalui {gate_name} pada pukul {time} WIB."
            subj_fmt = "Pemberitahuan Kehadiran Siswa"
        elif template_key == 'attendance.departure':
            body_fmt = "Ananda {student_name} telah keluar dari sekolah ({school_name}) melalui {gate_name} pada pukul {time} WIB."
            subj_fmt = "Pemberitahuan Kepulangan Siswa"
        elif template_key == 'emergency.alert':
            body_fmt = "PERINGATAN DARURAT: {message}. Hubungi pihak sekolah segera."
            subj_fmt = "PERINGATAN DARURAT SEKOLAH"
        elif template_key == 'finance.refund_executed':
            body_fmt = "Pengembalian dana sebesar {currency} {amount} untuk pembayaran {payment_reference} telah berhasil diproses (Ref: {payout_reference})."
            subj_fmt = "Pengembalian Dana Berhasil"
        elif template_key == 'academic.permission_slip.new':
            body_fmt = "Permintaan izin baru: {message}"
            subj_fmt = "Permintaan Izin Baru"
        elif template_key == 'attendance.absent':
            body_fmt = "Pemberitahuan: Ananda {student_name} belum tercatat hadir di {school_name} hingga batas waktu {cutoff_time} WIB pada {date}. Mohon konfirmasi kehadiran siswa."
            subj_fmt = "Pemberitahuan Ketidakhadiran Siswa"
        elif template_key == 'device.offline':
            body_fmt = "Perangkat {device_name} di {school_name} ({location}) terdeteksi offline. Mohon segera diperiksa."
            subj_fmt = "Peringatan: Perangkat Offline"
        else:
            body_fmt = "Pemberitahuan sekolah: {message}"
            subj_fmt = "Pemberitahuan EduCore"
    else:
        body_fmt = template.body
        subj_fmt = template.subject or "Pemberitahuan EduCore"

    # NTF-009: SMS should not expose sensitive information; direct to app
    if channel == ChannelType.SMS and len(body_fmt) > 160:
        student_name = payload.get('student_name', '')
        school_name = payload.get('school_name', '')
        body_fmt = f"Info EduCore: Terdapat pembaruan kehadiran untuk {student_name} di {school_name}. Silakan buka aplikasi EduCore."

    # Safe placeholder substitution
    class SafeDict(dict):
        def __missing__(self, key):
            return f"{{{key}}}"

    rendered_body = body_fmt.format_map(SafeDict(payload))
    rendered_subject = subj_fmt.format_map(SafeDict(payload))

    return {
        'subject': rendered_subject,
        'body': rendered_body,
    }


def process_intent(intent_id: int) -> bool:
    """
    Process a single NotificationIntent: resolve preferences, render, and dispatch (spec/13 §2).
    """
    try:
        intent = NotificationIntent.objects.get(id=intent_id)
    except NotificationIntent.DoesNotExist:
        logger.warning(f"NotificationIntent #{intent_id} does not exist.")
        return False

    if intent.status not in [IntentStatus.PENDING, IntentStatus.FAILED]:
        return False

    with tenant_context(intent.foundation_id):
        now = timezone.now()

        # 1. Quiet Hours check (NTF-002)
        cat_config = CATEGORY_CONFIG.get(intent.category, {})
        respects_quiet_hours = cat_config.get('quiet_hours_respected', True)

        if respects_quiet_hours and intent.category != NotificationCategory.EMERGENCY:
            # Check user preference or defaults (21:00 to 06:00)
            q_start = datetime.time(21, 0)
            q_end = datetime.time(6, 0)
            if intent.recipient_user:
                pref = NotificationPreference.objects.filter(
                    foundation_id=intent.foundation_id,
                    user=intent.recipient_user,
                    category=intent.category,
                    deleted_at__isnull=True,
                ).first()
                if pref:
                    q_start = pref.quiet_hours_start
                    q_end = pref.quiet_hours_end

            now_local = timezone.localtime(now)
            now_local_time = now_local.time()
            if is_in_quiet_hours(now_local_time, q_start, q_end):
                next_allowed = calculate_next_quiet_hours_end(now_local, q_end)
                logger.info(f"Intent #{intent.id} inside quiet hours ({q_start}-{q_end}). Deferring to {next_allowed}.")
                intent.scheduled_for = next_allowed
                intent.save(update_fields=['scheduled_for', 'updated_at'])
                return False

        # NTF-004: any category MAY declare a 'send_time_validator' (dotted path) in its
        # CATEGORY_CONFIG to re-evaluate its own condition right before send — e.g. REC-008's
        # "don't tell a guardian they owe money they already paid" wallet-reconciliation case.
        # Fails OPEN on a broken/missing validator: a validator bug must never silently
        # swallow a real notification.
        validator_path = cat_config.get('send_time_validator')
        if validator_path:
            try:
                from django.utils.module_loading import import_string
                validator = import_string(validator_path)
                still_needed = validator(intent)
            except Exception as exc:
                logger.warning(f"NTF-004 send_time_validator '{validator_path}' failed for intent #{intent.id}: {exc}")
                still_needed = True

            if not still_needed:
                intent.status = IntentStatus.CANCELLED
                intent.cancellation_reason = cat_config.get(
                    'send_time_cancelled_reason', _("Kondisi notifikasi tidak lagi berlaku saat pengiriman (NTF-004)")
                )
                intent.save(update_fields=['status', 'cancellation_reason', 'updated_at'])
                return False

        intent.status = IntentStatus.PROCESSING
        intent.save(update_fields=['status', 'updated_at'])

        # 2. Determine channel candidate list (NTF-001, NTF-006)
        preferred_channels = resolve_recipient_channels(
            user=intent.recipient_user,
            category=intent.category,
            foundation_id=intent.foundation_id,
        )

        # Build ladder starting with preferred channels, followed by any remaining fallback channels
        channels_to_try = list(preferred_channels)
        for ch in FALLBACK_CHANNEL_LADDER:
            if ch not in channels_to_try:
                channels_to_try.append(ch)

        # REC-019: SMS MUST NOT be a fallback channel for wallet debt notices (NTF-009 — no
        # amounts owed in SMS). If whatsapp and push both fail, the debt surfaces in-app only.
        if intent.category == NotificationCategory.WALLET_RECONCILIATION:
            channels_to_try = [c for c in channels_to_try if c != ChannelType.SMS]

        dispatched_successfully = False

        for channel in channels_to_try:
            # Determine target for channel
            target = ''
            if channel in [ChannelType.WHATSAPP, ChannelType.SMS]:
                target = intent.recipient_phone
            elif channel == ChannelType.EMAIL:
                target = intent.recipient_email
            elif channel in [ChannelType.PUSH, ChannelType.IN_APP]:
                target = str(intent.recipient_user_id) if intent.recipient_user_id else intent.recipient_phone

            if not target:
                continue

            # Render message
            rendered = render_template_message(
                template_key=intent.template_key,
                channel=channel,
                foundation_id=intent.foundation_id,
                payload=intent.payload,
            )

            try:
                provider = get_provider(channel)
                result = provider.send(
                    recipient_target=target,
                    rendered_body=rendered['body'],
                    rendered_subject=rendered['subject'],
                    template_key=intent.template_key,
                    variables=intent.payload,
                )
            except CircuitBreakerOpenException as cbe:
                logger.warning(f"Circuit breaker open for channel {channel}: {cbe}. Falling through to next channel.")
                continue
            except Exception as exc:
                logger.exception(f"Unexpected error calling provider for channel {channel}: {exc}")
                result = None

            if result and result.success:
                delivery = NotificationDelivery.objects.create(
                    foundation_id=intent.foundation_id,
                    intent=intent,
                    channel=channel,
                    provider=provider.name,
                    provider_message_id=result.provider_message_id,
                    recipient_target=target,
                    rendered_subject=rendered['subject'],
                    rendered_body=rendered['body'],
                    status=DeliveryStatus.SENT,
                    cost=result.cost,
                    cost_currency=result.cost_currency,
                    sent_at=now,
                )

                intent.status = IntentStatus.DISPATCHED
                intent.sent_at = now
                intent.save(update_fields=['status', 'sent_at', 'updated_at'])

                audit(
                    action='notifications.intent.dispatched',
                    entity_type='NotificationIntent',
                    entity_id=intent.id,
                    foundation_id=intent.foundation_id,
                    school_id=intent.school_id,
                    diff={
                        'channel': channel,
                        'provider': provider.name,
                        'message_id': result.provider_message_id,
                        'recipient': target,
                    }
                )

                record_domain_event(
                    name='notifications.message.dispatched',
                    foundation_id=intent.foundation_id,
                    payload={
                        'intent_id': str(intent.id),
                        'delivery_id': str(delivery.id),
                        'channel': channel,
                        'provider_message_id': result.provider_message_id,
                    }
                )

                dispatched_successfully = True

                # REC-012/029: the 48h reminder clock measures the guardian's time to act,
                # so it starts only once a notice was actually delivered on some channel.
                if intent.category == NotificationCategory.WALLET_RECONCILIATION:
                    from apps.wallet.services import mark_reconciliation_notice_sent
                    mark_reconciliation_notice_sent(intent)

                break
            else:
                # Log failed delivery attempt and fall through to next channel (NTF-006)
                err_code = result.error_code if result else 'UNKNOWN_ERROR'
                err_msg = result.error_message if result else 'Provider failed without result'
                NotificationDelivery.objects.create(
                    foundation_id=intent.foundation_id,
                    intent=intent,
                    channel=channel,
                    provider=provider.name if provider else 'unknown',
                    recipient_target=target,
                    rendered_subject=rendered['subject'],
                    rendered_body=rendered['body'],
                    status=DeliveryStatus.FAILED,
                    error_code=err_code,
                    error_message=err_msg,
                )
                logger.warning(f"Delivery via {channel} failed ({err_code}: {err_msg}). Falling through to next channel.")

        if not dispatched_successfully:
            intent.status = IntentStatus.FAILED
            intent.save(update_fields=['status', 'updated_at'])
            return False

        return True


def handle_gate_scanned_event(
    event_payload: Dict[str, Any],
    category: str = NotificationCategory.ARRIVAL,
):
    """
    Domain event listener for 'attendance.gate.scanned'.

    Generates parent arrival/departure notifications within 5 seconds
    (spec/05 §4 ATT-006, spec/13 §6, spec/08 PAR-004).
    """
    direction = event_payload.get('direction')
    student_id = event_payload.get('student_id')
    school_id = event_payload.get('school_id')
    occurred_at_str = event_payload.get('occurred_at')
    gate_event_id = event_payload.get('gate_event_id')

    expected_direction = 'IN' if category == NotificationCategory.ARRIVAL else 'OUT'
    if direction != expected_direction or not student_id:
        return

    try:
        student = Student.objects.select_related('school', 'person').get(id=student_id)
    except Student.DoesNotExist:
        logger.warning(f"Student #{student_id} not found for {category.lower()} notification.")
        return

    foundation_id = student.foundation_id

    # Parse scan timestamp
    if occurred_at_str:
        try:
            occurred_at = datetime.datetime.fromisoformat(occurred_at_str)
        except Exception:
            occurred_at = timezone.now()
    else:
        occurred_at = timezone.now()

    occurred_date_str = occurred_at.strftime('%Y-%m-%d')
    time_str = occurred_at.strftime('%H:%M')

    # Query active linked guardians (spec/02 §2, spec/05 §4)
    guardian_links = GuardianLink.objects.filter(
        foundation_id=foundation_id,
        student=student,
        deleted_at__isnull=True,
    ).select_related('guardian__person', 'guardian__user')

    if not guardian_links.exists():
        logger.info(f"Student #{student.id} has no linked guardians. No {category.lower()} notice sent.")
        return

    school_name = student.school.name if student.school else 'Sekolah'
    student_name = student.person.full_name if student.person else 'Siswa'
    gate_name = event_payload.get('gate_name') or 'Gerbang Sekolah'
    template_key = 'attendance.arrival' if category == NotificationCategory.ARRIVAL else 'attendance.departure'

    for link in guardian_links:
        guardian = link.guardian
        user = guardian.user
        phone = (getattr(user, 'phone_e164', None) or getattr(user, 'phone', '')) if user else ''
        email = (getattr(user, 'email', '')) if user else ''
        guardian_name = guardian.person.full_name if guardian.person else 'Wali Murid'

        if not phone and not user:
            continue

        dedupe_key = f"{category.lower()}:{student.id}:{occurred_date_str}:{guardian.id}"

        payload = {
            'type': category,
            'student_id': student.id,
            'student_name': student_name,
            'guardian_name': guardian_name,
            'school_name': school_name,
            'gate_name': gate_name,
            'time': time_str,
            'date': occurred_date_str,
        }

        dispatch_intent(
            foundation_id=foundation_id,
            school_id=school_id,
            recipient_user=user,
            recipient_phone=phone,
            recipient_email=email,
            recipient_name=guardian_name,
            category=category,
            template_key=template_key,
            payload=payload,
            priority=NotificationPriority.HIGH,
            dedupe_key=dedupe_key,
            immediate=True,  # Dispatched in < 5s (ATT-006, NFR-002)
        )
