"""Identity and Tenancy models (spec/02, spec/03).

Defines:
- Foundation: root multi-tenant entity
- School: educational operating unit
- User: custom auth model supporting phone and email authentication
- Person: PII vault isolated for UU PDP compliance
- OTPChallenge: 6-digit WhatsApp/SMS OTP challenges
"""
import logging
import re
import uuid
from datetime import timedelta
from decimal import Decimal
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from apps.core.fields import CoordinateField, MoneyField, soft_delete_uniqueness_marker
from apps.core.models import TenantModel
from .managers import UserManager, AllUsersManager

logger = logging.getLogger(__name__)

class Foundation(models.Model):
    """The governing foundation (Yayasan) managing one or more schools (spec/02 §2, spec/03)."""
    PLAN_STARTER = 'STARTER'
    PLAN_STANDARD = 'STANDARD'
    PLAN_ENTERPRISE = 'ENTERPRISE'
    PLAN_CHOICES = [
        (PLAN_STARTER, 'Starter'),
        (PLAN_STANDARD, 'Standard'),
        (PLAN_ENTERPRISE, 'Enterprise'),
    ]

    STATUS_ACTIVE = 'ACTIVE'
    STATUS_SUSPENDED = 'SUSPENDED'
    STATUS_INACTIVE = 'INACTIVE'
    STATUS_CHOICES = [
        (STATUS_ACTIVE, 'Active'),
        (STATUS_SUSPENDED, 'Suspended'),
        (STATUS_INACTIVE, 'Inactive'),
    ]

    id = models.BigAutoField(primary_key=True)
    legal_name = models.CharField(max_length=255, help_text="Legal registered name, e.g. Yayasan Pendidikan Islam Al-Hikmah")
    brand_name = models.CharField(max_length=128, help_text="Public-facing brand name, e.g. Al-Hikmah Nusantara")
    npwp = models.CharField(max_length=32, blank=True, default='', help_text="Nomor Pokok Wajib Pajak")
    address = models.TextField(blank=True, default='', help_text="Official registered address")
    timezone = models.CharField(max_length=32, default='Asia/Jakarta', help_text="Default timezone: Asia/Jakarta, Asia/Makassar, or Asia/Jayapura")
    reporting_currency = models.CharField(max_length=3, default='IDR', help_text="Currency for consolidated reporting (CUR-007)")
    approval_threshold = MoneyField(
        default=Decimal('1000000.00'),
        help_text="Nominal ambang batas persetujuan yayasan untuk diskon, keringanan, dan refund (FND-007, FIN-007, FIN-032)",
    )
    plan_tier = models.CharField(max_length=32, choices=PLAN_CHOICES, default=PLAN_STANDARD)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_ACTIVE, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'foundations'
        verbose_name = 'Yayasan'
        verbose_name_plural = 'Daftar Yayasan'

    def __str__(self):
        return f"{self.brand_name} ({self.legal_name})"

