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
