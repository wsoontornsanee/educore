from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.fields import MoneyField
from apps.core.models import TenantModel
from .crypto import decrypt_notes, encrypt_notes


class BehaviourCategory(models.TextChoices):
    POSITIVE = 'POSITIVE', _('Positif')
    MINOR = 'MINOR', _('Pelanggaran Ringan')
    MAJOR = 'MAJOR', _('Pelanggaran Berat')


class CaseStatus(models.TextChoices):
    OPEN = 'OPEN', _('Terbuka')
    IN_PROGRESS = 'IN_PROGRESS', _('Dalam Penanganan')
    RESOLVED = 'RESOLVED', _('Terselesaikan')
    CLOSED = 'CLOSED', _('Ditutup')


class BehaviourPolicy(TenantModel):
    """Per-school configuration for behaviour points and escalation (spec/10 §4)."""
    school = models.OneToOneField(
        'identity.School',
        on_delete=models.CASCADE,
        related_name='behaviour_policy',
    )
    escalation_negative_threshold = models.IntegerField(
        default=-25,
        help_text=_('Ambang batas akumulasi poin negatif per semester untuk auto-buka behaviour case (LIF-009).'),
    )
    default_counsellor = models.ForeignKey(
        'identity.Staff',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='counselor_behaviour_policies',
        help_text=_('Konselor BK default yang ditugaskan saat threshold terlampaui.'),
    )
    rapor_includes_behaviour = models.BooleanField(
        default=False,
        help_text=_('Apakah ringkasan poin perilaku dicantumkan di buku rapor (LIF-014).'),
    )

    class Meta:
        db_table = 'campus_behaviour_policies'
        verbose_name = _('Kebijakan Perilaku Sekolah')
        verbose_name_plural = _('Kebijakan Perilaku Sekolah')

    def __str__(self):
        return f"BehaviourPolicy(school_id={self.school_id}, threshold={self.escalation_negative_threshold})"


class BehaviourReason(TenantModel):
    """Configurable catalogue of behaviour reasons with point values (spec/10 §2, §4, spec/09 TCH-009)."""
    school = models.ForeignKey(
        'identity.School',
        on_delete=models.CASCADE,
        related_name='behaviour_reasons',
    )
    code = models.CharField(max_length=50)
    label = models.CharField(max_length=255)
    points = models.IntegerField(
        help_text=_('Nilai poin (positif untuk kebaikan, negatif untuk pelanggaran).'),
    )
    category = models.CharField(
        max_length=20,
        choices=BehaviourCategory.choices,
        default=BehaviourCategory.MINOR,
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'campus_behaviour_reasons'
        ordering = ['category', 'code']
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'school', 'code'],
                name='unique_school_behaviour_reason_code',
            ),
        ]

    def __str__(self):
        return f"[{self.code}] {self.label} ({self.points:+d})"


class BehaviourRecord(TenantModel):
    """Recorded student behaviour instance. Immutable / non-deletable (LIF-013)."""
    school = models.ForeignKey(
        'identity.School',
        on_delete=models.CASCADE,
        related_name='behaviour_records',
    )
    student = models.ForeignKey(
        'identity.Student',
        on_delete=models.CASCADE,
        related_name='behaviour_records',
    )
    term = models.ForeignKey(
        'academic.Term',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='behaviour_records',
        help_text=_('Semester akademik saat kejadian (LIF-008).'),
    )
    reason = models.ForeignKey(
        BehaviourReason,
        on_delete=models.PROTECT,
        related_name='records',
    )
    points = models.IntegerField(
        help_text=_('Snapshot nilai poin yang diterapkan.'),
    )
    note = models.TextField(blank=True, default='')
    occurred_at = models.DateTimeField(default=timezone.now)
    recorded_by = models.ForeignKey(
        'identity.User',
        on_delete=models.PROTECT,
        related_name='recorded_behaviour_records',
    )
    acknowledged_by_guardian_at = models.DateTimeField(null=True, blank=True)
    acknowledged_by = models.ForeignKey(
        'identity.Guardian',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='acknowledged_behaviour_records',
    )
    superseded_by = models.OneToOneField(
        'self',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='supersedes_record',
        help_text=_('Rekaman pengganti jika catatan ini dikoreksi (LIF-013).'),
    )
    is_superseded = models.BooleanField(default=False)
    correction_reason = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'campus_behaviour_records'
        ordering = ['-occurred_at', '-id']

    def delete(self, *args, **kwargs):
        raise PermissionDenied("Catatan perilaku tidak dapat dihapus secara fisik (LIF-013). Gunakan perbaikan/koreksi catatan.")

    def __str__(self):
        status_suffix = " [SUPERSEDED]" if self.is_superseded else ""
        return f"BehaviourRecord({self.student_id}, {self.reason.code}, {self.points:+d}{status_suffix})"


