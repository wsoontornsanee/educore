from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.fields import soft_delete_uniqueness_marker
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


class AttendanceStatus(models.TextChoices):
    HADIR = 'HADIR', _('Hadir (Present)')
    TERLAMBAT = 'TERLAMBAT', _('Terlambat (Late)')
    SAKIT = 'SAKIT', _('Sakit (Excused)')
    IZIN = 'IZIN', _('Izin (Permitted)')
    ALPA = 'ALPA', _('Alpa (Unexcused Absence)')
    DISPEN = 'DISPEN', _('Dispensasi (Sanctioned)')


class AttendanceSource(models.TextChoices):
    GATE = 'GATE', _('Gate Scan')
    TEACHER = 'TEACHER', _('Teacher / Class Attendance')
    SYSTEM = 'SYSTEM', _('System Default (Cutoff)')
    MANUAL = 'MANUAL', _('Manual Staff Override')


class GateDirection(models.TextChoices):
    IN = 'IN', _('Masuk (In)')
    OUT = 'OUT', _('Keluar (Out)')


class GateMethod(models.TextChoices):
    RFID = 'RFID', _('RFID / Smart Card')
    FACE = 'FACE', _('Face Recognition')
    MANUAL = 'MANUAL', _('Manual Check-in')
    KIOSK = 'KIOSK', _('Kiosk')


class GateEventStatus(models.TextChoices):
    ACCEPTED = 'ACCEPTED', _('Accepted')
    REJECTED = 'REJECTED', _('Rejected')


class AttendanceRule(TenantModel):
    """
    Per-school attendance timing and debouncing parameters (spec/05 §3, §4).
    """
    school = models.ForeignKey(
        'identity.School',
        on_delete=models.PROTECT,
        related_name='attendance_rules',
        help_text=_('School where this rule applies')
    )
    late_after_time = models.TimeField(
        default='07:15:00',
        help_text=_('Time after which an IN scan is marked TERLAMBAT instead of HADIR')
    )
    absent_cutoff_time = models.TimeField(
        default='09:00:00',
        help_text=_('Cutoff time after which students with no scan are marked ALPA')
    )
    debounce_seconds = models.PositiveIntegerField(
        default=120,
        help_text=_('Seconds within which duplicate scans are ignored for notification (ATT-007)')
    )
    active_uniq_marker = soft_delete_uniqueness_marker()

    class Meta(TenantModel.Meta):
        db_table = 'attendance_rules'
        verbose_name = _('Attendance Rule')
        verbose_name_plural = _('Attendance Rules')
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'school', 'active_uniq_marker'],
                name='unique_active_attendance_rule_per_school',)
        ]

    def __str__(self):
        return f"{self.school.name} Attendance Rules (Late after: {self.late_after_time})"


class AttendanceDay(TenantModel):
    """
    Daily attendance summary row per student per calendar date (spec/05 §2, §3).
    Automatically derived from gate scan events or manual/teacher entry.
    """
    school = models.ForeignKey(
        'identity.School',
        on_delete=models.PROTECT,
        related_name='attendance_days'
    )
    student = models.ForeignKey(
        'identity.Student',
        on_delete=models.PROTECT,
        related_name='attendance_days'
    )
    date = models.DateField(
        db_index=True
    )
    status = models.CharField(
        max_length=16,
        choices=AttendanceStatus.choices,
        default=AttendanceStatus.ALPA,
        db_index=True
    )
    first_in_at = models.DateTimeField(
        null=True,
        blank=True
    )
    last_out_at = models.DateTimeField(
        null=True,
        blank=True
    )
    source = models.CharField(
        max_length=32,
        choices=AttendanceSource.choices,
        default=AttendanceSource.GATE
    )
    note = models.CharField(
        max_length=255,
        blank=True,
        default=''
    )
    is_override = models.BooleanField(
        default=False,
        help_text=_('Whether this record was manually overridden by staff (ATT-003)')
    )
    original_status = models.CharField(
        max_length=16,
        blank=True,
        default='',
        help_text=_('Original derived status before staff override')
    )

    class Meta(TenantModel.Meta):
        db_table = 'attendance_days'
        verbose_name = _('Daily Attendance')
        verbose_name_plural = _('Daily Attendances')
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'school', 'student', 'date'],
                name='unique_student_attendance_per_date'
            )
        ]
        indexes = [
            models.Index(fields=['foundation_id', 'school', 'date', 'status'], name='idx_attday_fnd_sch_dt_st'),
            models.Index(fields=['foundation_id', 'student', 'date'], name='idx_attday_fnd_stu_dt'),
        ]

    def __str__(self):
        return f"{self.student.nis} - {self.date}: {self.status}"


