from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.archiving import build_archive_model
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


GateEventArchive = build_archive_model(GateEvent)  # NFR-009


class PeriodAttendanceSource(models.TextChoices):
    TEACHER = 'TEACHER', _('Guru (Teacher)')
    GATE_PREFILL = 'GATE_PREFILL', _('Pra-isi dari Gerbang (Gate Pre-fill)')
    MANUAL = 'MANUAL', _('Override Manual (mis. Klinik/UKS)')


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


class AbsenceType(models.TextChoices):
    SAKIT = 'SAKIT', _('Sakit (Sick / Medical)')
    IZIN = 'IZIN', _('Izin (Permitted)')


class AbsenceRequestStatus(models.TextChoices):
    PENDING = 'PENDING', _('Menunggu (Pending)')
    APPROVED = 'APPROVED', _('Disetujui (Approved)')
    REJECTED = 'REJECTED', _('Ditolak (Rejected)')


class AbsenceRequest(TenantModel):
    """
    Parent-submitted absence request with photo attachment (spec/05 §2, spec/08 PAR-011).
    Upon approval by school staff, automatically updates daily attendance status
    for the covered date range to SAKIT or IZIN (spec/05 ATT-002).
    """
    school = models.ForeignKey(
        'identity.School',
        on_delete=models.PROTECT,
        related_name='absence_requests',
        help_text=_('Sekolah tempat siswa terdaftar')
    )
    student = models.ForeignKey(
        'identity.Student',
        on_delete=models.PROTECT,
        related_name='absence_requests',
        help_text=_('Siswa yang dimohonkan izin/sakit')
    )
    requested_by = models.ForeignKey(
        'identity.User',
        on_delete=models.PROTECT,
        related_name='submitted_absence_requests',
        help_text=_('Orang tua / wali yang mengajukan permohonan')
    )
    date_from = models.DateField(
        db_index=True,
        help_text=_('Tanggal mulai ketidakhadiran')
    )
    date_to = models.DateField(
        db_index=True,
        help_text=_('Tanggal akhir ketidakhadiran (inklusif)')
    )
    type = models.CharField(
        max_length=16,
        choices=AbsenceType.choices,
        default=AbsenceType.SAKIT,
        db_index=True
    )
    reason = models.TextField(
        help_text=_('Alasan pengajuan izin atau keterangan sakit')
    )
    attachment_key = models.CharField(
        max_length=500,
        blank=True,
        default='',
        help_text=_('Kunci StoredFile untuk berkas lampiran foto surat dokter/izin')
    )
    status = models.CharField(
        max_length=16,
        choices=AbsenceRequestStatus.choices,
        default=AbsenceRequestStatus.PENDING,
        db_index=True
    )
    decided_by = models.ForeignKey(
        'identity.User',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='decided_absence_requests',
        help_text=_('Staf yang memproses persetujuan atau penolakan')
    )
    decided_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_('Waktu keputusan diproses')
    )
    decision_note = models.TextField(
        blank=True,
        default='',
        help_text=_('Catatan keputusan dari staf')
    )

    class Meta(TenantModel.Meta):
        db_table = 'absence_requests'
        verbose_name = _('Permohonan Izin / Sakit')
        verbose_name_plural = _('Permohonan Izin / Sakit')
        indexes = [
            models.Index(fields=['foundation_id', 'student', 'status'], name='idx_absreq_fnd_stu_st'),
            models.Index(fields=['foundation_id', 'school', 'status'], name='idx_absreq_fnd_sch_st'),
            models.Index(fields=['foundation_id', 'date_from', 'date_to'], name='idx_absreq_fnd_dt'),
        ]

    def __str__(self):
        return f"{self.type} - {self.student.nis} ({self.date_from} s/d {self.date_to}) [{self.status}]"



class PickupMethod(models.TextChoices):
    QR = 'QR', _('QR penjemputan (QR pickup)')
    GUARDIAN = 'GUARDIAN', _('Wali terdaftar (Registered guardian)')
    OVERRIDE = 'OVERRIDE', _('Pengecualian admin sekolah (School admin override)')