class BehaviourCase(TenantModel):
    """Escalated discipline/counselling case (spec/10 §2, §4, LIF-009)."""
    school = models.ForeignKey(
        'identity.School',
        on_delete=models.CASCADE,
        related_name='behaviour_cases',
    )
    student = models.ForeignKey(
        'identity.Student',
        on_delete=models.CASCADE,
        related_name='behaviour_cases',
    )
    term = models.ForeignKey(
        'academic.Term',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='behaviour_cases',
    )
    opened_at = models.DateTimeField(default=timezone.now)
    trigger = models.CharField(max_length=255)
    status = models.CharField(
        max_length=20,
        choices=CaseStatus.choices,
        default=CaseStatus.OPEN,
    )
    assigned_counsellor = models.ForeignKey(
        'identity.Staff',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='assigned_behaviour_cases',
    )
    resolution = models.TextField(blank=True, default='')
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'campus_behaviour_cases'
        ordering = ['-opened_at', '-id']

    def __str__(self):
        return f"BehaviourCase({self.student_id}, {self.status}, trigger={self.trigger})"


class LibraryItemType(models.TextChoices):
    BOOK = 'BOOK', _('Buku')
    EBOOK = 'EBOOK', _('E-Buku')
    EQUIPMENT = 'EQUIPMENT', _('Peralatan')


class LoanBorrowerType(models.TextChoices):
    STUDENT = 'STUDENT', _('Siswa')
    STAFF = 'STAFF', _('Staf')


class LoanStatus(models.TextChoices):
    ACTIVE = 'ACTIVE', _('Dipinjam')
    RETURNED = 'RETURNED', _('Dikembalikan')
    OVERDUE = 'OVERDUE', _('Terlambat')
    LOST = 'LOST', _('Hilang')


class LibraryItem(TenantModel):
    """Catalogued library book/e-book/equipment (spec/10 §2, §6 LIF-019).

    Scoped minimally for loan-due tracking + reminders only (this Open Item);
    checkout/barcode workflow, fines, and lost-item billing (LIF-020..023)
    belong to the separate, larger "Campus Life: E-Library & Asset
    Circulation" Open Item and will extend this model rather than replace it.
    """
    school = models.ForeignKey(
        'identity.School',
        on_delete=models.CASCADE,
        related_name='library_items',
    )
    type = models.CharField(max_length=16, choices=LibraryItemType.choices, default=LibraryItemType.BOOK, db_index=True)
    title = models.CharField(max_length=255)
    author = models.CharField(max_length=255, blank=True, default='')
    isbn = models.CharField(max_length=32, blank=True, default='')
    copies_total = models.PositiveIntegerField(default=1)
    copies_available = models.PositiveIntegerField(default=1)
    location = models.CharField(max_length=128, blank=True, default='')
    replacement_cost = MoneyField(
        default=Decimal('0.00'),
        help_text=_('Biaya penggantian jika hilang (LIF-021).'),
    )

    class Meta(TenantModel.Meta):
        db_table = 'campus_library_items'
        verbose_name = _('Item Perpustakaan')
        verbose_name_plural = _('Item Perpustakaan')
        indexes = [
            models.Index(fields=['foundation_id', 'school', 'type'], name='idx_libitem_fnd_sch_type'),
        ]

    def __str__(self):
        return f"{self.title} ({self.get_type_display()})"