class School(TenantModel):
    """An educational operating unit (school/madrasah) belonging to a Foundation (spec/02 §2)."""
    LEVEL_SD = 'SD'
    LEVEL_SMP = 'SMP'
    LEVEL_SMA = 'SMA'
    LEVEL_SMK = 'SMK'
    LEVEL_MI = 'MI'
    LEVEL_MTS = 'MTs'
    LEVEL_MA = 'MA'

    LEVEL_CHOICES = [
        (LEVEL_SD, 'SD (Sekolah Dasar)'),
        (LEVEL_SMP, 'SMP (Sekolah Menengah Pertama)'),
        (LEVEL_SMA, 'SMA (Sekolah Menengah Atas)'),
        (LEVEL_SMK, 'SMK (Sekolah Menengah Kejuruan)'),
        (LEVEL_MI, 'MI (Madrasah Ibtidaiyah)'),
        (LEVEL_MTS, 'MTs (Madrasah Tsanawiyah)'),
        (LEVEL_MA, 'MA (Madrasah Aliyah)'),
    ]

    name = models.CharField(max_length=128, help_text="e.g. SMP Al-Hikmah Nusantara")
    npsn = models.CharField(max_length=16, unique=True, db_index=True, help_text="Nomor Pokok Sekolah Nasional (8 digits)")
    level = models.CharField(max_length=16, choices=LEVEL_CHOICES)
    curriculum = models.CharField(max_length=64, default='KURIKULUM_MERDEKA')
    timezone = models.CharField(max_length=32, default='Asia/Jakarta')
    base_currency = models.CharField(max_length=3, default='IDR', help_text="School base currency (CUR-007)")
    is_active = models.BooleanField(default=True)

    # DAPODIK/EMIS statutory fields (CMP-017, CMP-019). Blank-optional:
    # the pre-export validation report flags them, entry is never blocked.
    OWNERSHIP_NEGERI = 'NEGERI'
    OWNERSHIP_SWASTA = 'SWASTA'
    OWNERSHIP_CHOICES = [
        (OWNERSHIP_NEGERI, 'Negeri (Public)'),
        (OWNERSHIP_SWASTA, 'Swasta (Private)'),
    ]
    nss = models.CharField(max_length=16, blank=True, default='', help_text="Nomor Statistik Sekolah (DAPODIK)")
    nsm = models.CharField(max_length=16, blank=True, default='', help_text="Nomor Statistik Madrasah (EMIS, CMP-019)")
    ownership_status = models.CharField(max_length=16, choices=OWNERSHIP_CHOICES, blank=True, default='', help_text="Status kepemilikan (negeri/swasta)")
    ACCREDITATION_CHOICES = [
        ('A', 'A'), ('B', 'B'), ('C', 'C'),
        ('TIDAK_TERAKREDITASI', 'Belum Terakreditasi'),
    ]
    accreditation = models.CharField(max_length=19, choices=ACCREDITATION_CHOICES, blank=True, default='', help_text="Hasil akreditasi (A/B/C)")
    establishment_date = models.DateField(null=True, blank=True, help_text="Tanggal pendirian sekolah")

    # Structured school address (DAPODIK profile block)
    street_address = models.TextField(blank=True, default='', help_text="Alamat jalan sekolah")
    kelurahan = models.CharField(max_length=64, blank=True, default='', help_text="Kelurahan / Desa")
    kecamatan = models.CharField(max_length=64, blank=True, default='', help_text="Kecamatan")
    kabupaten_kota = models.CharField(max_length=64, blank=True, default='', help_text="Kabupaten / Kota")
    provinsi = models.CharField(max_length=64, blank=True, default='', help_text="Provinsi")
    postal_code = models.CharField(max_length=10, blank=True, default='', help_text="Kode pos")

    class Meta:
        db_table = 'schools'
        verbose_name = 'Sekolah'
        verbose_name_plural = 'Daftar Sekolah'
        indexes = [
            models.Index(fields=['foundation_id', 'level']),
            models.Index(fields=['foundation_id', 'is_active']),
        ]

    def __str__(self):
        return f"{self.name} (NPSN: {self.npsn})"

class Person(TenantModel):
    """PII Vault table isolating personal identity data for UU PDP compliance (spec/02 §2, spec/14 §4).

    Identity-bearing PII (NIK, DOB, address) lives only in persons.
    Other tables reference person_id.

    DAPODIK/EMIS statutory fields (CMP-017) are blank-optional: the
    pre-export validation report flags them, entry is never blocked.
    """
    GENDER_MALE = 'L'
    GENDER_FEMALE = 'P'
    GENDER_CHOICES = [
        (GENDER_MALE, 'Laki-laki'),
        (GENDER_FEMALE, 'Perempuan'),
    ]

    RELIGION_ISLAM = 'ISLAM'
    RELIGION_KRISTEN = 'KRISTEN'
    RELIGION_KATOLIK = 'KATOLIK'
    RELIGION_HINDU = 'HINDU'
    RELIGION_BUDDHA = 'BUDDHA'
    RELIGION_KONGHUCU = 'KONGHUCU'
    RELIGION_CHOICES = [
        (RELIGION_ISLAM, 'Islam'),
        (RELIGION_KRISTEN, 'Kristen'),
        (RELIGION_KATOLIK, 'Katolik'),
        (RELIGION_HINDU, 'Hindu'),
        (RELIGION_BUDDHA, 'Buddha'),
        (RELIGION_KONGHUCU, 'Konghucu'),
    ]

    nik = models.CharField(max_length=16, blank=True, null=True, db_index=True, help_text="Nomor Induk Kependudukan (16 digits)")
    full_name = models.CharField(max_length=128, help_text="Full legal name per birth certificate / KTP")
    dob = models.DateField(null=True, blank=True, help_text="Tanggal lahir")
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES, blank=True, default='')
    address = models.TextField(blank=True, default='', help_text="Alamat domisili lengkap")

    # DAPODIK/EMIS statutory fields (CMP-017)
    religion = models.CharField(
        max_length=16, choices=RELIGION_CHOICES, blank=True, default='',
        help_text="Agama (DAPODIK/EMIS statutory field, CMP-017)"
    )
    birth_city = models.CharField(max_length=64, blank=True, default='', help_text="Tempat lahir (kota/kabupaten)")
    birth_certificate_number = models.CharField(
        max_length=64, blank=True, default='',
        help_text="Nomor Akta Kelahiran (DAPODIK/EMIS statutory field)"
    )
    mother_name = models.CharField(
        max_length=128, blank=True, default='',
        help_text="Nama ibu kandung (DAPODIK/EMIS statutory field)"
    )
    citizenship = models.CharField(
        max_length=32, blank=True, default='WNI',
        help_text="Kewarganegaraan (default WNI)"
    )

    # Structured address (DAPODIK requires the breakdown; `address` stays as
    # the free-text jalan/line. All optional — validation flags incompleteness.)
    rt = models.CharField(max_length=8, blank=True, default='', help_text="Rukun Tetangga")
    rw = models.CharField(max_length=8, blank=True, default='', help_text="Rukun Warga")
    dusun = models.CharField(max_length=64, blank=True, default='', help_text="Dusun / Kampung")
    kelurahan = models.CharField(max_length=64, blank=True, default='', help_text="Kelurahan / Desa")
    kecamatan = models.CharField(max_length=64, blank=True, default='', help_text="Kecamatan")
    kabupaten_kota = models.CharField(max_length=64, blank=True, default='', help_text="Kabupaten / Kota")
    provinsi = models.CharField(max_length=64, blank=True, default='', help_text="Provinsi")
    postal_code = models.CharField(max_length=10, blank=True, default='', help_text="Kode pos")
    home_latitude = CoordinateField(
        null=True, blank=True,
        validators=[
            MinValueValidator(Decimal('-90.000000')),
            MaxValueValidator(Decimal('90.000000')),
        ],
        help_text="Lintang koordinat tempat tinggal (-90 s/d +90)",
    )
    home_longitude = CoordinateField(
        null=True, blank=True,
        validators=[
            MinValueValidator(Decimal('-180.000000')),
            MaxValueValidator(Decimal('180.000000')),
        ],
        help_text="Bujur koordinat tempat tinggal (-180 s/d +180)",
    )

    class Meta:
        db_table = 'persons'
        verbose_name = 'Data Pribadi (PII)'
        verbose_name_plural = 'Data Pribadi (PII)'
        indexes = [
            models.Index(fields=['foundation_id', 'nik']),
        ]

    def __str__(self):
        return f"{self.full_name} (NIK: {self.nik or '-'})"