class PickupAuthorization(TenantModel):
    """A guardian's authorisation for one named person to collect one student (spec/05 §5, ATT-014, ATT-015).

    Created by a guardian of the student who may pick up; it is proven at the gate by a signed QR token whose
    only content is this row's id, so revoking or using the row invalidates every copy of the QR. The window
    (`valid_from`..`valid_to`) is checked in the database, never trusted from the token. A `one_time`
    authorisation is spent by the first completed pickup. `person_name`, `phone` and `photo_key` are PII of a
    third party: they are shown to staff at the gate and never written to logs or audit diffs.
    """
    school = models.ForeignKey('identity.School', on_delete=models.PROTECT, related_name='pickup_authorizations')
    student = models.ForeignKey('identity.Student', on_delete=models.PROTECT, related_name='pickup_authorizations')
    created_by_guardian = models.ForeignKey(
        'identity.Guardian', on_delete=models.PROTECT, related_name='pickup_authorizations',
    )
    person_name = models.CharField(max_length=120)
    relation = models.CharField(max_length=64, blank=True, default='')
    phone = models.CharField(max_length=32, blank=True, default='')
    photo_key = models.CharField(max_length=500, blank=True, default='')
    valid_from = models.DateTimeField()
    valid_to = models.DateTimeField(db_index=True)
    one_time = models.BooleanField(default=True)
    used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        'identity.User', null=True, blank=True, on_delete=models.PROTECT, related_name='+',
    )

    class Meta(TenantModel.Meta):
        db_table = 'pickup_authorizations'
        indexes = [
            models.Index(fields=['foundation_id', 'student', 'valid_to'], name='idx_pickauth_fnd_stu_to'),
            models.Index(fields=['foundation_id', 'school', 'valid_to'], name='idx_pickauth_fnd_sch_to'),
        ]

    def __str__(self):
        return f"Pickup authorisation #{self.pk} for student {self.student_id} ({self.valid_from:%Y-%m-%d %H:%M} - {self.valid_to:%Y-%m-%d %H:%M})"


class PickupEvent(TenantModel):
    """One completed release of a student to a person (spec/05 §5, ATT-016..ATT-018).

    `verified_by` is the staff member who checked the person at the gate. `method` says why the release was
    allowed: a valid QR authorisation, a registered guardian with `can_pickup`, or a school-admin override
    (then `override_reason` is mandatory). `picked_up_by` is a name, snapshotted so the record survives edits
    to the authorisation or guardian.
    """
    school = models.ForeignKey('identity.School', on_delete=models.PROTECT, related_name='pickup_events')
    student = models.ForeignKey('identity.Student', on_delete=models.PROTECT, related_name='pickup_events')
    authorization = models.ForeignKey(
        PickupAuthorization, null=True, blank=True, on_delete=models.PROTECT, related_name='events',
    )
    authorized_by_guardian = models.ForeignKey(
        'identity.Guardian', null=True, blank=True, on_delete=models.PROTECT, related_name='+',
    )
    picked_up_by = models.CharField(max_length=120)
    verified_by = models.ForeignKey('identity.User', on_delete=models.PROTECT, related_name='+')
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    method = models.CharField(max_length=16, choices=PickupMethod.choices, db_index=True)
    override_reason = models.TextField(blank=True, default='')

    class Meta(TenantModel.Meta):
        db_table = 'pickup_events'
        indexes = [
            models.Index(fields=['foundation_id', 'school', 'occurred_at'], name='idx_pickev_fnd_sch_at'),
            models.Index(fields=['foundation_id', 'student', 'occurred_at'], name='idx_pickev_fnd_stu_at'),
        ]

    def __str__(self):
        return f"Pickup event #{self.pk} student {self.student_id} via {self.method}"


# ── School transport (spec/05 §6, ATT-019..ATT-022) ────────────────────────────────────────────────────

class BusRoute(TenantModel):
    """A school bus route. `approach_minutes` is how far out from a student's stop the guardians are told the
    bus is coming (ATT-020, "~5 minutes, configurable")."""
    school = models.ForeignKey('identity.School', on_delete=models.PROTECT, related_name='bus_routes')
    name = models.CharField(max_length=120)
    approach_minutes = models.PositiveSmallIntegerField(default=5)
    is_active = models.BooleanField(default=True)
    active_uniq_marker = soft_delete_uniqueness_marker()

    class Meta(TenantModel.Meta):
        db_table = 'bus_routes'
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'school', 'name', 'active_uniq_marker'], name='unique_active_bus_route_name',
            ),
        ]
        indexes = [models.Index(fields=['foundation_id', 'school', 'is_active'], name='idx_busroute_fnd_sch_act')]

    def __str__(self):
        return f"{self.name} (school {self.school_id})"


class BusStop(TenantModel):
    """A pick-up / drop-off point on a route. `radius_m` is the geofence: a bus inside it has arrived."""
    route = models.ForeignKey(BusRoute, on_delete=models.PROTECT, related_name='stops')
    name = models.CharField(max_length=120)
    sequence = models.PositiveSmallIntegerField(default=0)
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)
    radius_m = models.PositiveIntegerField(default=150)

    class Meta(TenantModel.Meta):
        db_table = 'bus_stops'
        ordering = ['sequence', 'id']
        indexes = [models.Index(fields=['foundation_id', 'route', 'sequence'], name='idx_busstop_fnd_rt_seq')]

    def __str__(self):
        return f"{self.name} (route {self.route_id})"