class Loan(TenantModel):
    """Book/equipment loan (spec/10 §2, §6 LIF-019).

    No checkout/return API in this slice (Open Item non-goal) — loans are
    created/returned at the service layer; `remind_overdue_loans` sweeps
    ACTIVE loans past `due_at` for the digest reminder (spec/13 §3).
    """
    item = models.ForeignKey(
        LibraryItem,
        on_delete=models.PROTECT,
        related_name='loans',
    )
    borrower_type = models.CharField(max_length=16, choices=LoanBorrowerType.choices, db_index=True)
    borrower_id = models.BigIntegerField(help_text=_("Student.id or Staff.id"))
    borrowed_at = models.DateTimeField(default=timezone.now)
    due_at = models.DateTimeField(db_index=True)
    returned_at = models.DateTimeField(null=True, blank=True)
    fine = MoneyField(
        default=Decimal('0.00'),
        help_text=_('Denda keterlambatan, dihitung saat pengembalian (LIF-020).'),
    )
    status = models.CharField(max_length=16, choices=LoanStatus.choices, default=LoanStatus.ACTIVE, db_index=True)
    last_reminded_at = models.DateTimeField(null=True, blank=True)
    condition_on_issue = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text=_('Kondisi barang saat dipinjamkan, wajib untuk EQUIPMENT (LIF-022).'),
    )
    condition_on_return = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text=_('Kondisi barang saat dikembalikan, wajib untuk EQUIPMENT (LIF-022).'),
    )
    checked_out_by = models.ForeignKey(
        'identity.User',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='checked_out_library_loans',
    )

    class Meta(TenantModel.Meta):
        db_table = 'campus_library_loans'
        verbose_name = _('Peminjaman Perpustakaan')
        verbose_name_plural = _('Peminjaman Perpustakaan')
        ordering = ['-borrowed_at', '-id']
        indexes = [
            models.Index(fields=['foundation_id', 'status', 'due_at'], name='idx_loan_fnd_st_due'),
            models.Index(fields=['foundation_id', 'borrower_type', 'borrower_id'], name='idx_loan_fnd_borrower'),
        ]

    def __str__(self):
        return f"Loan({self.item_id}, {self.borrower_type}#{self.borrower_id}, {self.status})"


class CounsellingConfidentiality(models.TextChoices):
    NORMAL = 'NORMAL', _('Normal')
    RESTRICTED = 'RESTRICTED', _('Terbatas (Restricted)')


class CounsellingSessionType(models.TextChoices):
    INITIAL = 'INITIAL', _('Sesi Awal')
    FOLLOW_UP = 'FOLLOW_UP', _('Sesi Lanjutan')
    MEDIATION = 'MEDIATION', _('Mediasi')
    PARENT_MEETING = 'PARENT_MEETING', _('Pertemuan Orang Tua')
    OTHER = 'OTHER', _('Lainnya')


