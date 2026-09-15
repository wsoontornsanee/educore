import secrets
from datetime import timedelta
from typing import Any, Dict, Optional

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import AuditEvent, DomainEvent
from apps.core.services import audit, record_domain_event
from apps.attendance.models import Credential, CredentialStatus, CredentialType
from apps.identity.models import Student, Staff


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
                record_domain_event(
                    name='attendance.gate.scanned',
                    foundation_id=foundation_id,
                    payload={
                        'gate_event_id': str(gate_event.id),
                        'event_uuid': str(gate_event.event_uuid),
                        'school_id': str(device.school_id),
                        'device_id': str(device.id),
                        'student_id': str(student.id) if student else None,
                        'staff_id': str(staff.id) if staff else None,
                        'direction': direction,
                        'occurred_at': occurred_at.isoformat(),
                        'method': method,
                    }
                )

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