class GateEvent(TenantModel):
    """
    Raw gate check-in/out scan event from hardware readers or kiosks (spec/05 §2, §4).
    Batched, idempotent, debounced per ATT-007, with offline replay tracking (ATT-012).
    """
    event_uuid = models.UUIDField(
        unique=True,
        db_index=True,
        help_text=_('Client-generated idempotent UUID from edge gateway (HW-005)')
    )
    school = models.ForeignKey(
        'identity.School',
        on_delete=models.PROTECT,
        related_name='gate_events'
    )
    device = models.ForeignKey(
        'hardware.Device',
        on_delete=models.PROTECT,
        related_name='gate_events'
    )
    student = models.ForeignKey(
        'identity.Student',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='gate_events'
    )
    staff = models.ForeignKey(
        'identity.Staff',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='gate_events'
    )
    credential = models.ForeignKey(
        'attendance.Credential',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='gate_events'
    )
    raw_uid = models.CharField(
        max_length=128,
        blank=True,
        default='',
        help_text=_('Scanned card UID or QR string from reader')
    )
    direction = models.CharField(
        max_length=16,
        choices=GateDirection.choices,
        default=GateDirection.IN
    )
    occurred_at = models.DateTimeField(
        db_index=True
    )
    method = models.CharField(
        max_length=16,
        choices=GateMethod.choices,
        default=GateMethod.RFID
    )
    confidence = models.DecimalField(
        max_digits=5,
        decimal_places=4,
        null=True,
        blank=True,
        help_text=_('Match confidence score for face recognition terminals (ATT-010)')
    )
    photo_key = models.CharField(
        max_length=255,
        blank=True,
        default=''
    )
    status = models.CharField(
        max_length=16,
        choices=GateEventStatus.choices,
        default=GateEventStatus.ACCEPTED,
        db_index=True
    )
    reject_reason = models.CharField(
        max_length=255,
        blank=True,
        default=''
    )
    is_duplicate_scan = models.BooleanField(
        default=False,
        help_text=_('Marked True if scanned within debounce window (ATT-007)')
    )
    replayed = models.BooleanField(
        default=False,
        help_text=_('Marked True if buffered offline and replayed on reconnect (ATT-012)')
    )

    class Meta(TenantModel.Meta):
        db_table = 'gate_events'
        verbose_name = _('Gate Event')
        verbose_name_plural = _('Gate Events')
        indexes = [
            models.Index(fields=['foundation_id', 'school', 'occurred_at'], name='idx_gtevt_fnd_sch_occ'),
            models.Index(fields=['foundation_id', 'student', 'occurred_at'], name='idx_gtevt_fnd_stu_occ'),
            models.Index(fields=['foundation_id', 'status'], name='idx_gtevt_fnd_st'),
        ]

    def __str__(self):
        holder = self.student.person.full_name if self.student else (self.staff.person.full_name if self.staff else self.raw_uid)
        return f"{self.direction} - {holder} @ {self.occurred_at.strftime('%Y-%m-%d %H:%M:%S')} [{self.status}]"


class PeriodAttendanceSource(models.TextChoices):
    TEACHER = 'TEACHER', _('Guru (Teacher)')
    GATE_PREFILL = 'GATE_PREFILL', _('Pra-isi dari Gerbang (Gate Pre-fill)')


class PeriodAttendance(TenantModel):
    """A student's attendance for one class period, submitted by the teacher (spec/09 TCH-002/003).

    Distinct from AttendanceDay (whole-day, gate-derived): this is per-timetable-slot,
    teacher-submitted, and may be pre-filled from that day's gate-derived status.
    """
    student = models.ForeignKey(
        'identity.Student', on_delete=models.PROTECT, related_name='period_attendances'
    )
    slot = models.ForeignKey(
        'academic.TimetableSlot', on_delete=models.PROTECT, related_name='period_attendances'
    )
    date = models.DateField(db_index=True)
    status = models.CharField(max_length=16, choices=AttendanceStatus.choices, default=AttendanceStatus.HADIR)
    source = models.CharField(max_length=16, choices=PeriodAttendanceSource.choices, default=PeriodAttendanceSource.TEACHER)
    note = models.CharField(max_length=255, blank=True, default='')
    recorded_by = models.ForeignKey(
        'identity.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    active_uniq_marker = soft_delete_uniqueness_marker()

    class Meta(TenantModel.Meta):
        db_table = 'period_attendances'
        verbose_name = _('Absensi Per Jam Pelajaran')
        verbose_name_plural = _('Absensi Per Jam Pelajaran')
        indexes = [
            models.Index(fields=['foundation_id', 'slot_id', 'date']),
            models.Index(fields=['foundation_id', 'student_id', 'date']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'student', 'slot', 'date', 'active_uniq_marker'],
                name='unique_period_attendance_per_student_slot_date',
            ),
        ]

    def __str__(self):
        return f"{self.student.nis} - {self.slot} @ {self.date} [{self.status}]"