class User(AbstractBaseUser, PermissionsMixin, TenantModel):
    """Custom User model supporting dual phone E.164 and email authentication (spec/02 §2, §3)."""
    STATUS_ACTIVE = 'ACTIVE'
    STATUS_SUSPENDED = 'SUSPENDED'
    STATUS_INACTIVE = 'INACTIVE'
    STATUS_CHOICES = [
        (STATUS_ACTIVE, 'Active'),
        (STATUS_SUSPENDED, 'Suspended'),
        (STATUS_INACTIVE, 'Inactive'),
    ]

    phone_e164 = models.CharField(max_length=20, unique=True, db_index=True, help_text="Nomor HP format E.164 (+62...)")
    email = models.EmailField(max_length=255, unique=True, null=True, blank=True, db_index=True)
    full_name = models.CharField(max_length=128)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_ACTIVE, db_index=True)
    mfa_secret = models.CharField(max_length=64, blank=True, default='', help_text="Base32 TOTP secret for staff MFA")
    
    # Account lockout (IAM-008: 10 failed attempts in 15 min locks account)
    failed_login_attempts = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()
    all_tenants = AllUsersManager()

    USERNAME_FIELD = 'phone_e164'
    REQUIRED_FIELDS = ['full_name']

    class Meta:
        db_table = 'users'
        verbose_name = 'Pengguna'
        verbose_name_plural = 'Daftar Pengguna'
        indexes = [
            models.Index(fields=['foundation_id', 'status']),
            models.Index(fields=['foundation_id', 'email']),
        ]

    def __str__(self):
        return f"{self.full_name} ({self.phone_e164})"

    @property
    def is_locked(self) -> bool:
        """Return True if account is currently locked due to failed login attempts."""
        if self.locked_until and self.locked_until > timezone.now():
            return True
        return False

    def record_login_failure(self):
        """Record a failed login attempt and lock if threshold exceeded (IAM-008)."""
        self.failed_login_attempts += 1
        if self.failed_login_attempts >= 10:
            self.locked_until = timezone.now() + timedelta(minutes=15)
        self.save(update_fields=['failed_login_attempts', 'locked_until'])

    def record_login_success(self):
        """Reset failed attempt counters upon successful login."""
        if self.failed_login_attempts > 0 or self.locked_until is not None:
            self.failed_login_attempts = 0
            self.locked_until = None
            self.save(update_fields=['failed_login_attempts', 'locked_until'])

class OTPChallenge(models.Model):
    """WhatsApp/SMS OTP authentication challenge (IAM-002, IAM-003)."""
    id = models.BigAutoField(primary_key=True)
    phone_e164 = models.CharField(max_length=20, db_index=True)
    code_hash = models.CharField(max_length=128, help_text="Hashed 6-digit OTP code")
    expires_at = models.DateTimeField(db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=5)
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'otp_challenges'
        indexes = [
            models.Index(fields=['phone_e164', 'created_at']),
        ]

    def __str__(self):
        return f"OTP to {self.phone_e164} (Expires: {self.expires_at})"

    @property
    def is_expired(self) -> bool:
        return timezone.now() > self.expires_at

    @property
    def is_valid(self) -> bool:
        return not self.is_expired and self.verified_at is None and self.attempts < self.max_attempts

