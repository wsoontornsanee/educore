"""Pickup safety: who may collect a student, and the record of who did (spec/05 §5, ATT-014..ATT-018).

A student is released only to (1) a guardian the school has on file with `can_pickup`, (2) a person a guardian
authorised for a time window, proven by a signed QR token, or (3) anyone else only through a school-admin
override with a written reason. Every release is a `PickupEvent` and tells ALL linked guardians, including those
not involved (ATT-017).

The QR token carries only the authorisation's id. Everything that decides whether it works (window, revoked,
already used) is read from the database, so revoking or spending the row kills every copy of the QR, and a
forged or edited token fails its signature. Third-party details on an authorisation (name, phone, photo) are
shown to staff at the gate and are never written to logs or audit diffs.
"""
import logging
from datetime import timedelta

from django.core import signing
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.attendance.models import PickupAuthorization, PickupEvent, PickupMethod
from apps.core.services import audit
from apps.identity.models import GuardianLink
from apps.identity.recipients import get_school_admin_users

logger = logging.getLogger(__name__)

TOKEN_SALT = 'attendance.pickup.qr.v1'
MAX_AUTHORIZATION_WINDOW = timedelta(days=366)
MAX_OVERRIDE_REASON_AUDIT_CHARS = 500


class PickupError(ValueError):
    """A pickup rule was not met; ``code`` is stable and machine-readable, ``message`` is for the person."""

    def __init__(self, code: str, message: str):
        super().__init__(code)
        self.code = code
        self.message = message


# ── Authorisations (guardian side, ATT-015) ─────────────────────────────────────────────────────────────

def create_pickup_authorization(
    *, user, student, person_name, valid_from, valid_to, relation='', phone='', photo_key='', one_time=True, now=None,
) -> PickupAuthorization:
    """A guardian who may pick up authorises a named person for a window. Returns the saved authorisation."""
    now = now or timezone.now()
    person_name = (person_name or '').strip()
    if not person_name:
        raise PickupError('PICKUP_PERSON_REQUIRED', _("Nama penjemput wajib diisi."))
    link = GuardianLink.all_tenants.filter(
        foundation_id=student.foundation_id, student=student, guardian__user=user, can_pickup=True,
        deleted_at__isnull=True, guardian__deleted_at__isnull=True,
    ).select_related('guardian').first()
    if link is None:
        raise PickupError(
            'PICKUP_NOT_GUARDIAN',
            _("Hanya wali murid siswa ini yang berhak menjemput yang dapat membuat otorisasi penjemputan."),
        )
    if not (valid_to > valid_from and valid_to > now and valid_to - valid_from <= MAX_AUTHORIZATION_WINDOW):
        raise PickupError(
            'PICKUP_INVALID_WINDOW',
            _("Masa berlaku tidak valid: harus berakhir di masa depan, setelah waktu mulai, dan paling lama 366 hari."),
        )
    authorization = PickupAuthorization.all_tenants.create(
        foundation_id=student.foundation_id, school=student.school, student=student,
        created_by_guardian=link.guardian, person_name=person_name, relation=(relation or '').strip(),
        phone=(phone or '').strip(), photo_key=photo_key or '', valid_from=valid_from, valid_to=valid_to,
        one_time=one_time,
    )
    audit(
        action='attendance.pickup_authorization.created', entity_type='PickupAuthorization',
        entity_id=authorization.id, actor_id=str(user.id), foundation_id=student.foundation_id,
        school_id=student.school_id,
        diff={
            'student_id': student.id, 'one_time': one_time,
            'valid_from': valid_from.isoformat(), 'valid_to': valid_to.isoformat(),
        },
    )
    return authorization


def pickup_qr_token(authorization: PickupAuthorization) -> str:
    """The signed QR payload for an authorisation: its id and nothing else."""
    return signing.dumps({'a': authorization.id}, salt=TOKEN_SALT)