class CounsellingSession(TenantModel):
    """Guidance counselling (BK) session record (spec/10 §5, LIF-015 to LIF-018).

    `notes_encrypted` mirrors the `apps.hardware.BiometricTemplate` convention:
    ciphertext at rest via `apps.campus.crypto`, plaintext only ever handled
    in-memory through the `notes` property — never exposed by a model field a
    serializer could pick up by name.
    """
    school = models.ForeignKey(
        'identity.School',
        on_delete=models.CASCADE,
        related_name='counselling_sessions',
    )
    case = models.ForeignKey(
        BehaviourCase,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='counselling_sessions',
        help_text=_('Kasus perilaku terkait (opsional) — sesi BK dapat berdiri sendiri.'),
    )
    student = models.ForeignKey(
        'identity.Student',
        on_delete=models.CASCADE,
        related_name='counselling_sessions',
    )
    counsellor = models.ForeignKey(
        'identity.Staff',
        on_delete=models.PROTECT,
        related_name='counselling_sessions',
    )
    occurred_at = models.DateTimeField(default=timezone.now)
    type = models.CharField(
        max_length=20,
        choices=CounsellingSessionType.choices,
        default=CounsellingSessionType.INITIAL,
    )
    notes_encrypted = models.TextField(
        blank=True,
        default='',
        help_text=_('Catatan sesi, terenkripsi at-rest (LIF-015).'),
    )
    follow_up_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_('Tanggal tindak lanjut; memicu pengingat tugas konselor (LIF-018).'),
    )
    confidentiality = models.CharField(
        max_length=20,
        choices=CounsellingConfidentiality.choices,
        default=CounsellingConfidentiality.NORMAL,
        help_text=_('RESTRICTED hanya terlihat oleh konselor penulis dan kepala sekolah (LIF-015).'),
    )
    is_urgent = models.BooleanField(
        default=False,
        help_text=_('Eskalasi perlindungan anak (safeguarding) — memberitahu kepala sekolah langsung (LIF-017).'),
    )
    urgent_notified_at = models.DateTimeField(null=True, blank=True)
    follow_up_reminder_sent_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_('Kapan pengingat tugas tindak lanjut dikirim ke konselor (LIF-018).'),
    )

    class Meta:
        db_table = 'campus_counselling_sessions'
        ordering = ['-occurred_at', '-id']
        indexes = [
            models.Index(fields=['foundation_id', 'follow_up_at']),
        ]

    @property
    def notes(self) -> str:
        return decrypt_notes(self.notes_encrypted)

    @notes.setter
    def notes(self, value: str):
        self.notes_encrypted = encrypt_notes(value or '')

    def __str__(self):
        return f"CounsellingSession({self.student_id}, {self.type}, {self.confidentiality})"


class LibraryPolicy(TenantModel):
    """Per-school, per-borrower-type loan and fine configuration (LIF-019, LIF-020)."""
    school = models.ForeignKey(
        'identity.School',
        on_delete=models.CASCADE,
        related_name='library_policies',
    )
    borrower_type = models.CharField(max_length=16, choices=LoanBorrowerType.choices)
    loan_period_days = models.PositiveIntegerField(
        default=7,
        help_text=_('Lama masa pinjam dalam hari (LIF-019).'),
    )
    max_concurrent_loans = models.PositiveIntegerField(
        default=3,
        help_text=_('Jumlah maksimum pinjaman aktif bersamaan (LIF-019).'),
    )
    fine_per_day = MoneyField(
        default=Decimal('0.00'),
        help_text=_('Denda keterlambatan per hari (LIF-020).'),
    )
    fine_cap = MoneyField(
        default=Decimal('0.00'),
        help_text=_('Batas maksimum denda keterlambatan (LIF-020).'),
    )

    class Meta(TenantModel.Meta):
        db_table = 'campus_library_policies'
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'school', 'borrower_type'],
                name='unique_school_library_policy_borrower_type',
            ),
        ]

    def __str__(self):
        return f"LibraryPolicy(school_id={self.school_id}, {self.borrower_type})"


class ClinicOutcome(models.TextChoices):
    RETURNED_TO_CLASS = 'RETURNED_TO_CLASS', _('Kembali ke Kelas')
    SENT_HOME = 'SENT_HOME', _('Dipulangkan')
    REFERRED = 'REFERRED', _('Dirujuk')