class RoleAssignment(TenantModel):
    """Assignment of a role to a user, scoped to either FOUNDATION or SCHOOL (spec/02 §2, §4)."""
    SCOPE_FOUNDATION = 'FOUNDATION'
    SCOPE_SCHOOL = 'SCHOOL'
    SCOPE_CHOICES = [
        (SCOPE_FOUNDATION, 'Foundation'),
        (SCOPE_SCHOOL, 'School'),
    ]

    ROLE_FOUNDATION_ADMIN = 'foundation_admin'
    ROLE_SCHOOL_ADMIN = 'school_admin'
    ROLE_FINANCE_OFFICER = 'finance_officer'
    ROLE_TEACHER = 'teacher'
    ROLE_COUNSELLOR = 'counsellor'
    ROLE_CANTEEN_OPERATOR = 'canteen_operator'
    ROLE_CLINIC_OFFICER = 'clinic_officer'
    ROLE_PARENT = 'parent'
    ROLE_CHOICES = [
        (ROLE_FOUNDATION_ADMIN, 'Foundation Admin'),
        (ROLE_SCHOOL_ADMIN, 'School Admin'),
        (ROLE_FINANCE_OFFICER, 'Finance Officer'),
        (ROLE_TEACHER, 'Teacher'),
        (ROLE_COUNSELLOR, 'Counsellor'),
        (ROLE_CANTEEN_OPERATOR, 'Canteen Operator'),
        (ROLE_CLINIC_OFFICER, 'Clinic Officer'),
        (ROLE_PARENT, 'Parent'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='role_assignments')
    role = models.CharField(max_length=32, choices=ROLE_CHOICES, db_index=True)
    scope_type = models.CharField(max_length=16, choices=SCOPE_CHOICES, default=SCOPE_SCHOOL, db_index=True)
    scope_id = models.BigIntegerField(db_index=True, help_text="ID of Foundation if scope_type=FOUNDATION, or School if scope_type=SCHOOL")

    class Meta:
        db_table = 'role_assignments'
        verbose_name = 'Penugasan Peran'
        verbose_name_plural = 'Daftar Penugasan Peran'
        indexes = [
            models.Index(fields=['foundation_id', 'user', 'role']),
            models.Index(fields=['foundation_id', 'scope_type', 'scope_id']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'user', 'role', 'scope_type', 'scope_id'],
                name='unique_user_role_scope'
            )
        ]

    def __str__(self):
        return f"{self.user.phone_e164} -> {self.role} ({self.scope_type}:{self.scope_id})"

class FoundationEntitlement(TenantModel):
    """Feature entitlements gating modules per foundation and per school (spec/02 §6, IAM-023)."""
    MODULE_ACADEMIC = 'academic'
    MODULE_ATTENDANCE = 'attendance'
    MODULE_FINANCE = 'finance'
    MODULE_WALLET = 'wallet'
    MODULE_CAMPUS_LIFE = 'campus_life'
    MODULE_PAYROLL = 'payroll'
    MODULE_ANALYTICS = 'analytics'
    MODULE_HARDWARE = 'hardware'

    MODULE_CHOICES = [
        (MODULE_ACADEMIC, 'Academic'),
        (MODULE_ATTENDANCE, 'Attendance'),
        (MODULE_FINANCE, 'Finance'),
        (MODULE_WALLET, 'Wallet / Canteen'),
        (MODULE_CAMPUS_LIFE, 'Campus Life'),
        (MODULE_PAYROLL, 'Payroll'),
        (MODULE_ANALYTICS, 'Analytics'),
        (MODULE_HARDWARE, 'Hardware & IoT'),
    ]

    school_id = models.BigIntegerField(null=True, blank=True, db_index=True, help_text="Null for foundation-wide default, or specific school ID")
    module_key = models.CharField(max_length=32, choices=MODULE_CHOICES, db_index=True)
    enabled = models.BooleanField(default=True, db_index=True)
    limits = models.JSONField(default=dict, blank=True, help_text="Plan tier limits or quotas (JSON)")

    class Meta:
        db_table = 'foundation_entitlements'
        verbose_name = 'Hak Akses Modul'
        verbose_name_plural = 'Daftar Hak Akses Modul'
        indexes = [
            models.Index(fields=['foundation_id', 'module_key']),
            models.Index(fields=['foundation_id', 'school_id', 'module_key']),
        ]

    def __str__(self):
        scope = f"School {self.school_id}" if self.school_id else "Foundation-wide"
        status_text = "ENABLED" if self.enabled else "DISABLED"
        return f"[{scope}] {self.module_key} -> {status_text}"