@transaction.atomic
def revoke_pickup_authorization(authorization: PickupAuthorization, user, now=None) -> PickupAuthorization:
    """Withdraw an authorisation. Idempotent; a one-time authorisation that was already used cannot be revoked."""
    now = now or timezone.now()
    authorization = PickupAuthorization.all_tenants.select_for_update().get(
        id=authorization.id, foundation_id=authorization.foundation_id,
    )
    if authorization.revoked_at is not None:
        return authorization
    if authorization.one_time and authorization.used_at is not None:
        raise PickupError('PICKUP_ALREADY_USED', _("Otorisasi penjemputan sekali pakai ini sudah digunakan."))
    authorization.revoked_at = now
    authorization.revoked_by = user
    authorization.save(update_fields=['revoked_at', 'revoked_by', 'updated_at'])
    audit(
        action='attendance.pickup_authorization.revoked', entity_type='PickupAuthorization',
        entity_id=authorization.id, actor_id=str(user.id), foundation_id=authorization.foundation_id,
        school_id=authorization.school_id, diff={'student_id': authorization.student_id},
    )
    return authorization


# ── Verification and release (staff side, ATT-014, ATT-016) ────────────────────────────────────────────

def load_authorization(foundation_id, *, qr_token=None, authorization_id=None) -> PickupAuthorization:
    """Find an authorisation from a scanned token or an id, inside `foundation_id`. Anything that does not
    resolve reads as an invalid token, so a caller learns nothing about other foundations' rows."""
    if qr_token:
        try:
            data = signing.loads(qr_token, salt=TOKEN_SALT)
        except signing.BadSignature:
            raise PickupError('PICKUP_INVALID_TOKEN', _("Kode QR penjemputan tidak valid."))
        authorization_id = data.get('a') if isinstance(data, dict) else None
    authorization = None
    if isinstance(authorization_id, int) and not isinstance(authorization_id, bool):
        authorization = PickupAuthorization.all_tenants.select_related('student__person', 'school').filter(
            id=authorization_id, foundation_id=foundation_id, deleted_at__isnull=True,
        ).first()
    if authorization is None:
        raise PickupError('PICKUP_INVALID_TOKEN', _("Kode QR penjemputan tidak valid."))
    return authorization


def authorization_status(authorization: PickupAuthorization, now=None) -> str:
    """REVOKED, USED (a spent one-time authorisation), EXPIRED, SCHEDULED (not started yet) or ACTIVE."""
    now = now or timezone.now()
    if authorization.revoked_at is not None:
        return 'REVOKED'
    if authorization.one_time and authorization.used_at is not None:
        return 'USED'
    if now > authorization.valid_to:
        return 'EXPIRED'
    if now < authorization.valid_from:
        return 'SCHEDULED'
    return 'ACTIVE'


def assert_usable(authorization: PickupAuthorization, now=None) -> None:
    """Raise PickupError unless this authorisation can release the student right now."""
    now = now or timezone.now()
    if authorization.revoked_at is not None:
        raise PickupError('PICKUP_REVOKED', _("Otorisasi penjemputan ini sudah dicabut."))
    if authorization.one_time and authorization.used_at is not None:
        raise PickupError('PICKUP_ALREADY_USED', _("Otorisasi penjemputan sekali pakai ini sudah digunakan."))
    if not (authorization.valid_from <= now <= authorization.valid_to):
        raise PickupError('PICKUP_WINDOW_EXPIRED', _("Kode QR penjemputan di luar masa berlakunya."))


def verification_summary(authorization: PickupAuthorization) -> dict:
    """What staff see BEFORE releasing (ATT-016): the person's photo and name, and which student. Only the last
    four digits of the phone are shown."""
    student = authorization.student
    phone = authorization.phone
    return {
        'authorization_id': authorization.id,
        'student_id': student.id,
        'student_name': student.person.full_name if student.person else '',
        'school_id': authorization.school_id,
        'person_name': authorization.person_name,
        'relation': authorization.relation,
        'photo_key': authorization.photo_key,
        'phone_last4': phone[-4:] if phone else '',
        'valid_from': authorization.valid_from,
        'valid_to': authorization.valid_to,
        'one_time': authorization.one_time,
    }


