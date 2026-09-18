import logging
import secrets
from datetime import timedelta
from typing import Any, Dict, Optional

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import AuditEvent, DomainEvent
from apps.core.services import audit, record_domain_event
from apps.attendance.models import (
    AbsenceRequest,
    AbsenceRequestStatus,
    AbsenceType,
    AttendanceDay,
    AttendanceSource,
    AttendanceStatus,
    Credential,
    CredentialStatus,
    CredentialType,
    PeriodAttendance,
    PeriodAttendanceSource,
)
from apps.identity.models import Student, Staff

logger = logging.getLogger(__name__)


class NotAuthorizedForSlotError(ValueError):
    pass


@transaction.atomic
def issue_credential(
    foundation_id: str,
    student: Optional[Student] = None,
    staff: Optional[Staff] = None,
    type: str = CredentialType.RFID,
    uid: Optional[str] = None,
    card_number: str = '',
    expires_in_minutes: int = 15,
    user: Optional[Any] = None,
) -> Credential:
    """
    Issues a new physical card (RFID/NFC) or temporary QR fallback credential (spec/05 §2, spec/12 §5).
    
    Invariants:
    - Exactly one of student or staff must be provided.
    - HW-019: A student/staff can hold at most ONE active physical card (RFID/NFC).
      Issuing a new physical card automatically revokes the prior active card.
    - HW-020: QR fallback credentials are time-limited (<=15 min default) and single-use.
    """
    if not student and not staff:
        raise ValidationError(_("Credential must be assigned to either a student or a staff member."))
    if student and staff:
        raise ValidationError(_("Credential cannot be simultaneously assigned to both student and staff."))

    # Validate foundation scoping
    holder_foundation_id = str(student.foundation_id) if student else str(staff.foundation_id)
    if holder_foundation_id != str(foundation_id):
        raise ValidationError(_("Holder does not belong to the active foundation."))

    school = student.school if student else staff.school

    # Handle QR vs Physical card requirements
    expires_at = None
    is_used = False

    if type == CredentialType.QR:
        if not uid:
            uid = f"QR_{secrets.token_urlsafe(24)}"
        # HW-020: QR credentials must be time-limited (<= 15 minutes)
        capped_minutes = min(expires_in_minutes, 15) if expires_in_minutes > 0 else 15
        expires_at = timezone.now() + timedelta(minutes=capped_minutes)
    else:
        if not uid or not str(uid).strip():
            raise ValidationError(_("Card UID is required for RFID and NFC credentials."))
        uid = str(uid).strip().upper()

        # Check UID uniqueness among currently active credentials within this foundation
        if Credential.objects.filter(foundation_id=foundation_id, uid=uid, status=CredentialStatus.ACTIVE).exists():
            raise ValidationError(_("Card UID '%(uid)s' is already actively assigned to another holder.") % {'uid': uid})

        # HW-019: Auto-revoke any existing active physical card for this holder
        holder_filter = {'student': student} if student else {'staff': staff}
        existing_active_cards = Credential.objects.filter(
            foundation_id=foundation_id,
            type__in=[CredentialType.RFID, CredentialType.NFC],
            status=CredentialStatus.ACTIVE,
            **holder_filter
        )
        for old_card in existing_active_cards:
            revoke_credential(
                credential=old_card,
                reason=str(_("Otomatis dicabut: penggantian kartu fisik baru (HW-019)")),
                user=user
            )

    actor_id = str(user.id) if user and getattr(user, 'id', None) else ''

    credential = Credential.objects.create(
        foundation_id=foundation_id,
        student=student,
        staff=staff,
        type=type,
        uid=uid,
        card_number=card_number,
        status=CredentialStatus.ACTIVE,
        issued_at=timezone.now(),
        expires_at=expires_at,
        is_used=is_used,
        created_by=actor_id,
        updated_by=actor_id,
    )

    # Audit logging
    audit(
        action='attendance.credential.issued',
        entity_type='Credential',
        entity_id=credential.id,
        actor_id=actor_id,
        foundation_id=foundation_id,
        school_id=school.id if school else None,
        diff={
            'uid': credential.uid,
            'type': credential.type,
            'holder_type': credential.holder_type,
            'holder_id': str(student.id) if student else str(staff.id),
            'expires_at': credential.expires_at.isoformat() if credential.expires_at else None,
        }
    )

    # Domain event
    record_domain_event(
        name='attendance.credential.issued',
        foundation_id=foundation_id,
        payload={
            'credential_id': str(credential.id),
            'uid': credential.uid,
            'type': credential.type,
            'school_id': str(school.id) if school else None,
            'student_id': str(student.id) if student else None,
            'staff_id': str(staff.id) if staff else None,
        }
    )

    return credential


@transaction.atomic
def revoke_credential(
    credential: Credential,
    reason: str = '',
    post_replacement_fee: bool = False,
    user: Optional[Any] = None,
) -> Credential:
    """
    Revokes a credential in one action (HW-018).
    Subsequent verifications and offline gate syncs will reject this credential.
    """
    if credential.status == CredentialStatus.REVOKED:
        return credential

    actor_id = str(user.id) if user and getattr(user, 'id', None) else ''

    credential.status = CredentialStatus.REVOKED
    credential.revoked_at = timezone.now()
    credential.revoked_reason = reason or _("Kartu hilang/rusak atau dicabut oleh admin.")
    credential.replacement_fee_posted = post_replacement_fee
    credential.updated_by = actor_id
    credential.save(update_fields=['status', 'revoked_at', 'revoked_reason', 'replacement_fee_posted', 'updated_by', 'updated_at'])

    school = credential.student.school if credential.student else (credential.staff.school if credential.staff else None)

    # Audit logging
    audit(
        action='attendance.credential.revoked',
        entity_type='Credential',
        entity_id=credential.id,
        actor_id=actor_id,
        foundation_id=credential.foundation_id,
        school_id=school.id if school else None,
        diff={
            'uid': credential.uid,
            'type': credential.type,
            'reason': credential.revoked_reason,
            'replacement_fee_posted': post_replacement_fee,
        }
    )

    # Domain event
    record_domain_event(
        name='attendance.credential.revoked',
        foundation_id=credential.foundation_id,
        payload={
            'credential_id': str(credential.id),
            'uid': credential.uid,
            'type': credential.type,
            'school_id': str(school.id) if school else None,
            'student_id': str(credential.student_id) if credential.student_id else None,
            'staff_id': str(credential.staff_id) if credential.staff_id else None,
            'reason': credential.revoked_reason,
        }
    )

    return credential


def verify_credential(
    foundation_id: str,
    uid: str,
    mark_used: bool = False,
) -> Dict[str, Any]:
    """
    Verifies credential status for gates and POS terminals (spec/05 §4, spec/12 §5).
    Checks revocation, expiration, and single-use QR rules.
    """
    clean_uid = str(uid).strip().upper() if uid else ''
    credential = Credential.objects.filter(
        foundation_id=foundation_id,
        uid=clean_uid
    ).first()

    if not credential:
        # Also check exact case for QR code tokens which may be case-sensitive
        credential = Credential.objects.filter(
            foundation_id=foundation_id,
            uid=str(uid).strip()
        ).first()

    if not credential:
        return {
            'valid': False,
            'status': 'NOT_FOUND',
            'reason': _('Kredensial tidak ditemukan / tidak terdaftar.'),
            'credential': None,
        }

    if credential.status == CredentialStatus.REVOKED:
        return {
            'valid': False,
            'status': CredentialStatus.REVOKED,
            'reason': credential.revoked_reason or _('Kredensial telah dicabut (revoked).'),
            'credential': credential,
        }

    # Time-limited & single-use checks for QR fallback
    if credential.type == CredentialType.QR:
        if credential.is_used:
            return {
                'valid': False,
                'status': 'ALREADY_USED',
                'reason': _('Kode QR sekali pakai telah digunakan sebelumnya.'),
                'credential': credential,
            }
        if credential.expires_at and timezone.now() > credential.expires_at:
            if credential.status != CredentialStatus.EXPIRED:
                credential.status = CredentialStatus.EXPIRED
                credential.save(update_fields=['status', 'updated_at'])
            return {
                'valid': False,
                'status': CredentialStatus.EXPIRED,
                'reason': _('Kode QR telah kedaluwarsa (>15 menit).'),
                'credential': credential,
            }
    if credential.status != CredentialStatus.ACTIVE:
        return {
            'valid': False,
            'status': credential.status,
            'reason': _('Kredensial tidak aktif.'),
            'credential': credential,
        }

    if credential.type == CredentialType.QR and mark_used:
        credential.is_used = True
        credential.status = CredentialStatus.EXPIRED
        credential.save(update_fields=['is_used', 'status', 'updated_at'])

    return {
        'valid': True,
        'status': CredentialStatus.ACTIVE,
        'reason': _('Kredensial valid dan aktif.'),
        'credential': credential,
        'holder_type': credential.holder_type,
        'holder_name': credential.holder_name,
        'student': credential.student,
        'staff': credential.staff,
    }