class Student(TenantModel):
    """Student profile linked to a School and Person PII record (spec/02 §2, §5)."""
    STATUS_PROSPECT = 'PROSPECT'
    STATUS_ACTIVE = 'ACTIVE'
    STATUS_INACTIVE = 'INACTIVE'
    STATUS_GRADUATED = 'GRADUATED'
    STATUS_TRANSFERRED_OUT = 'TRANSFERRED_OUT'

    STATUS_CHOICES = [
        (STATUS_PROSPECT, 'Calon Siswa (Prospect)'),
        (STATUS_ACTIVE, 'Aktif (Active)'),
        (STATUS_INACTIVE, 'Nonaktif (Inactive)'),
        (STATUS_GRADUATED, 'Lulus (Graduated)'),
        (STATUS_TRANSFERRED_OUT, 'Pindah Keluar (Transferred Out)'),
    ]

    # Permitted lifecycle state machine transitions (IAM-019)
    VALID_STATUS_TRANSITIONS = {
        STATUS_PROSPECT: {STATUS_ACTIVE, STATUS_INACTIVE},
        STATUS_ACTIVE: {STATUS_INACTIVE, STATUS_GRADUATED, STATUS_TRANSFERRED_OUT},
        STATUS_INACTIVE: {STATUS_ACTIVE},  # Re-activation permitted
        STATUS_GRADUATED: set(),            # Terminal state
        STATUS_TRANSFERRED_OUT: set(),      # Terminal state
    }

    school = models.ForeignKey(
        School,
        on_delete=models.PROTECT,
        related_name='students',
        help_text="The school/madrasah where the student is currently enrolled"
    )
    person = models.ForeignKey(
        Person,
        on_delete=models.PROTECT,
        related_name='student_profiles',
        help_text="Reference to Person PII vault (spec/02 §2, spec/14 §4)"
    )
    nisn = models.CharField(
        max_length=10,
        blank=True,
        null=True,
        db_index=True,
        help_text="Nomor Induk Siswa Nasional (10 digits)"
    )
    nis = models.CharField(
        max_length=32,
        db_index=True,
        help_text="Nomor Induk Siswa (internal school registration number)"
    )
    photo_key = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text="Local filesystem key / relative path to student profile picture"
    )
    status = models.CharField(
        max_length=32,
        choices=STATUS_CHOICES,
        default=STATUS_PROSPECT,
        db_index=True,
        help_text="Current enrollment status (IAM-019)"
    )
    target_grade_level = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Target entry grade level (e.g. 1, 7, 10) for prospective students"
    )

    class Meta:
        db_table = 'students'
        verbose_name = 'Siswa'
        verbose_name_plural = 'Daftar Siswa'
        indexes = [
            models.Index(fields=['foundation_id', 'school_id', 'status']),
            models.Index(fields=['foundation_id', 'school_id', 'target_grade_level']),
            models.Index(fields=['foundation_id', 'nis']),
            models.Index(fields=['foundation_id', 'nisn']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'school_id', 'nis'],
                name='unique_school_student_nis'
            ),
        ]

    def __str__(self):
        return f"{self.person.full_name} (NIS: {self.nis} - {self.status})"

    @property
    def full_name(self) -> str:
        return self.person.full_name if hasattr(self, 'person') and self.person else ''

    @property
    def effective_grade_level(self):
        """Resolve current grade level: active ClassEnrollment grade_level if enrolled, else target_grade_level."""
        active_enrollment = self.class_enrollments.filter(is_active=True).select_related('class_group').first()
        if active_enrollment and active_enrollment.class_group:
            return active_enrollment.class_group.grade_level
        return self.target_grade_level

    def transition_status(self, new_status: str, actor_id: str = None, reason: str = ''):
        """Execute a validated lifecycle state transition (IAM-019) and record domain event."""
        if new_status == self.status:
            return

        valid_targets = self.VALID_STATUS_TRANSITIONS.get(self.status, set())
        if new_status not in valid_targets:
            raise ValueError(
                f"Invalid status transition from {self.status} to {new_status} for student {self.id}."
            )

        old_status = self.status
        self.status = new_status
        if actor_id:
            self.updated_by = actor_id
        self.save(update_fields=['status', 'updated_at', 'updated_by'])

        from apps.core.services import record_domain_event
        record_domain_event(
            name='identity.student.status_changed',
            foundation_id=self.foundation_id,
            payload={
                'student_id': self.id,
                'school_id': self.school_id,
                'old_status': old_status,
                'new_status': new_status,
                'actor_id': actor_id,
                'reason': reason,
            }
        )

        # WAL-007: freeze/unfreeze the student's wallet if one exists. Mirrors the
        # existing apps.attendance -> apps.notifications direct-call pattern; a
        # wallet-side failure must never break the status transition itself.
        try:
            from apps.wallet.services import sync_wallet_freeze_on_student_status_change
            sync_wallet_freeze_on_student_status_change(self, new_status)
        except Exception as exc:
            logger.warning(f"Error syncing wallet freeze state for student #{self.id}: {exc}")

        # WAL-026: queue a residual-balance refund on exit. Kept as a second,
        # independently try/except-wrapped call so a failure here can never block
        # the freeze sync above (or vice versa).
        try:
            from apps.wallet.services import queue_wallet_refund_on_exit
            queue_wallet_refund_on_exit(self, new_status)
        except Exception as exc:
            logger.warning(f"Error queuing wallet refund on exit for student #{self.id}: {exc}")

        # spec/18: notify partner integrations when a student becomes ACTIVE
        # within their scope ("roster.student.enrolled"). Fire-and-forget, kept
        # as a third independently try/except-wrapped call.
        if new_status == self.STATUS_ACTIVE:
            try:
                from apps.partners.services import (
                    EVENT_ROSTER_STUDENT_ENROLLED,
                    safe_emit_partner_event,
                )
                safe_emit_partner_event(
                    foundation_id=self.foundation_id,
                    event_type=EVENT_ROSTER_STUDENT_ENROLLED,
                    payload={
                        'student_id': self.id,
                        'school_id': self.school_id,
                        'full_name': self.full_name,
                    },
                )
            except Exception as exc:
                logger.warning(f"Error emitting partner roster event for student #{self.id}: {exc}")


