from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantModel


class CredentialType(models.TextChoices):
    RFID = 'RFID', _('RFID (MIFARE DESFire)')
    NFC = 'NFC', _('NFC')
    QR = 'QR', _('QR Code Fallback')


class CredentialStatus(models.TextChoices):
    ACTIVE = 'ACTIVE', _('Active')
    REVOKED = 'REVOKED', _('Revoked')
    EXPIRED = 'EXPIRED', _('Expired')


class Credential(TenantModel):
    """
    Physical smart cards (RFID/NFC) and temporary QR fallback credentials
    bound to students or staff members (spec/05 §2, spec/12 §5).
    """
    student = models.ForeignKey(
        'identity.Student',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='credentials',
        help_text=_('Linked student (if issued to a student)')
    )
    staff = models.ForeignKey(
        'identity.Staff',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='credentials',
        help_text=_('Linked staff member (if issued to a staff member)')
    )
    type = models.CharField(
        max_length=16,
        choices=CredentialType.choices,
        default=CredentialType.RFID,
        db_index=True
    )
    uid = models.CharField(
        max_length=128,
        db_index=True,
        help_text=_('Card UID hex string or QR cryptographic payload token')
    )
    card_number = models.CharField(
        max_length=64,
        blank=True,
        default='',
        help_text=_('Human-readable printed card identifier')
    )
    status = models.CharField(
        max_length=16,
        choices=CredentialStatus.choices,
        default=CredentialStatus.ACTIVE,
        db_index=True
    )
    issued_at = models.DateTimeField(
        default=timezone.now
    )
    revoked_at = models.DateTimeField(
        null=True,
        blank=True
    )
    revoked_reason = models.CharField(
        max_length=255,
        blank=True,
        default=''
    )
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_('Expiry timestamp for temporary credentials (HW-020: <=15 min QR)')
    )
    is_used = models.BooleanField(
        default=False,
        help_text=_('Single-use flag for temporary QR fallback credentials')
    )
    replacement_fee_posted = models.BooleanField(
        default=False,
        help_text=_('Flag indicating replacement fee was posted to invoice (HW-018)')
    )

    class Meta(TenantModel.Meta):
        db_table = 'credentials'
        verbose_name = _('Credential')
        verbose_name_plural = _('Credentials')
        indexes = [
            models.Index(fields=['foundation_id', 'uid'], name='idx_cred_fnd_uid'),
            models.Index(fields=['foundation_id', 'student', 'status'], name='idx_cred_fnd_stu_st'),
            models.Index(fields=['foundation_id', 'staff', 'status'], name='idx_cred_fnd_stf_st'),
            models.Index(fields=['foundation_id', 'type', 'status'], name='idx_cred_fnd_type_st'),
        ]

    def clean(self):
        super().clean()
        if not self.student and not self.staff:
            raise ValidationError(_("Credential must be bound to either a student or a staff member."))
        if self.student and self.staff:
            raise ValidationError(_("Credential cannot be simultaneously bound to both student and staff."))

    @property
    def holder_type(self) -> str:
        if self.student_id:
            return 'student'
        if self.staff_id:
            return 'staff'
        return 'unknown'

    @property
    def holder_name(self) -> str:
        if self.student and self.student.person:
            return self.student.person.full_name
        if self.staff and self.staff.person:
            return self.staff.person.full_name
        return ''

    @property
    def is_valid_now(self) -> bool:
        if self.status != CredentialStatus.ACTIVE:
            return False
        if self.type == CredentialType.QR:
            if self.is_used:
                return False
            if self.expires_at and timezone.now() > self.expires_at:
                return False
        return True

    def __str__(self):
        return f"{self.type} ({self.uid}) - {self.holder_name} [{self.status}]"
