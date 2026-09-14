"""Identity and Tenancy models (spec/02, spec/03).

Defines:
- Foundation: root multi-tenant entity
- School: educational operating unit
- User: custom auth model supporting phone and email authentication
- Person: PII vault isolated for UU PDP compliance
- OTPChallenge: 6-digit WhatsApp/SMS OTP challenges
"""
from datetime import timedelta
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone
from apps.core.models import TenantModel
from .managers import UserManager, AllUsersManager

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
    """
    GENDER_MALE = 'L'
    GENDER_FEMALE = 'P'
    GENDER_CHOICES = [
        (GENDER_MALE, 'Laki-laki'),
        (GENDER_FEMALE, 'Perempuan'),
    ]

    nik = models.CharField(max_length=16, blank=True, null=True, db_index=True, help_text="Nomor Induk Kependudukan (16 digits)")
    full_name = models.CharField(max_length=128, help_text="Full legal name per birth certificate / KTP")
    dob = models.DateField(null=True, blank=True, help_text="Tanggal lahir")
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES, blank=True, default='')
    address = models.TextField(blank=True, default='', help_text="Alamat domisili lengkap")

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
    ROLE_PARENT = 'parent'
    ROLE_CHOICES = [
        (ROLE_FOUNDATION_ADMIN, 'Foundation Admin'),
        (ROLE_SCHOOL_ADMIN, 'School Admin'),
        (ROLE_FINANCE_OFFICER, 'Finance Officer'),
        (ROLE_TEACHER, 'Teacher'),
        (ROLE_COUNSELLOR, 'Counsellor'),
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

    class Meta:
        db_table = 'students'
        verbose_name = 'Siswa'
        verbose_name_plural = 'Daftar Siswa'
        indexes = [
            models.Index(fields=['foundation_id', 'school_id', 'status']),
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
    employment_type = models.CharField(
        max_length=32,
        choices=EMPLOYMENT_CHOICES,
        default=TYPE_PERMANENT,
        db_index=True
    )
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