@transaction.atomic
def ingest_gate_events(
    foundation_id: str,
    school_id: str,
    events_data: list,
    user: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Batched, idempotent ingestion of gate scan events from edge gateways (spec/05 §2, §4, spec/12 §3).
    
    Implements:
    - HW-005: Idempotency on client-generated event_uuid.
    - ATT-007: Debouncing of duplicate scans within debounce_seconds without re-notifying.
    - ATT-008: Direction inference per device (with alternating state for BIDIRECTIONAL devices).
    - ATT-009: Rejected scan recording for unknown/revoked credentials.
    - ATT-001: Automatic daily attendance status derivation (HADIR vs TERLAMBAT).
    - ATT-006: Dispatch of attendance.gate.scanned domain event for non-duplicate accepted scans.
    """
    import datetime
    from apps.hardware.models import Device, DeviceDirection
    from apps.attendance.models import (
        AttendanceDay, AttendanceRule, AttendanceSource, AttendanceStatus,
        GateDirection, GateEvent, GateEventStatus, GateMethod
    )
    from apps.identity.models import Student, Staff
    import dateutil.parser

    rule = AttendanceRule.objects.filter(
        foundation_id=foundation_id,
        school_id=school_id,
        deleted_at__isnull=True
    ).first()

    late_after_time = rule.late_after_time if rule else datetime.time(7, 15, 0)
    debounce_seconds = rule.debounce_seconds if rule else 120

    accepted_count = 0
    rejected_count = 0
    debounced_count = 0
    duplicate_uuid_count = 0
    processed_events = []

    actor_id = str(user.id) if user and getattr(user, 'id', None) else ''

    for item in events_data:
        event_uuid = item.get('event_uuid')
        if not event_uuid:
            continue

        # 1. Idempotency check on client-generated event_uuid (HW-005)
        existing = GateEvent.objects.filter(
            foundation_id=foundation_id,
            event_uuid=event_uuid
        ).first()
        if existing:
            duplicate_uuid_count += 1
            processed_events.append(existing)
            continue

        device_id = item.get('device_id')
        try:
            device = Device.objects.get(id=device_id, foundation_id=foundation_id, school_id=school_id)
        except Device.DoesNotExist:
            continue

        # Parse occurred_at
        occurred_at = item.get('occurred_at')
        if isinstance(occurred_at, str):
            occurred_at = dateutil.parser.isoparse(occurred_at)
        if timezone.is_naive(occurred_at):
            occurred_at = timezone.make_aware(occurred_at, timezone.get_current_timezone())

        method = item.get('method', GateMethod.RFID)
        confidence = item.get('confidence')
        photo_key = item.get('photo_key', '')
        replayed = item.get('replayed', False)
        raw_uid = str(item.get('raw_uid', '')).strip()

        credential = None
        student = None
        staff = None
        event_status = GateEventStatus.ACCEPTED
        reject_reason = ''

        # 2. Verify Credential / Resolve Holder
        if raw_uid:
            res = verify_credential(foundation_id=foundation_id, uid=raw_uid)
            if not res['valid']:
                event_status = GateEventStatus.REJECTED
                reject_reason = res.get('reason', _('Kredensial tidak valid'))
                rejected_count += 1
            else:
                credential = res.get('credential')
                student = res.get('student')
                staff = res.get('staff')
        elif item.get('student_id'):
            try:
                student = Student.objects.get(id=item['student_id'], foundation_id=foundation_id, school_id=school_id)
            except Student.DoesNotExist:
                event_status = GateEventStatus.REJECTED
                reject_reason = _('Siswa tidak ditemukan')
                rejected_count += 1
        elif item.get('staff_id'):
            try:
                staff = Staff.objects.get(id=item['staff_id'], foundation_id=foundation_id, school_id=school_id)
            except Staff.DoesNotExist:
                event_status = GateEventStatus.REJECTED
                reject_reason = _('Staf tidak ditemukan')
                rejected_count += 1

        # 3. Direction inference (ATT-008)
        direction = item.get('direction')
        if not direction:
            if device.direction in [GateDirection.IN, GateDirection.OUT]:
                direction = device.direction
            else:
                # Device is BIDIRECTIONAL: alternate from student's last accepted event that date
                last_event = None
                if student:
                    last_event = GateEvent.objects.filter(
                        foundation_id=foundation_id,
                        student=student,
                        occurred_at__date=occurred_at.date(),
                        status=GateEventStatus.ACCEPTED,
                        deleted_at__isnull=True
                    ).order_by('-occurred_at').first()
                elif staff:
                    last_event = GateEvent.objects.filter(
                        foundation_id=foundation_id,
                        staff=staff,
                        occurred_at__date=occurred_at.date(),
                        status=GateEventStatus.ACCEPTED,
                        deleted_at__isnull=True
                    ).order_by('-occurred_at').first()

                if last_event and last_event.direction == GateDirection.IN:
                    direction = GateDirection.OUT
                else:
                    direction = GateDirection.IN

        # 4. Debounce check (ATT-007)
        is_duplicate_scan = False
        if event_status == GateEventStatus.ACCEPTED and (student or staff):
            holder_filter = {'student': student} if student else {'staff': staff}
            debounce_window_start = occurred_at - timedelta(seconds=debounce_seconds)
            recent_scan_exists = GateEvent.objects.filter(
                foundation_id=foundation_id,
                direction=direction,
                status=GateEventStatus.ACCEPTED,
                occurred_at__gte=debounce_window_start,
                occurred_at__lte=occurred_at,
                deleted_at__isnull=True,
                **holder_filter
            ).exists()
            if recent_scan_exists:
                is_duplicate_scan = True
                debounced_count += 1

        gate_event = GateEvent.objects.create(
            foundation_id=foundation_id,
            event_uuid=event_uuid,
            school=device.school,
            device=device,
            student=student,
            staff=staff,
            credential=credential,
            raw_uid=raw_uid,
            direction=direction,
            occurred_at=occurred_at,
            method=method,
            confidence=confidence,
            photo_key=photo_key,
            status=event_status,
            reject_reason=reject_reason,
            is_duplicate_scan=is_duplicate_scan,
            replayed=replayed,
            created_by=actor_id,
            updated_by=actor_id,
        )

        if event_status == GateEventStatus.ACCEPTED:
            accepted_count += 1

            # 5. Trigger notification event only for non-duplicate scans (ATT-006, ATT-007)
            if not is_duplicate_scan:
                event_payload = {
                    'gate_event_id': str(gate_event.id),
                    'event_uuid': str(gate_event.event_uuid),
                    'school_id': str(device.school_id),
                    'device_id': str(device.id),
                    'student_id': str(student.id) if student else None,
                    'staff_id': str(staff.id) if staff else None,
                    'direction': direction,
                    'occurred_at': occurred_at.isoformat(),
                    'method': method,
                    'gate_name': device.name,
                }
                record_domain_event(
                    name='attendance.gate.scanned',
                    foundation_id=foundation_id,
                    payload=event_payload,
                )
                # Dispatch parent arrival/departure notification (ATT-006: < 5s, PAR-004)
                if direction in ('IN', 'OUT') and student:
                    try:
                        from apps.notifications.models import NotificationCategory
                        from apps.notifications.services import handle_gate_scanned_event
                        category = NotificationCategory.ARRIVAL if direction == 'IN' else NotificationCategory.DEPARTURE
                        handle_gate_scanned_event(event_payload, category=category)
                    except Exception as exc:
                        logger.warning(f"Error handling gate scanned event notification: {exc}")

            # 6. Derive/update Daily Attendance summary for students (ATT-001)
            if student:
                update_daily_attendance_from_gate(
                    foundation_id=foundation_id,
                    school_id=school_id,
                    student=student,
                    occurred_at=occurred_at,
                    direction=direction,
                    late_after_time=late_after_time,
                )

        processed_events.append(gate_event)

    return {
        'total': len(events_data),
        'accepted': accepted_count,
        'rejected': rejected_count,
        'debounced': debounced_count,
        'duplicates_skipped': duplicate_uuid_count,
        'events': processed_events,
    }


def update_daily_attendance_from_gate(
    foundation_id: str,
    school_id: str,
    student: Any,
    occurred_at: Any,
    direction: str,
    late_after_time: Any,
) -> Any:
    """
    Updates or creates an AttendanceDay record from an accepted gate scan event (ATT-001).
    """
    from apps.attendance.models import AttendanceDay, AttendanceSource, AttendanceStatus, GateDirection

    event_date = occurred_at.date()
    attendance_day, _ = AttendanceDay.objects.get_or_create(
        foundation_id=foundation_id,
        school_id=school_id,
        student=student,
        date=event_date,
        defaults={
            'status': AttendanceStatus.ALPA,
            'source': AttendanceSource.GATE,
        }
    )

    # If already manually overridden by staff, preserve staff status (ATT-003)
    is_staff_override = attendance_day.is_override

    if direction == GateDirection.IN:
        if not attendance_day.first_in_at or occurred_at < attendance_day.first_in_at:
            attendance_day.first_in_at = occurred_at

        if not is_staff_override:
            # Derive HADIR vs TERLAMBAT per ATT-001
            # Compare time in school local timezone
            scan_time = occurred_at.time()
            if scan_time <= late_after_time:
                attendance_day.status = AttendanceStatus.HADIR
            else:
                attendance_day.status = AttendanceStatus.TERLAMBAT
            attendance_day.source = AttendanceSource.GATE

    elif direction == GateDirection.OUT:
        if not attendance_day.last_out_at or occurred_at > attendance_day.last_out_at:
            attendance_day.last_out_at = occurred_at

    attendance_day.save()
    return attendance_day


@transaction.atomic
def override_attendance_day(
    foundation_id: str,
    attendance_day: Any,
    new_status: str,
    note: str,
    user: Optional[Any] = None,
) -> Any:
    """
    Staff override of a derived daily attendance status (spec/05 §3 ATT-003).
    Requires a non-empty reason note, retains original status, and logs an immutable audit event.
    """
    if not note or not str(note).strip():
        raise ValidationError(_("Catatan alasan wajib diisi saat melakukan override kehadiran staf."))

    actor_id = str(user.id) if user and getattr(user, 'id', None) else ''

    if not attendance_day.is_override:
        attendance_day.original_status = attendance_day.status

    old_status = attendance_day.status
    attendance_day.status = new_status
    attendance_day.is_override = True
    attendance_day.note = str(note).strip()
    attendance_day.updated_by = actor_id
    attendance_day.save(update_fields=['status', 'original_status', 'is_override', 'note', 'updated_by', 'updated_at'])

    audit(
        action='attendance.day.overridden',
        entity_type='AttendanceDay',
        entity_id=attendance_day.id,
        actor_id=actor_id,
        foundation_id=foundation_id,
        school_id=attendance_day.school_id,
        diff={
            'student_id': str(attendance_day.student_id),
            'date': attendance_day.date.isoformat(),
            'old_status': old_status,
            'new_status': new_status,
            'note': attendance_day.note,
        }
    )

    record_domain_event(
        name='attendance.day.overridden',
        foundation_id=foundation_id,
        payload={
            'attendance_day_id': str(attendance_day.id),
            'student_id': str(attendance_day.student_id),
            'school_id': str(attendance_day.school_id),
            'date': attendance_day.date.isoformat(),
            'old_status': old_status,
            'new_status': new_status,
            'note': attendance_day.note,
        }
    )

    return attendance_day


def get_live_gate_feed(
    foundation_id: str,
    school_id: str,
    since: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    """
    Retrieves live chronological gate scan events and device status for wall monitors (spec/05 §4 ATT-013, spec/17 §7.2).
    Uses 3-second cursor polling (ARC-015) without persistent socket dependencies.
    """
    import dateutil.parser
    from apps.attendance.models import AttendanceDay, GateEvent, GateEventStatus
    from apps.hardware.models import Device

    since_dt = None
    if since:
        try:
            since_dt = dateutil.parser.isoparse(str(since).strip())
            if timezone.is_naive(since_dt):
                since_dt = timezone.make_aware(since_dt, timezone.get_current_timezone())
        except (ValueError, TypeError):
            since_dt = None

    qs = GateEvent.objects.filter(
        foundation_id=foundation_id,
        school_id=school_id,
        deleted_at__isnull=True,
    ).select_related(
        'student', 'student__person', 'staff', 'staff__person', 'device', 'credential'
    )

    if since_dt:
        qs = qs.filter(created_at__gt=since_dt).order_by('created_at')
    else:
        today = timezone.now().date()
        qs = qs.filter(occurred_at__date=today).order_by('-created_at')

    raw_events = list(qs[:limit])
    if not since_dt:
        # For initial load, restore chronological order for the wall monitor feed
        raw_events.reverse()

    # Determine next cursor
    now_dt = timezone.now()
    if raw_events:
        max_created = max(e.created_at for e in raw_events)
        next_cursor = max_created.isoformat()
    elif since_dt:
        next_cursor = since_dt.isoformat()
    else:
        next_cursor = now_dt.isoformat()

    # Map today's daily attendance for students to enrich status badge
    today = timezone.now().date()
    student_ids = [e.student_id for e in raw_events if e.student_id]
    att_map = {}
    if student_ids:
        for ad in AttendanceDay.objects.filter(
            foundation_id=foundation_id,
            school_id=school_id,
            student_id__in=student_ids,
            date=today,
            deleted_at__isnull=True
        ):
            att_map[ad.student_id] = ad.status

    feed_events = []
    for e in raw_events:
        feed_events.append({
            'id': e.id,
            'event_uuid': str(e.event_uuid),
            'occurred_at': e.occurred_at.isoformat(),
            'created_at': e.created_at.isoformat(),
            'direction': e.direction,
            'method': e.method,
            'confidence': str(e.confidence) if e.confidence is not None else None,
            'status': e.status,
            'reject_reason': e.reject_reason,
            'is_duplicate_scan': e.is_duplicate_scan,
            'replayed': e.replayed,
            'device': {
                'id': e.device_id,
                'name': e.device.name if e.device else '',
                'code': e.device.device_code if e.device else '',
            } if e.device else None,
            'student': {
                'id': e.student_id,
                'nis': e.student.nis if e.student else '',
                'nisn': e.student.nisn if e.student else '',
                'full_name': e.student.person.full_name if e.student and e.student.person else '',
                'photo_key': e.student.photo_key if e.student else '',
                'derived_status': att_map.get(e.student_id),
            } if e.student else None,
            'staff': {
                'id': e.staff_id,
                'nip': e.staff.nip if e.staff else '',
                'full_name': e.staff.person.full_name if e.staff and e.staff.person else '',
            } if e.staff else None,
        })

    # Alerts: Rejected scans (ATT-009)
    alerts = [evt for evt in feed_events if evt['status'] == GateEventStatus.REJECTED]

    # Hardware devices summary (spec/17 §7.2: gate connectivity indicators)
    devices = list(Device.objects.filter(
        foundation_id=foundation_id,
        school_id=school_id,
        deleted_at__isnull=True,
    ).values('id', 'name', 'device_code', 'status', 'direction', 'last_heartbeat_at'))

    return {
        'events': feed_events,
        'alerts': alerts,
        'devices_summary': devices,
        'next_cursor': next_cursor,
        'server_time': now_dt.isoformat(),
    }


@transaction.atomic
def manual_gate_checkin(
    foundation_id: str,
    school_id: str,
    student_id: int,
    direction: str = 'IN',
    occurred_at: Optional[Any] = None,
    reason: str = '',
    user: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Executes a manual student check-in/out for forgotten cards in <=3 taps (spec/05 §4 ATT-013, §8).
    Creates a GateEvent with method=MANUAL and derives/updates AttendanceDay with source=MANUAL and audit log.
    """
    import datetime
    import uuid
    from apps.attendance.models import (
        AttendanceDay, AttendanceRule, AttendanceSource, AttendanceStatus,
        GateDirection, GateEvent, GateEventStatus, GateMethod
    )
    from apps.hardware.models import Device, DeviceClass, DeviceDirection
    from apps.identity.models import Student

    if not reason or not str(reason).strip():
        raise ValidationError(_("Alasan check-in manual wajib diisi (misal: kartu tertinggal)."))

    try:
        student = Student.objects.get(
            id=student_id,
            foundation_id=foundation_id,
            school_id=school_id,
            deleted_at__isnull=True
        )
    except Student.DoesNotExist:
        raise ValidationError(_("Siswa tidak ditemukan pada sekolah ini."))

    if not occurred_at:
        occurred_at = timezone.now()
    elif isinstance(occurred_at, str):
        import dateutil.parser
        occurred_at = dateutil.parser.isoparse(occurred_at)
    if timezone.is_naive(occurred_at):
        occurred_at = timezone.make_aware(occurred_at, timezone.get_current_timezone())

    # Resolve or create designated manual check-in device record for the school
    device = Device.objects.filter(
        foundation_id=foundation_id,
        school_id=school_id,
        device_class=DeviceClass.KIOSK,
        deleted_at__isnull=True
    ).first()
    if not device:
        device = Device.objects.filter(
            foundation_id=foundation_id,
            school_id=school_id,
            deleted_at__isnull=True
        ).first()
    if not device:
        device = Device.objects.create(
            foundation_id=foundation_id,
            school_id=school_id,
            device_code=f"MANUAL-DESK-{school_id}",
            name="Meja Piket / Manual Check-in",
            device_class=DeviceClass.KIOSK,
            direction=DeviceDirection.BIDIRECTIONAL,
            location="Lobi / Pos Keamanan",
        )

    actor_id = str(user.id) if user and getattr(user, 'id', None) else ''
    event_uuid = uuid.uuid4()

    gate_event = GateEvent.objects.create(
        foundation_id=foundation_id,
        event_uuid=event_uuid,
        school=student.school,
        device=device,
        student=student,
        raw_uid=f"MANUAL:{student.nis}",
        direction=direction,
        occurred_at=occurred_at,
        method=GateMethod.MANUAL,
        status=GateEventStatus.ACCEPTED,
        reject_reason='',
        is_duplicate_scan=False,
        replayed=False,
        created_by=actor_id,
        updated_by=actor_id,
    )

    # AttendanceDay derivation
    rule = AttendanceRule.objects.filter(
        foundation_id=foundation_id,
        school_id=school_id,
        deleted_at__isnull=True
    ).first()
    late_after_time = rule.late_after_time if rule else datetime.time(7, 15, 0)

    attendance_day = update_daily_attendance_from_gate(
        foundation_id=foundation_id,
        school_id=school_id,
        student=student,
        occurred_at=occurred_at,
        direction=direction,
        late_after_time=late_after_time,
    )
    attendance_day.source = AttendanceSource.MANUAL
    attendance_day.note = str(reason).strip()
    attendance_day.updated_by = actor_id
    attendance_day.save(update_fields=['source', 'note', 'updated_by', 'updated_at'])

    # Audit log
    audit(
        action='attendance.manual.checkin',
        entity_type='GateEvent',
        entity_id=gate_event.id,
        actor_id=actor_id,
        foundation_id=foundation_id,
        school_id=school_id,
        diff={
            'student_id': str(student.id),
            'student_nis': student.nis,
            'direction': direction,
            'occurred_at': occurred_at.isoformat(),
            'reason': reason,
            'derived_status': attendance_day.status,
        }
    )

    # Domain event
    record_domain_event(
        name='attendance.gate.scanned',
        foundation_id=foundation_id,
        payload={
            'gate_event_id': str(gate_event.id),
            'event_uuid': str(gate_event.event_uuid),
            'school_id': str(school_id),
            'device_id': str(device.id),
            'student_id': str(student.id),
            'staff_id': None,
            'direction': direction,
            'occurred_at': occurred_at.isoformat(),
            'method': GateMethod.MANUAL,
            'reason': reason,
        }
    )

    return {
        'gate_event': gate_event,
        'attendance_day': attendance_day,
    }


def get_device_sync_payload(
    foundation_id: str,
    school_id: str,
    since_cursor: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Produces incremental delta export payload for on-premise campus edge gateways (spec/12 §3, §7).
    Allows edge turnstiles to cache roster and credentials locally for offline verification.
    """
    import dateutil.parser
    from django.db.models import Q
    from apps.attendance.models import AttendanceRule, Credential
    from apps.identity.models import Student

    since_dt = None
    if since_cursor:
        try:
            since_dt = dateutil.parser.isoparse(str(since_cursor).strip())
            if timezone.is_naive(since_dt):
                since_dt = timezone.make_aware(since_dt, timezone.get_current_timezone())
        except (ValueError, TypeError):
            since_dt = None

    # 1. Roster delta
    student_qs = Student.objects.filter(
        foundation_id=foundation_id,
        school_id=school_id,
        deleted_at__isnull=True
    ).select_related('person')

    if since_dt:
        student_qs = student_qs.filter(updated_at__gt=since_dt)

    roster_delta = [{
        'id': s.id,
        'nis': s.nis,
        'nisn': s.nisn,
        'full_name': s.person.full_name if s.person else '',
        'photo_key': s.photo_key,
        'status': s.status,
        'updated_at': s.updated_at.isoformat(),
    } for s in student_qs]

    # 2. Credentials delta
    cred_qs = Credential.objects.filter(
        foundation_id=foundation_id,
        deleted_at__isnull=True,
    ).filter(
        Q(student__school_id=school_id) |
        Q(staff__school_id=school_id)
    ).select_related('student', 'staff')

    if since_dt:
        cred_qs = cred_qs.filter(updated_at__gt=since_dt)

    credentials_delta = [{
        'id': c.id,
        'uid': c.uid,
        'type': c.type,
        'status': c.status,
        'card_number': c.card_number,
        'holder_type': c.holder_type,
        'student_id': c.student_id,
        'staff_id': c.staff_id,
        'expires_at': c.expires_at.isoformat() if c.expires_at else None,
        'is_used': c.is_used,
        'updated_at': c.updated_at.isoformat(),
    } for c in cred_qs]

    # 3. Rules delta
    rule = AttendanceRule.objects.filter(
        foundation_id=foundation_id,
        school_id=school_id,
        deleted_at__isnull=True
    ).first()

    rules_delta = {
        'late_after_time': rule.late_after_time.strftime('%H:%M:%S') if rule else '07:15:00',
        'absent_cutoff_time': rule.absent_cutoff_time.strftime('%H:%M:%S') if rule else '09:00:00',
        'debounce_seconds': rule.debounce_seconds if rule else 120,
    }

    return {
        'school_id': int(school_id),
        'roster_delta': roster_delta,
        'credentials_delta': credentials_delta,
        'rules_delta': rules_delta,
        'next_cursor': timezone.now().isoformat(),
    }


def get_teacher_agenda(teacher: Staff, date) -> list:
    """TCH-001/ACD-020: today's timetable slots for a teacher, including slots they're
    substituting into — and excluding their own slots substituted away to someone else.
    Each entry reports whether PeriodAttendance was already submitted for it.
    """
    from apps.academic.models import SubstitutionStatus, TimetableSlot, TimetableSubstitution

    weekday = date.isoweekday()
    own_slots = TimetableSlot.objects.filter(
        foundation_id=teacher.foundation_id,
        class_subject__teacher=teacher,
        day_of_week=weekday,
        deleted_at__isnull=True,
    ).select_related('class_subject__class_group', 'class_subject__subject')

    substitutions = TimetableSubstitution.objects.filter(
        foundation_id=teacher.foundation_id,
        date=date,
        deleted_at__isnull=True,
    ).select_related(
        'slot__class_subject__class_group',
        'slot__class_subject__subject',
        'original_teacher__person',
    )
    # A slot is only substituted away from the original teacher if NOT declined
    active_subs_away = [s for s in substitutions if s.status != SubstitutionStatus.DECLINED]
    substitutions_by_slot_id = {s.slot_id: s for s in active_subs_away}
    substituted_in = [
        s for s in substitutions
        if s.substitute_teacher_id == teacher.id and s.status != SubstitutionStatus.DECLINED
    ]

    relevant_slot_ids = [s.id for s in own_slots] + [s.slot_id for s in substituted_in]
    submitted_slot_ids = set(
        PeriodAttendance.objects.filter(
            foundation_id=teacher.foundation_id, slot_id__in=relevant_slot_ids, date=date,
        ).values_list('slot_id', flat=True).distinct()
    )

    # Check for active AcademicCalendarEvents affecting attendance on this date
    from datetime import datetime, time
    from apps.academic.models import AcademicCalendarEvent
    tz = timezone.get_current_timezone()
    day_start = timezone.make_aware(datetime.combine(date, time.min), tz)
    day_end = timezone.make_aware(datetime.combine(date, time.max), tz)

    cal_events = list(AcademicCalendarEvent.objects.filter(
        foundation_id=teacher.foundation_id,
        school_id=teacher.school_id,
        affects_attendance=True,
        start_at__lte=day_end,
        end_at__gte=day_start,
        deleted_at__isnull=True,
    ).prefetch_related('class_groups'))

    def _find_exemption(slot_obj):
        for ev in cal_events:
            ev_cgroups = set(ev.class_groups.values_list('id', flat=True))
            if ev_cgroups and slot_obj.class_subject.class_group_id not in ev_cgroups:
                continue
            if ev.is_all_day:
                return ev
            s_start = timezone.make_aware(datetime.combine(date, slot_obj.start_time), tz)
            s_end = timezone.make_aware(datetime.combine(date, slot_obj.end_time), tz)
            if s_start < ev.end_at and s_end > ev.start_at:
                return ev
        return None

    agenda = []
    for slot in own_slots:
        # A slot substituted away to another teacher no longer belongs on this teacher's agenda.
        if slot.id in substitutions_by_slot_id:
            continue
        ev = _find_exemption(slot)
        agenda.append({
            'slot_id': slot.id,
            'period_no': slot.period_no,
            'start_time': slot.start_time,
            'end_time': slot.end_time,
            'class_group': slot.class_subject.class_group.name,
            'subject': slot.class_subject.subject.name,
            'room': slot.room,
            'is_substitution': False,
            'attendance_submitted': slot.id in submitted_slot_ids,
            'is_exempt': ev is not None,
            'exemption_reason': ev.title if ev else None,
            'calendar_event_id': ev.id if ev else None,
        })
    for sub in substituted_in:
        slot = sub.slot
        orig_name = ''
        if sub.original_teacher and hasattr(sub.original_teacher, 'person'):
            orig_name = sub.original_teacher.person.full_name
        ev = _find_exemption(slot)
        agenda.append({
            'slot_id': slot.id,
            'period_no': slot.period_no,
            'start_time': slot.start_time,
            'end_time': slot.end_time,
            'class_group': slot.class_subject.class_group.name,
            'subject': slot.class_subject.subject.name,
            'room': slot.room,
            'is_substitution': True,
            'substitution_id': sub.id,
            'substitution_status': sub.status,
            'original_teacher_name': orig_name,
            'substitution_reason': sub.reason,
            'attendance_submitted': slot.id in submitted_slot_ids,
            'is_exempt': ev is not None,
            'exemption_reason': ev.title if ev else None,
            'calendar_event_id': ev.id if ev else None,
        })

    agenda.sort(key=lambda a: a['period_no'])
    return agenda


def submit_period_attendance(teacher: Staff, slot, date, exceptions: dict, actor=None) -> list:
    """TCH-002/003/ACD-020: default HADIR, pre-fill ALPA from gate data, apply teacher overrides.
    Idempotent. The timetable is the source of truth for who may submit: `date` must fall on
    the slot's scheduled weekday, and `teacher` must be the slot's assigned teacher — or that
    date's substitute, per TimetableSubstitution (spec/04 §5).
    """
    from apps.academic.models import ClassEnrollment
    from apps.academic.services import SlotNotScheduledError, get_effective_teacher_for_slot
    from educore.middleware.tenancy import tenant_context

    if slot.day_of_week != date.isoweekday():
        raise SlotNotScheduledError(
            f"SLOT_NOT_SCHEDULED: slot #{slot.id} is scheduled on day_of_week={slot.day_of_week}, not {date} (isoweekday={date.isoweekday()})."
        )

    effective_teacher = get_effective_teacher_for_slot(slot, date)
    if effective_teacher.id != teacher.id:
        raise NotAuthorizedForSlotError(
            f"NOT_AUTHORIZED_FOR_SLOT: only {effective_teacher} may submit attendance for slot #{slot.id} on {date}."
        )

    class_group = slot.class_subject.class_group
    student_ids = list(
        ClassEnrollment.objects.filter(
            class_group=class_group, is_active=True, deleted_at__isnull=True,
        ).values_list('student_id', flat=True)
    )

    gate_status_by_student = dict(
        AttendanceDay.objects.filter(
            foundation_id=teacher.foundation_id, student_id__in=student_ids, date=date, deleted_at__isnull=True,
        ).values_list('student_id', 'status')
    )

    results = []
    with tenant_context(teacher.foundation_id):
        for student_id in student_ids:
            if student_id in exceptions:
                status = exceptions[student_id]
                source = PeriodAttendanceSource.TEACHER
            elif gate_status_by_student.get(student_id) == AttendanceStatus.ALPA:
                status = AttendanceStatus.ALPA
                source = PeriodAttendanceSource.GATE_PREFILL
            else:
                status = AttendanceStatus.HADIR
                source = PeriodAttendanceSource.TEACHER

            record, _created = PeriodAttendance.objects.update_or_create(
                foundation_id=teacher.foundation_id,
                student_id=student_id,
                slot=slot,
                date=date,
                defaults={'status': status, 'source': source, 'recorded_by': actor},
            )
            results.append(record)

    audit(
        action='attendance.period_attendance.submitted',
        entity_type='TimetableSlot',
        entity_id=slot.id,
        foundation_id=teacher.foundation_id,
        diff={'date': str(date), 'student_count': len(results), 'exceptions': len(exceptions)},
    )
    return results


def sync_offline_period_attendance_batch(teacher: Staff, entries: list) -> list:
    """TCH-004: sync a batch of period-attendance submissions queued while a teacher's
    device was offline, in one round trip. Each entry is
    {slot_id, date (ISO string), exceptions: {student_id: status}}.

    submit_period_attendance is already fully idempotent (update_or_create keyed by
    student+slot+date) and already validates weekday-scheduling and substitution-aware
    authorization (ACD-020) — this is a thin per-entry wrapper, not a new idempotency
    mechanism. One bad entry in an offline backlog must not discard the rest of the
    batch, matching process_offline_pos_batch's per-item result-list pattern.
    """
    import datetime as _dt
    from apps.academic.models import TimetableSlot
    from apps.academic.services import SlotNotScheduledError

    results = []
    for entry in entries:
        slot_id = entry['slot_id']
        slot = TimetableSlot.objects.filter(id=slot_id, foundation_id=teacher.foundation_id).first()
        if not slot:
            results.append({'slot_id': slot_id, 'status': 'SLOT_NOT_FOUND'})
            continue

        date = _dt.date.fromisoformat(entry['date']) if isinstance(entry['date'], str) else entry['date']
        exceptions = entry.get('exceptions', {})

        try:
            submit_period_attendance(teacher, slot, date, exceptions)
        except SlotNotScheduledError:
            results.append({'slot_id': slot_id, 'date': str(date), 'status': 'SLOT_NOT_SCHEDULED'})
            continue
        except NotAuthorizedForSlotError:
            results.append({'slot_id': slot_id, 'date': str(date), 'status': 'NOT_AUTHORIZED'})
            continue

        results.append({'slot_id': slot_id, 'date': str(date), 'status': 'SYNCED'})

    return results


MAX_ABSENCE_ATTACHMENT_SIZE = 1024 * 1024  # 1MB (PAR-011)
ALLOWED_ABSENCE_CONTENT_TYPES = {
    'image/jpeg',
    'image/jpg',
    'image/png',
    'image/webp',
    'application/pdf',
}


def validate_absence_attachment(uploaded_file) -> None:
    """Validates that an absence photo attachment complies with PAR-011.
    Must be <= 1MB (1,048,576 bytes) and an image or PDF document.
    """
    if uploaded_file.size > MAX_ABSENCE_ATTACHMENT_SIZE:
        raise ValidationError(
            _("Ukuran berkas lampiran tidak boleh melebihi 1MB (PAR-011).")
        )
    content_type = getattr(uploaded_file, 'content_type', '') or ''
    if content_type.lower() not in ALLOWED_ABSENCE_CONTENT_TYPES:
        raise ValidationError(
            _("Format berkas lampiran tidak didukung. Harap gunakan JPG, PNG, WebP, atau PDF.")
        )


@transaction.atomic
def submit_absence_request(
    student: Student,
    requested_by: Any,
    date_from: Any,
    date_to: Any,
    type: str,
    reason: str,
    attachment_file: Optional[Any] = None,
    foundation_id: Optional[int] = None,
) -> AbsenceRequest:
    """
    Parent submission of absence request with photo attachment (spec/05 §2, spec/08 PAR-011).
    """
    import datetime as _dt
    from apps.core.services import write_generated_file

    if foundation_id is None:
        foundation_id = student.foundation_id

    if isinstance(date_from, str):
        date_from = _dt.date.fromisoformat(date_from)
    if isinstance(date_to, str):
        date_to = _dt.date.fromisoformat(date_to)

    if date_from > date_to:
        raise ValidationError(_("Tanggal mulai tidak boleh melebihi tanggal akhir."))

    if type not in AbsenceType.values:
        raise ValidationError(_(f"Tipe permohonan '{type}' tidak valid. Pilihan: SAKIT, IZIN."))

    if not reason or not str(reason).strip():
        raise ValidationError(_("Alasan izin atau keterangan sakit wajib diisi."))

    attachment_key = ''
    if attachment_file:
        validate_absence_attachment(attachment_file)
        stored_file = write_generated_file(
            purpose='ABSENCE_ATTACHMENT',
            filename=attachment_file.name,
            data=b''.join(attachment_file.chunks()),
            content_type=attachment_file.content_type,
            foundation_id=foundation_id,
            uploaded_by=str(requested_by.id) if requested_by else '',
            school_id=student.school_id,
        )
        attachment_key = stored_file.key

    absence_request = AbsenceRequest.objects.create(
        foundation_id=foundation_id,
        school=student.school,
        student=student,
        requested_by=requested_by,
        date_from=date_from,
        date_to=date_to,
        type=type,
        reason=str(reason).strip(),
        attachment_key=attachment_key,
        status=AbsenceRequestStatus.PENDING,
        created_by=str(requested_by.id) if requested_by else '',
    )

    audit(
        action='attendance.absence_request.submitted',
        entity_type='AbsenceRequest',
        entity_id=absence_request.id,
        actor_id=str(requested_by.id) if requested_by else '',
        foundation_id=foundation_id,
        school_id=student.school_id,
        diff={
            'student_id': str(student.id),
            'type': type,
            'date_from': date_from.isoformat(),
            'date_to': date_to.isoformat(),
            'reason': absence_request.reason,
            'has_attachment': bool(attachment_key),
        }
    )

    record_domain_event(
        name='attendance.absence_request.submitted',
        foundation_id=foundation_id,
        payload={
            'absence_request_id': str(absence_request.id),
            'student_id': str(student.id),
            'school_id': str(student.school_id),
            'type': type,
            'date_from': date_from.isoformat(),
            'date_to': date_to.isoformat(),
        }
    )

    return absence_request


@transaction.atomic
def approve_absence_request(
    absence_request: AbsenceRequest,
    decided_by: Any,
    note: str = '',
) -> AbsenceRequest:
    """
    Staff approval of an absence request (spec/05 ATT-002).
    Overrides daily attendance status to SAKIT or IZIN for the covered date range.
    """
    import datetime as _dt

    if absence_request.status != AbsenceRequestStatus.PENDING:
        raise ValidationError(_("Permohonan ini telah diproses sebelumnya dan tidak dapat disetujui lagi."))

    actor_id = str(decided_by.id) if decided_by and getattr(decided_by, 'id', None) else ''

    absence_request.status = AbsenceRequestStatus.APPROVED
    absence_request.decided_by = decided_by
    absence_request.decided_at = timezone.now()
    absence_request.decision_note = str(note or '').strip()
    absence_request.updated_by = actor_id
    absence_request.save(update_fields=['status', 'decided_by', 'decided_at', 'decision_note', 'updated_by', 'updated_at'])

    # ATT-002: Override daily status for each date in the window
    note_text = f"Disetujui: {absence_request.reason}" if not note else f"Disetujui: {note}"
    current_date = absence_request.date_from
    while current_date <= absence_request.date_to:
        att_day = AttendanceDay.all_tenants.filter(
            foundation_id=absence_request.foundation_id,
            school=absence_request.school,
            student=absence_request.student,
            date=current_date,
            deleted_at__isnull=True,
        ).first()

        if att_day:
            if not att_day.is_override:
                att_day.original_status = att_day.status
            att_day.status = absence_request.type
            att_day.source = AttendanceSource.MANUAL
            att_day.is_override = True
            att_day.note = note_text
            att_day.updated_by = actor_id
            att_day.save(update_fields=['status', 'original_status', 'source', 'is_override', 'note', 'updated_by', 'updated_at'])
        else:
            AttendanceDay.objects.create(
                foundation_id=absence_request.foundation_id,
                school=absence_request.school,
                student=absence_request.student,
                date=current_date,
                status=absence_request.type,
                source=AttendanceSource.MANUAL,
                is_override=True,
                note=note_text,
                created_by=actor_id,
            )
        current_date += _dt.timedelta(days=1)

    audit(
        action='attendance.absence_request.approved',
        entity_type='AbsenceRequest',
        entity_id=absence_request.id,
        actor_id=actor_id,
        foundation_id=absence_request.foundation_id,
        school_id=absence_request.school_id,
        diff={
            'student_id': str(absence_request.student_id),
            'status': AbsenceRequestStatus.APPROVED,
            'type': absence_request.type,
            'date_from': absence_request.date_from.isoformat(),
            'date_to': absence_request.date_to.isoformat(),
            'decision_note': absence_request.decision_note,
        }
    )

    record_domain_event(
        name='attendance.absence_request.approved',
        foundation_id=absence_request.foundation_id,
        payload={
            'absence_request_id': str(absence_request.id),
            'student_id': str(absence_request.student_id),
            'status': AbsenceRequestStatus.APPROVED,
        }
    )

    return absence_request


@transaction.atomic
def reject_absence_request(
    absence_request: AbsenceRequest,
    decided_by: Any,
    note: str = '',
) -> AbsenceRequest:
    """
    Staff rejection of an absence request (spec/05 §2).
    """
    if absence_request.status != AbsenceRequestStatus.PENDING:
        raise ValidationError(_("Permohonan ini telah diproses sebelumnya dan tidak dapat ditolak lagi."))

    actor_id = str(decided_by.id) if decided_by and getattr(decided_by, 'id', None) else ''

    absence_request.status = AbsenceRequestStatus.REJECTED
    absence_request.decided_by = decided_by
    absence_request.decided_at = timezone.now()
    absence_request.decision_note = str(note or '').strip()
    absence_request.updated_by = actor_id
    absence_request.save(update_fields=['status', 'decided_by', 'decided_at', 'decision_note', 'updated_by', 'updated_at'])

    audit(
        action='attendance.absence_request.rejected',
        entity_type='AbsenceRequest',
        entity_id=absence_request.id,
        actor_id=actor_id,
        foundation_id=absence_request.foundation_id,
        school_id=absence_request.school_id,
        diff={
            'student_id': str(absence_request.student_id),
            'status': AbsenceRequestStatus.REJECTED,
            'decision_note': absence_request.decision_note,
        }
    )

    record_domain_event(
        name='attendance.absence_request.rejected',
        foundation_id=absence_request.foundation_id,
        payload={
            'absence_request_id': str(absence_request.id),
            'student_id': str(absence_request.student_id),
            'status': AbsenceRequestStatus.REJECTED,
        }
    )

    return absence_request


def send_absence_alert(
    student: Student,
    school: Any,
    target_date: Any,
    cutoff_time: Any,
    foundation_id: str,
) -> int:
    """
    Dispatches high-priority absence notification to linked guardians of an unexcused absent student (spec/05 ATT-006, spec/13).
    Uses NotificationCategory.ABSENCE and template 'attendance.absent'.
    Returns count of notification intents dispatched.
    """
    from apps.identity.models import GuardianLink
    from apps.notifications.models import NotificationCategory, NotificationPriority
    from apps.notifications.services import dispatch_intent

    guardian_links = GuardianLink.objects.filter(
        foundation_id=foundation_id,
        student=student,
        deleted_at__isnull=True,
    ).select_related('guardian__person', 'guardian__user')

    if not guardian_links.exists():
        return 0

    school_name = school.name if school else 'Sekolah'
    student_name = student.person.full_name if (student.person and student.person.full_name) else (student.nis or 'Siswa')
    date_str = target_date.strftime('%Y-%m-%d')
    cutoff_str = cutoff_time.strftime('%H:%M')

    dispatched_count = 0
    for link in guardian_links:
        guardian = link.guardian
        user = guardian.user
        phone = (getattr(user, 'phone_e164', None) or getattr(user, 'phone', '')) if user else ''
        email = getattr(user, 'email', '') if user else ''
        guardian_name = guardian.person.full_name if (guardian.person and guardian.person.full_name) else 'Wali Murid'

        if not phone and not user:
            continue

        dedupe_key = f"absence:{student.id}:{date_str}:{guardian.id}"
        payload = {
            'type': NotificationCategory.ABSENCE,
            'student_id': student.id,
            'student_name': student_name,
            'guardian_name': guardian_name,
            'school_name': school_name,
            'cutoff_time': cutoff_str,
            'date': date_str,
        }

        dispatch_intent(
            foundation_id=foundation_id,
            school_id=school.id,
            recipient_user=user,
            recipient_phone=phone,
            recipient_email=email,
            recipient_name=guardian_name,
            category=NotificationCategory.ABSENCE,
            template_key='attendance.absent',
            payload=payload,
            priority=NotificationPriority.HIGH,
            dedupe_key=dedupe_key,
            immediate=True,
        )
        dispatched_count += 1

    return dispatched_count


def get_school_timezone(school: Any):
    """Resolves `school.timezone` (default 'Asia/Jakarta') to a zoneinfo.ZoneInfo, falling
    back to Asia/Jakarta on a missing/invalid value. Shared by every caller that needs the
    school's local wall-clock time (the daily absence sweep, clinic SAKIT overrides)."""
    import zoneinfo

    school_tz_str = getattr(school, 'timezone', None) or 'Asia/Jakarta'
    try:
        return zoneinfo.ZoneInfo(school_tz_str)
    except Exception:
        return zoneinfo.ZoneInfo('Asia/Jakarta')


def mark_absent_students_for_school(
    school: Any,
    target_date: Optional[Any] = None,
    dry_run: bool = False,
    user: Optional[Any] = None,
    force_cutoff: bool = False,
) -> Dict[str, Any]:
    """
    Automated daily absence sweep for a school (spec/05 §3 ATT-001..005, deploy/crontab:19).
    
    Invariants:
    - ATT-001: Daily status must derive automatically: no scan by absent_cutoff and no approved request -> ALPA.
    - ATT-002: Approved absence request overrides daily status to SAKIT or IZIN.
    - ATT-003: Staff manual override (is_override=True) or existing attendance is preserved.
    - ATT-005: Non-school days (weekends, national holidays, school holidays from academic calendar)
      MUST NOT generate ALPA.
    - Idempotent: Multiple runs on the same date will not re-mark or re-notify students.
    """
    import datetime
    from apps.attendance.models import (
        AttendanceDay, AttendanceRule, AttendanceSource, AttendanceStatus,
        AbsenceRequest, AbsenceRequestStatus, GateEvent, GateEventStatus,
    )
    from apps.identity.models import Student
    from apps.academic.models import AcademicCalendarEvent, AcademicCalendarEventType, ClassEnrollment

    foundation_id = school.foundation_id

    # 1. Determine school local datetime
    school_tz = get_school_timezone(school)

    now_local = timezone.now().astimezone(school_tz)
    today_local = now_local.date()

    if target_date is None:
        target_date = today_local

    # 2. Check future date
    if target_date > today_local:
        return {
            'school_id': str(school.id),
            'school_name': school.name,
            'date': target_date.isoformat(),
            'status': 'SKIPPED_FUTURE_DATE',
            'reason': 'Tanggal target berada di masa depan.',
            'total_students': 0,
            'already_recorded': 0,
            'excused_from_requests': 0,
            'marked_alpa': 0,
            'notifications_dispatched': 0,
        }

    # 3. Retrieve AttendanceRule & evaluate cutoff time
    rule = AttendanceRule.objects.filter(
        foundation_id=foundation_id,
        school=school,
        deleted_at__isnull=True,
    ).first()

    absent_cutoff_time = rule.absent_cutoff_time if rule else datetime.time(9, 0, 0)

    # If target_date is today, verify cutoff time has elapsed (unless force_cutoff=True)
    if target_date == today_local and not force_cutoff:
        current_time = now_local.time()
        if current_time < absent_cutoff_time:
            return {
                'school_id': str(school.id),
                'school_name': school.name,
                'date': target_date.isoformat(),
                'status': 'SKIPPED_BEFORE_CUTOFF',
                'reason': f"Waktu sekarang ({current_time.strftime('%H:%M')}) belum melewati batas waktu cutoff ({absent_cutoff_time.strftime('%H:%M')}).",
                'cutoff_time': absent_cutoff_time.strftime('%H:%M'),
                'total_students': 0,
                'already_recorded': 0,
                'excused_from_requests': 0,
                'marked_alpa': 0,
                'notifications_dispatched': 0,
            }

    # 4. ATT-005: Weekend and Holiday checks
    # Sunday is non-school day (weekday == 6 in Python)
    if target_date.weekday() == 6:
        return {
            'school_id': str(school.id),
            'school_name': school.name,
            'date': target_date.isoformat(),
            'status': 'SKIPPED_NON_SCHOOL_DAY',
            'reason': 'Hari Minggu adalah hari libur (ATT-005).',
            'total_students': 0,
            'already_recorded': 0,
            'excused_from_requests': 0,
            'marked_alpa': 0,
            'notifications_dispatched': 0,
        }

    # Check academic calendar holidays affecting attendance
    target_start_dt = datetime.datetime.combine(target_date, datetime.time.min, tzinfo=school_tz)
    target_end_dt = datetime.datetime.combine(target_date, datetime.time.max, tzinfo=school_tz)

    holiday_events = list(AcademicCalendarEvent.objects.filter(
        foundation_id=foundation_id,
        school=school,
        deleted_at__isnull=True,
        affects_attendance=True,
        event_type=AcademicCalendarEventType.HOLIDAY,
        start_at__lte=target_end_dt,
        end_at__gte=target_start_dt,
    ).prefetch_related('class_groups'))

    # Check for school-wide holiday (no specific class groups specified)
    school_wide_holiday = next((h for h in holiday_events if not h.class_groups.exists()), None)
    if school_wide_holiday:
        return {
            'school_id': str(school.id),
            'school_name': school.name,
            'date': target_date.isoformat(),
            'status': 'SKIPPED_HOLIDAY',
            'reason': f"Hari libur kalender akademik sekolah: {school_wide_holiday.title} (ATT-005).",
            'total_students': 0,
            'already_recorded': 0,
            'excused_from_requests': 0,
            'marked_alpa': 0,
            'notifications_dispatched': 0,
        }

    # Identify class groups that have holidays on this date
    holiday_class_group_ids = set()
    for h in holiday_events:
        for cg in h.class_groups.all():
            holiday_class_group_ids.add(cg.id)

    # 5. Query active enrolled students in this school
    active_students = list(
        Student.objects.filter(
            foundation_id=foundation_id,
            school=school,
            status=Student.STATUS_ACTIVE,
            deleted_at__isnull=True,
        ).select_related('person')
    )

    if not active_students:
        return {
            'school_id': str(school.id),
            'school_name': school.name,
            'date': target_date.isoformat(),
            'status': 'NO_ACTIVE_STUDENTS',
            'total_students': 0,
            'already_recorded': 0,
            'excused_from_requests': 0,
            'marked_alpa': 0,
            'notifications_dispatched': 0,
        }

    # 6. Fetch existing attendance records for the date
    existing_attendance_days = {
        att.student_id: att
        for att in AttendanceDay.objects.filter(
            foundation_id=foundation_id,
            school=school,
            date=target_date,
            deleted_at__isnull=True,
        )
    }

    # Fetch accepted gate events for the date
    gate_event_student_ids = set(
        GateEvent.objects.filter(
            foundation_id=foundation_id,
            school=school,
            occurred_at__gte=target_start_dt,
            occurred_at__lte=target_end_dt,
            status=GateEventStatus.ACCEPTED,
            student__isnull=False,
            deleted_at__isnull=True,
        ).values_list('student_id', flat=True)
    )

    # Fetch approved absence requests covering target_date
    approved_requests = {
        req.student_id: req
        for req in AbsenceRequest.objects.filter(
            foundation_id=foundation_id,
            school=school,
            status=AbsenceRequestStatus.APPROVED,
            date_from__lte=target_date,
            date_to__gte=target_date,
            deleted_at__isnull=True,
        )
    }

    # Map student active class enrollment if class-specific holidays exist
    student_class_map = {}
    if holiday_class_group_ids:
        for enr in ClassEnrollment.objects.filter(
            foundation_id=foundation_id,
            student__in=active_students,
            is_active=True,
            deleted_at__isnull=True,
        ):
            student_class_map[enr.student_id] = enr.class_group_id

    actor_id = str(user.id) if user and getattr(user, 'id', None) else ''

    already_recorded_count = 0
    excused_count = 0
    holiday_exempt_count = 0
    alpa_count = 0
    notifications_count = 0

    with transaction.atomic():
        for student in active_students:
            # Check if student already has AttendanceDay record
            if student.id in existing_attendance_days:
                already_recorded_count += 1
                continue

            # Check if student scanned at gate
            if student.id in gate_event_student_ids:
                already_recorded_count += 1
                continue

            # Check if student belongs to a class group with holiday exemption (ATT-005)
            if holiday_class_group_ids:
                cg_id = student_class_map.get(student.id)
                if cg_id in holiday_class_group_ids:
                    holiday_exempt_count += 1
                    continue

            # Check if student has approved absence request (ATT-002)
            if student.id in approved_requests:
                req = approved_requests[student.id]
                note_text = f"Disetujui: {req.reason}"
                if not dry_run:
                    AttendanceDay.objects.create(
                        foundation_id=foundation_id,
                        school=school,
                        student=student,
                        date=target_date,
                        status=req.type,  # SAKIT or IZIN
                        source=AttendanceSource.MANUAL,
                        is_override=True,
                        note=note_text,
                        created_by=actor_id,
                    )
                excused_count += 1
                continue

            # Unrecorded and unexcused -> mark ALPA (ATT-001)
            alpa_count += 1
            if not dry_run:
                att_day = AttendanceDay.objects.create(
                    foundation_id=foundation_id,
                    school=school,
                    student=student,
                    date=target_date,
                    status=AttendanceStatus.ALPA,
                    source=AttendanceSource.SYSTEM,
                    note=f"Tidak hadir tanpa keterangan hingga batas cutoff {absent_cutoff_time.strftime('%H:%M')} WIB (ATT-001)",
                    created_by=actor_id,
                )

                audit(
                    action='attendance.day.auto_marked_alpa',
                    entity_type='AttendanceDay',
                    entity_id=att_day.id,
                    actor_id=actor_id,
                    foundation_id=foundation_id,
                    school_id=school.id,
                    diff={
                        'student_id': str(student.id),
                        'date': target_date.isoformat(),
                        'status': AttendanceStatus.ALPA,
                        'source': AttendanceSource.SYSTEM,
                        'cutoff_time': absent_cutoff_time.strftime('%H:%M:%S'),
                    }
                )

                record_domain_event(
                    name='attendance.day.auto_marked_alpa',
                    foundation_id=foundation_id,
                    payload={
                        'attendance_day_id': str(att_day.id),
                        'school_id': str(school.id),
                        'student_id': str(student.id),
                        'date': target_date.isoformat(),
                        'cutoff_time': absent_cutoff_time.strftime('%H:%M:%S'),
                    }
                )

                dispatched = send_absence_alert(
                    student=student,
                    school=school,
                    target_date=target_date,
                    cutoff_time=absent_cutoff_time,
                    foundation_id=foundation_id,
                )
                notifications_count += dispatched

    return {
        'school_id': str(school.id),
        'school_name': school.name,
        'date': target_date.isoformat(),
        'status': 'COMPLETED' if not dry_run else 'DRY_RUN',
        'total_students': len(active_students),
        'already_recorded': already_recorded_count,
        'excused_from_requests': excused_count,
        'holiday_exempt': holiday_exempt_count,
        'marked_alpa': alpa_count,
        'notifications_dispatched': notifications_count,
    }