class Guardian(TenantModel):
    """Parent / legal guardian profile (spec/02 §2, IAM-009, IAM-014).
    
    A guardian is anchored to a Person PII record and optionally to a User account
    for logging into the mobile portal via phone OTP.
    """
    person = models.ForeignKey(
        Person,
        on_delete=models.PROTECT,
        related_name='guardian_profiles',
        help_text="Reference to Person PII vault (spec/02 §2)"
    )
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='guardian_profiles',
        help_text="Associated user account for mobile login (null if not yet invited/registered)"
    )
    occupation = models.CharField(
        max_length=128,
        blank=True,
        default='',
        help_text="Pekerjaan wali murid (e.g. Pegawai Swasta, Wiraswasta, Guru)"
    )

    class Meta:
        db_table = 'guardians'
        verbose_name = 'Wali Murid'
        verbose_name_plural = 'Daftar Wali Murid'
        indexes = [
            models.Index(fields=['foundation_id', 'person']),
            models.Index(fields=['foundation_id', 'user']),
        ]

    def __str__(self):
        return f"Wali: {self.person.full_name} ({self.occupation or 'Wali'})"


class GuardianLink(TenantModel):
    """Relationship link between a Guardian and a Student (spec/02 §2, §4, IAM-014, IAM-015)."""
    RELATION_FATHER = 'FATHER'
    RELATION_MOTHER = 'MOTHER'
    RELATION_GUARDIAN = 'GUARDIAN'

    RELATION_CHOICES = [
        (RELATION_FATHER, 'Ayah (Father)'),
        (RELATION_MOTHER, 'Ibu (Mother)'),
        (RELATION_GUARDIAN, 'Wali (Guardian)'),
    ]

    guardian = models.ForeignKey(
        Guardian,
        on_delete=models.CASCADE,
        related_name='student_links',
        help_text="The parent or legal guardian"
    )
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name='guardian_links',
        help_text="The linked student"
    )
    relation = models.CharField(
        max_length=16,
        choices=RELATION_CHOICES,
        default=RELATION_GUARDIAN,
        help_text="Hubungan kekeluargaan (Ayah, Ibu, atau Wali)"
    )
    is_primary = models.BooleanField(
        default=False,
        help_text="Whether this guardian is the primary contact for communications"
    )
    can_pickup = models.BooleanField(
        default=True,
        help_text="Whether this guardian is authorized for student campus pickup"
    )
    financial_responsible = models.BooleanField(
        default=False,
        help_text="Whether this guardian receives invoices and can view financial arrears (IAM-015)"
    )

    class Meta:
        db_table = 'guardian_links'
        verbose_name = 'Hubungan Siswa-Wali'
        verbose_name_plural = 'Daftar Hubungan Siswa-Wali'
        indexes = [
            models.Index(fields=['foundation_id', 'guardian']),
            models.Index(fields=['foundation_id', 'student']),
            models.Index(fields=['foundation_id', 'financial_responsible']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'guardian', 'student'],
                name='unique_guardian_student_link'
            )
        ]

    def __str__(self):
        return f"{self.guardian.person.full_name} -> {self.student.person.full_name} ({self.relation})"