class ClinicPolicy(TenantModel):
    """Per-school clinic configuration (spec/10 §3 LIF-006)."""
    school = models.OneToOneField(
        'identity.School',
        on_delete=models.CASCADE,
        related_name='clinic_policy',
    )
    teacher_sees_allergies = models.BooleanField(
        default=True,
        help_text=_('LIF-006: guru dapat melihat daftar alergi siswa pada ringkasan non-klinis (default aktif karena keselamatan).'),
    )

    class Meta:
        db_table = 'campus_clinic_policies'
        verbose_name = _('Kebijakan Klinik Sekolah')
        verbose_name_plural = _('Kebijakan Klinik Sekolah')

    def __str__(self):
        return f"ClinicPolicy(school_id={self.school_id}, teacher_sees_allergies={self.teacher_sees_allergies})"


class HealthProfile(TenantModel):
    """Student medical profile, surfaced above the fold on every clinic visit (LIF-002)."""
    student = models.OneToOneField(
        'identity.Student',
        on_delete=models.CASCADE,
        related_name='health_profile',
    )
    blood_type = models.CharField(max_length=8, blank=True, default='')
    allergies = models.JSONField(default=list, blank=True)
    chronic_conditions = models.JSONField(default=list, blank=True)
    medications = models.JSONField(default=list, blank=True)
    emergency_contacts = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'campus_health_profiles'
        verbose_name = _('Profil Kesehatan Siswa')
        verbose_name_plural = _('Profil Kesehatan Siswa')

    @property
    def has_medical_alert(self) -> bool:
        return bool(self.allergies or self.chronic_conditions or self.medications)

    def __str__(self):
        return f"HealthProfile(student_id={self.student_id})"


class MedicationStock(TenantModel):
    """UKS medication/first-aid inventory (spec/10 §2, LIF-004, LIF-005)."""
    school = models.ForeignKey(
        'identity.School',
        on_delete=models.CASCADE,
        related_name='medication_stocks',
    )
    name = models.CharField(max_length=255)
    unit = models.CharField(max_length=32)
    quantity = models.IntegerField(default=0)
    expiry_date = models.DateField()
    reorder_level = models.IntegerField(default=0)

    class Meta:
        db_table = 'campus_medication_stock'
        ordering = ['name']

    def __str__(self):
        return f"MedicationStock({self.name}, qty={self.quantity})"


class ClinicVisit(TenantModel):
    """A single UKS clinic visit encounter (spec/10 §2, §3, LIF-001..007)."""
    school = models.ForeignKey(
        'identity.School',
        on_delete=models.CASCADE,
        related_name='clinic_visits',
    )
    student = models.ForeignKey(
        'identity.Student',
        on_delete=models.CASCADE,
        related_name='clinic_visits',
    )
    occurred_at = models.DateTimeField(default=timezone.now)
    complaint_encrypted = models.TextField(
        help_text=_('LIF-007: keluhan terenkripsi Fernet, lihat apps.campus.crypto.'),
    )
    treatment_encrypted = models.TextField(
        blank=True, default='',
        help_text=_('LIF-007: penanganan terenkripsi Fernet, lihat apps.campus.crypto.'),
    )
    vitals = models.JSONField(default=dict, blank=True)
    medication_given = models.ForeignKey(
        MedicationStock,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='clinic_visits',
    )
    medication_quantity_used = models.IntegerField(null=True, blank=True)
    outcome = models.CharField(max_length=20, choices=ClinicOutcome.choices)
    handled_by = models.ForeignKey(
        'identity.Staff',
        on_delete=models.PROTECT,
        related_name='handled_clinic_visits',
    )
    guardian_consent_confirmed = models.BooleanField(default=False)
    guardian_consent_note = models.CharField(max_length=255, blank=True, default='')
    guardian_notified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'campus_clinic_visits'
        ordering = ['-occurred_at', '-id']
        indexes = [
            models.Index(fields=['foundation_id', 'school_id', 'occurred_at']),
            models.Index(fields=['foundation_id', 'student_id', 'occurred_at']),
        ]

    def __str__(self):
        return f"ClinicVisit(student_id={self.student_id}, outcome={self.outcome})"