class BusStopAssignment(TenantModel):
    """The stop where a student boards and alights. A student has one stop per route."""
    route = models.ForeignKey(BusRoute, on_delete=models.PROTECT, related_name='assignments')
    stop = models.ForeignKey(BusStop, on_delete=models.PROTECT, related_name='assignments')
    student = models.ForeignKey('identity.Student', on_delete=models.PROTECT, related_name='bus_assignments')
    active_uniq_marker = soft_delete_uniqueness_marker()

    class Meta(TenantModel.Meta):
        db_table = 'bus_stop_assignments'
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'route', 'student', 'active_uniq_marker'],
                name='unique_active_bus_assignment_per_route',
            ),
        ]
        indexes = [
            models.Index(fields=['foundation_id', 'student'], name='idx_busasg_fnd_stu'),
            models.Index(fields=['foundation_id', 'stop'], name='idx_busasg_fnd_stop'),
        ]

    def __str__(self):
        return f"Student {self.student_id} at stop {self.stop_id}"


class BusRunDirection(models.TextChoices):
    TO_SCHOOL = 'TO_SCHOOL', _('Berangkat ke sekolah (To school)')
    FROM_SCHOOL = 'FROM_SCHOOL', _('Pulang dari sekolah (From school)')


class BusRunStatus(models.TextChoices):
    ACTIVE = 'ACTIVE', _('Berjalan (Active)')
    COMPLETED = 'COMPLETED', _('Selesai (Completed)')


class BusRun(TenantModel):
    """One trip of a route. While ACTIVE the driver's handheld reports the bus position (`last_*`), which drives
    the geofence notices and the guardians' live view. `announced_stop_ids` are the stops whose guardians were
    already told the bus is near; `unaccounted_checked_at` is set once the route-end check has run (ATT-021),
    so a repeated check never alerts twice."""
    school = models.ForeignKey('identity.School', on_delete=models.PROTECT, related_name='bus_runs')
    route = models.ForeignKey(BusRoute, on_delete=models.PROTECT, related_name='runs')
    direction = models.CharField(max_length=16, choices=BusRunDirection.choices)
    status = models.CharField(max_length=16, choices=BusRunStatus.choices, default=BusRunStatus.ACTIVE, db_index=True)
    started_at = models.DateTimeField(default=timezone.now)
    ended_at = models.DateTimeField(null=True, blank=True)
    started_by = models.ForeignKey('identity.User', null=True, on_delete=models.PROTECT, related_name='+')
    ended_by = models.ForeignKey('identity.User', null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    last_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    last_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    last_speed_mps = models.FloatField(null=True, blank=True)
    last_position_at = models.DateTimeField(null=True, blank=True)
    announced_stop_ids = models.JSONField(default=list, blank=True)
    unaccounted_checked_at = models.DateTimeField(null=True, blank=True)

    class Meta(TenantModel.Meta):
        db_table = 'bus_runs'
        indexes = [
            models.Index(fields=['foundation_id', 'route', 'status'], name='idx_busrun_fnd_rt_st'),
            models.Index(fields=['foundation_id', 'status', 'unaccounted_checked_at'], name='idx_busrun_fnd_st_chk'),
        ]

    def __str__(self):
        return f"Run #{self.pk} of route {self.route_id} ({self.status})"


class BusEventKind(models.TextChoices):
    BOARD = 'BOARD', _('Naik (Board)')
    ALIGHT = 'ALIGHT', _('Turun (Alight)')


class BusBoardingEvent(TenantModel):
    """A student boarding or alighting, tapped on the driver's handheld and geotagged (ATT-019). Idempotent on
    `event_uuid` (HW-005); a replayed offline event keeps its original `occurred_at`."""
    event_uuid = models.UUIDField(unique=True)
    school = models.ForeignKey('identity.School', on_delete=models.PROTECT, related_name='bus_events')
    run = models.ForeignKey(BusRun, on_delete=models.PROTECT, related_name='events')
    student = models.ForeignKey('identity.Student', on_delete=models.PROTECT, related_name='bus_events')
    kind = models.CharField(max_length=8, choices=BusEventKind.choices)
    occurred_at = models.DateTimeField(db_index=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    device = models.ForeignKey('hardware.Device', null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    replayed = models.BooleanField(default=False)

    class Meta(TenantModel.Meta):
        db_table = 'bus_boarding_events'
        indexes = [models.Index(fields=['foundation_id', 'run', 'student', 'occurred_at'], name='idx_busev_fnd_run_stu_at')]

    def __str__(self):
        return f"{self.kind} student {self.student_id} run {self.run_id}"