class Staff(TenantModel):
    """Staff member profile linked to Person PII and User account (spec/02 §2, §5)."""
    TYPE_PERMANENT = 'PERMANENT'
    TYPE_CONTRACT = 'CONTRACT'
    TYPE_HONORARY = 'HONORARY'
    EMPLOYMENT_CHOICES = [
        (TYPE_PERMANENT, 'Tetap (Permanent)'),
        (TYPE_CONTRACT, 'Kontrak (Contract)'),
        (TYPE_HONORARY, 'Honorer (Honorary)'),
    ]

    STATUS_ACTIVE = 'ACTIVE'
    STATUS_ON_LEAVE = 'ON_LEAVE'
    STATUS_OFFBOARDED = 'OFFBOARDED'
    STATUS_CHOICES = [
        (STATUS_ACTIVE, 'Aktif (Active)'),
        (STATUS_ON_LEAVE, 'Cuti (On Leave)'),
        (STATUS_OFFBOARDED, 'Berhenti / Offboarded'),
    ]

    person = models.ForeignKey(
        Person,
        on_delete=models.PROTECT,
        related_name='staff_profiles',
        help_text="Reference to Person PII vault (spec/02 §2)"
    )
    user = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='staff_profiles',
        help_text="Associated user account for staff system access"
    )
    school = models.ForeignKey(
        School,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='staff_members',
        help_text="Specific school assignment (null if foundation-wide staff)"
    )
    nip = models.CharField(
        max_length=32,
        blank=True,
        null=True,
        db_index=True,
        help_text="Nomor Induk Pegawai"
    )
    nuptk = models.CharField(
        max_length=16,
        blank=True,
        null=True,
        db_index=True,
        help_text="Nomor Urut Pendidik dan Tenaga Kependidikan (DAPODIK/EMIS educator ID, CMP-017)"
    )
    employment_type = models.CharField(
        max_length=32,
        choices=EMPLOYMENT_CHOICES,
        default=TYPE_PERMANENT,
        db_index=True
    )

    # DAPODIK/EMIS statutory fields (CMP-017). Blank-optional: the
    # pre-export validation report flags them, entry is never blocked.
    APPT_PNS = 'PNS'
    APPT_CPNS = 'CPNS'
    APPT_PPPK = 'PPPK'
    APPT_GTY = 'GTY'
    APPT_PTT = 'PTT'
    APPT_NONE = 'TIDAK_ADA'
    APPOINTMENT_CHOICES = [
        (APPT_PNS, 'PNS'),
        (APPT_CPNS, 'CPNS'),
        (APPT_PPPK, 'PPPK'),
        (APPT_GTY, 'GTY (Guru Tidak Tetap)'),
        (APPT_PTT, 'PTT (Pegawai Tidak Tetap)'),
        (APPT_NONE, 'Tidak Ada (Tetap Lokal)'),
    ]
    appointment_type = models.CharField(max_length=16, choices=APPOINTMENT_CHOICES, blank=True, default='', help_text="Jenis pengangkatan (DAPODIK stat peg)")
    CERT_SERTIFIKAT = 'SERTIFIKAT'
    CERT_BELUM = 'BELUM'
    CERT_PROSES = 'SERTIFIKASI_PROSES'
    CERTIFICATION_CHOICES = [
        (CERT_SERTIFIKAT, 'Sudah Bersertifikat Pendidik'),
        (CERT_BELUM, 'Belum Bersertifikat'),
        (CERT_PROSES, 'Sedang Proses Sertifikasi'),
    ]
    certification_status = models.CharField(max_length=19, choices=CERTIFICATION_CHOICES, blank=True, default='', help_text="Status sertifikasi pendidik (DAPODIK)")
    DEGREE_CHOICES = [
        ('SMA', 'SMA/SMK/MA'), ('D1', 'D1'), ('D2', 'D2'), ('D3', 'D3'),
        ('D4', 'D4'), ('S1', 'S1'), ('S2', 'S2'), ('S3', 'S3'),
    ]
    highest_degree = models.CharField(max_length=16, choices=DEGREE_CHOICES, blank=True, default='', help_text="Pendidikan tertinggi (DAPODIK)")
    degree_institution = models.CharField(max_length=128, blank=True, default='', help_text="Institusi pendidikan terakhir")
    degree_graduation_year = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Tahun lulus pendidikan terakhir")

    join_date = models.DateField(help_text="Tanggal mulai bekerja")
    resignation_date = models.DateField(null=True, blank=True, help_text="Tanggal berhenti / offboarding")
    resignation_reason = models.TextField(blank=True, default='', help_text="Alasan offboarding")
    status = models.CharField(
        max_length=32,
        choices=STATUS_CHOICES,
        default=STATUS_ACTIVE,
        db_index=True
    )

    class Meta:
        db_table = 'staff'
        verbose_name = 'Staf / Pendidik'
        verbose_name_plural = 'Daftar Staf & Pendidik'
        indexes = [
            models.Index(fields=['foundation_id', 'status']),
            models.Index(fields=['foundation_id', 'school_id']),
            models.Index(fields=['foundation_id', 'nip']),
        ]

    def __str__(self):
        school_name = self.school.name if self.school else "Yayasan"
        return f"{self.person.full_name} ({self.nip or 'No NIP'} - {school_name} - {self.status})"