def release_with_authorization(*, staff_user, authorization: PickupAuthorization, now=None) -> PickupEvent:
    """Release the student to the authorised person. A one-time authorisation is spent here, under a row lock, so
    two scans of the same QR cannot both release."""
    now = now or timezone.now()
    with transaction.atomic():
        locked = PickupAuthorization.all_tenants.select_for_update().select_related('student__person', 'school').get(
            id=authorization.id, foundation_id=authorization.foundation_id,
        )
        assert_usable(locked, now)
        if locked.one_time:
            locked.used_at = now
            locked.save(update_fields=['used_at', 'updated_at'])
        event = _record_event(
            staff_user=staff_user, student=locked.student, method=PickupMethod.QR, picked_up_by=locked.person_name,
            authorization=locked, guardian=locked.created_by_guardian, now=now,
        )
    notify_pickup_completed(event)
    return event


def release_to_guardian(*, staff_user, student, guardian_id, now=None) -> PickupEvent:
    """Release the student to a guardian the school has on file with `can_pickup` (ATT-014)."""
    now = now or timezone.now()
    link = GuardianLink.all_tenants.filter(
        foundation_id=student.foundation_id, student=student, guardian_id=guardian_id, can_pickup=True,
        deleted_at__isnull=True, guardian__deleted_at__isnull=True,
    ).select_related('guardian__person').first()
    if link is None:
        raise PickupError(
            'PICKUP_NOT_AUTHORISED',
            _("Penjemput tidak terdaftar. Hanya admin sekolah yang dapat mengizinkan dengan alasan tertulis."),
        )
    guardian = link.guardian
    name = guardian.person.full_name if guardian.person else ''
    with transaction.atomic():
        event = _record_event(
            staff_user=staff_user, student=student, method=PickupMethod.GUARDIAN, picked_up_by=name,
            authorization=None, guardian=guardian, now=now,
        )
    notify_pickup_completed(event)
    return event


def override_release(*, staff_user, student, picked_up_by, reason, now=None) -> PickupEvent:
    """A school admin releases the student to someone who is not authorised (ATT-018). The reason is mandatory and
    the audit event is flagged HIGH priority."""
    now = now or timezone.now()
    picked_up_by = (picked_up_by or '').strip()
    reason = (reason or '').strip()
    if not picked_up_by:
        raise PickupError('PICKUP_PERSON_REQUIRED', _("Nama penjemput wajib diisi."))
    if not reason:
        raise PickupError('PICKUP_OVERRIDE_REASON_REQUIRED', _("Alasan pengecualian wajib diisi."))
    with transaction.atomic():
        event = _record_event(
            staff_user=staff_user, student=student, method=PickupMethod.OVERRIDE, picked_up_by=picked_up_by,
            authorization=None, guardian=None, now=now, override_reason=reason,
        )
        audit(
            action='attendance.pickup.override', entity_type='PickupEvent', entity_id=event.id,
            actor_id=str(staff_user.id), foundation_id=student.foundation_id, school_id=student.school_id,
            diff={
                'priority': 'HIGH', 'student_id': student.id,
                'reason': reason[:MAX_OVERRIDE_REASON_AUDIT_CHARS],
            },
        )
    notify_pickup_completed(event)
    notify_override_to_school_admins(event)
    return event


def _record_event(*, staff_user, student, method, picked_up_by, authorization, guardian, now, override_reason=''):
    event = PickupEvent.all_tenants.create(
        foundation_id=student.foundation_id, school=student.school, student=student, authorization=authorization,
        authorized_by_guardian=guardian, picked_up_by=picked_up_by, verified_by=staff_user, occurred_at=now,
        method=method, override_reason=override_reason,
    )
    if method != PickupMethod.OVERRIDE:
        audit(
            action='attendance.pickup.completed', entity_type='PickupEvent', entity_id=event.id,
            actor_id=str(staff_user.id), foundation_id=student.foundation_id, school_id=student.school_id,
            diff={'student_id': student.id, 'method': method},
        )
    return event


# ── Notification (ATT-017) ──────────────────────────────────────────────────────────────────────────────

def notify_pickup_completed(event: PickupEvent) -> int:
    """Tell EVERY linked guardian of the student that the pickup happened, including those not involved (ATT-017).
    A failed delivery never undoes the release: it is logged (without names) and the rest still go out.
    Returns how many notices were queued."""
    from apps.notifications.models import NotificationCategory, NotificationPriority
    from apps.notifications.services import dispatch_intent

    student = event.student
    school = event.school
    template_key = 'attendance.pickup_override' if event.method == PickupMethod.OVERRIDE else 'attendance.pickup'
    local_time = timezone.localtime(event.occurred_at)
    links = GuardianLink.all_tenants.filter(
        foundation_id=event.foundation_id, student=student, deleted_at__isnull=True,
        guardian__deleted_at__isnull=True,
    ).select_related('guardian__person', 'guardian__user')

    queued = 0
    for link in links:
        guardian = link.guardian
        user = guardian.user
        phone = (getattr(user, 'phone_e164', None) or getattr(user, 'phone', '')) if user else ''
        email = getattr(user, 'email', '') if user else ''
        if not phone and not user:
            continue
        payload = {
            'type': NotificationCategory.DEPARTURE,
            'student_id': student.id,
            'student_name': student.person.full_name if student.person else 'Siswa',
            'guardian_name': guardian.person.full_name if guardian.person else 'Wali Murid',
            'school_name': school.name if school else 'Sekolah',
            'picked_up_by': event.picked_up_by,
            'time': local_time.strftime('%H:%M'),
            'date': local_time.strftime('%Y-%m-%d'),
        }
        try:
            dispatch_intent(
                foundation_id=event.foundation_id, school_id=event.school_id, recipient_user=user,
                recipient_phone=phone, recipient_email=email, recipient_name=payload['guardian_name'],
                category=NotificationCategory.DEPARTURE, template_key=template_key, payload=payload,
                priority=NotificationPriority.HIGH, dedupe_key=f"pickup:{event.id}:{guardian.id}", immediate=True,
            )
            queued += 1
        except Exception as exc:  # noqa: BLE001 - the release is already committed; one bad recipient must not stop the rest
            # Type only: an exception message can carry the recipient's phone or email.
            logger.warning(
                "pickup notice failed for event %s guardian %s: %s", event.id, guardian.id, type(exc).__name__,
            )
    return queued


MAX_OVERRIDE_REASON_NOTICE_CHARS = 200


def notify_override_to_school_admins(event: PickupEvent) -> int:
    """Tell the school's admins (and the foundation's) that a student was released to an unauthorised person
    (ATT-018), so an override is reviewed and not only recorded. The admin who made the override is not told
    about their own action. Like the guardian notice, a failed delivery never undoes the release and its log
    line carries only the exception type. Returns how many notices were queued."""
    from apps.notifications.models import NotificationCategory, NotificationPriority
    from apps.notifications.services import dispatch_intent

    student = event.student
    school = event.school
    local_time = timezone.localtime(event.occurred_at)
    payload_base = {
        'type': NotificationCategory.PICKUP_OVERRIDE,
        'student_id': student.id,
        'student_name': student.person.full_name if student.person else 'Siswa',
        'school_name': school.name if school else 'Sekolah',
        'picked_up_by': event.picked_up_by,
        'actor_name': event.verified_by.full_name or 'Admin sekolah',
        'reason': event.override_reason[:MAX_OVERRIDE_REASON_NOTICE_CHARS],
        'time': local_time.strftime('%H:%M'),
        'date': local_time.strftime('%Y-%m-%d'),
    }
    queued = 0
    for recipient in get_school_admin_users(school):
        if recipient.id == event.verified_by_id:
            continue
        try:
            dispatch_intent(
                foundation_id=event.foundation_id, school_id=event.school_id, recipient_user=recipient,
                recipient_name=recipient.full_name or '', category=NotificationCategory.PICKUP_OVERRIDE,
                template_key='attendance.pickup_override_admin', payload=dict(payload_base),
                priority=NotificationPriority.HIGH, dedupe_key=f"pickup_override:{event.id}:{recipient.id}",
                immediate=True,
            )
            queued += 1
        except Exception as exc:  # noqa: BLE001 - the release is committed; one bad recipient must not stop the rest
            logger.warning(
                "pickup override notice failed for event %s user %s: %s", event.id, recipient.id, type(exc).__name__,
            )
    return queued