class SocialLogin(TenantModel):
    """Links a User to an external identity provider (Google Workspace / Microsoft 365)
    for Staff SSO (spec/14 §6, TASK-036).

    One user may link multiple provider accounts; one provider user ID may only
    be linked to one user per foundation (enforced by unique constraint). Soft-delete
    preserves history when unlinking.
    """
    PROVIDER_GOOGLE = 'google'
    PROVIDER_MICROSOFT = 'microsoft'
    PROVIDER_CHOICES = [
        (PROVIDER_GOOGLE, 'Google Workspace'),
        (PROVIDER_MICROSOFT, 'Microsoft 365'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='social_logins')
    provider = models.CharField(max_length=16, choices=PROVIDER_CHOICES, db_index=True)
    provider_user_id = models.CharField(
        max_length=255,
        help_text="Provider's unique identifier (Google 'sub' or Microsoft 'oid')",
    )
    email = models.EmailField(max_length=255, blank=True, default='',
                              help_text="Email used for this social login")
    active_uniq_marker = soft_delete_uniqueness_marker()

    class Meta:
        db_table = 'social_logins'
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'provider', 'provider_user_id', 'active_uniq_marker'],
                name='unique_active_social_login_per_provider',
            ),
        ]
        indexes = [
            models.Index(fields=['foundation_id', 'user']),
            models.Index(fields=['foundation_id', 'email']),
        ]

    def __str__(self):
        return f"{self.provider}:{self.provider_user_id} -> {self.user_id}"


def validate_microsoft_tenant_id(value: str) -> None:
    """Accept a Microsoft Entra Directory (tenant) ID: a GUID or a registered domain."""
    v = (value or '').strip()
    try:
        uuid.UUID(v)
        return
    except (ValueError, AttributeError):
        pass
    domain_re = r'[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?)+'
    if re.fullmatch(domain_re, v):
        return
    raise DjangoValidationError(
        "Tenant ID harus berupa GUID Microsoft Entra (Directory ID) atau nama domain terdaftar."
    )


class MicrosoftTenantConfig(TenantModel):
    """Per-foundation Microsoft Entra (Azure AD) tenant pinning for Microsoft 365 SSO.

    Open item deferred from TASK-036 (PR #115): the global env fallback
    (`SOCIAL_AUTH_MICROSOFT_TENANT_ID`, default 'common') accepts ID tokens from
    any Microsoft tenant. When an active row exists for the foundation, SSO for
    that foundation verifies tokens strictly against this tenant instead
    (tenant-specific JWKS/issuer + `tid` claim check). Foundations without a
    row keep the pilot behaviour.
    """
    tenant_id = models.CharField(
        max_length=255,
        validators=[validate_microsoft_tenant_id],
        help_text="Microsoft Entra Directory (tenant) ID — GUID atau domain terdaftar",
    )
    active_uniq_marker = soft_delete_uniqueness_marker()

    class Meta:
        db_table = 'microsoft_tenant_configs'
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'active_uniq_marker'],
                name='unique_active_ms_tenant_config_per_foundation',
            ),
        ]
        indexes = [
            models.Index(fields=['foundation_id', 'tenant_id']),
        ]

    def __str__(self):
        return f"foundation={self.foundation_id} tenant={self.tenant_id}"


class PlatformRoleAssignment(models.Model):
    """Platform-wide (non-tenant) role assignment for EduCore operator staff.

    Deliberately NOT a TenantModel: a platform role has no foundation_id and
    must never be routed through TenantManager's fail-closed tenant scoping.
    See docs/superpowers/specs/2026-09-18-service-status-page-design.md §4.
    """
    ROLE_PLATFORM_OPERATOR = 'platform_operator'
    ROLE_CHOICES = [
        (ROLE_PLATFORM_OPERATOR, 'Platform Operator'),
    ]

    # Forward access to .user on a freshly-loaded (not select_related'd) PlatformRoleAssignment
    # requires an active tenant context, or use User.all_tenants.get(pk=assignment.user_id) instead.
    # This model is deliberately used in situations with NO tenant context; see select_related docs.
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='platform_role_assignments')
    role = models.CharField(max_length=32, choices=ROLE_CHOICES, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'platform_role_assignments'
        verbose_name = 'Penugasan Peran Platform'
        verbose_name_plural = 'Daftar Penugasan Peran Platform'
        constraints = [
            models.UniqueConstraint(fields=['user', 'role'], name='unique_user_platform_role')
        ]

    def __str__(self):
        return f"{self.user.phone_e164} -> {self.role} (PLATFORM)"
