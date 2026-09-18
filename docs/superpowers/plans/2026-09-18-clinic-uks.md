# Campus Life: Clinic / UKS Unit Management (LIF-001..007) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the School Health Unit (UKS) backend slice — clinic visits, health profiles, medication stock, guardian consent, and the resulting attendance/notification side-effects — per `spec/10-campus-life.md` §3 (LIF-001..007).

**Architecture:** All new code lives inside the existing `apps.campus` app (which already owns spec/10's Behaviour submodule). Clinic notes are Fernet-encrypted at rest via a new `apps/campus/crypto.py`. Medication consent reuses `apps.compliance.ConsentRecord` (purpose `HEALTH_DATA`). SENT_HOME/REFERRED outcomes reuse the existing day-level `AttendanceDay` override mechanism and a new `NotificationCategory.CLINIC_INCIDENT`. A new `clinic_officer` RBAC role is added to `apps.identity`.

**Tech Stack:** Django 5.x, DRF, MySQL 8 (SQLite fallback for fast tests per `EDUCORE_USE_SQLITE=1`), `cryptography` (Fernet, already a dependency).

## Global Constraints

- Every new model inherits `core.models.TenantModel` (3-layer tenancy — model, `TenantManager`, viewset cross-tenant test) per `memory/00_CORE.md` §2.
- Money is not touched by this slice — no `MoneyField` usage.
- No hard-deletion: rely on `TenantModel`'s soft delete (`deleted_at`); no model in this plan overrides `delete()`.
- Every mutating service call writes an audit event via `apps.core.services.audit()` (no signals).
- `id-ID` first: all user-facing strings (`help_text`, validation messages, choice labels) are Indonesian, matching the existing `apps.campus` convention.
- Clinic free-text notes (`complaint`, `treatment`) are Fernet-encrypted at rest (LIF-007) via a per-app key, `EDUCORE_CLINIC_FERNET_KEY`, falling back to a `SECRET_KEY`-derived key in dev/test — same convention as `apps.hardware.crypto`.
- Target ≥80% coverage on new business logic per `USER.md` SOP §5.
- Design reference: `docs/superpowers/specs/2026-09-18-clinic-uks-design.md`.

---

### Task 1: RBAC — `clinic_officer` role

**Files:**
- Modify: `apps/identity/models.py` (`RoleAssignment.ROLE_CHOICES`, around line 331-349)
- Modify: `apps/identity/rbac.py`
- Create: migration via `python manage.py makemigrations identity`
- Test: `apps/identity/tests/test_rbac.py`

**Interfaces:**
- Produces: `RoleAssignment.ROLE_CLINIC_OFFICER = 'clinic_officer'`; `ROLE_PERMISSIONS['clinic_officer']` set; `'clinic.write'` added to `ROLE_FOUNDATION_ADMIN` and `ROLE_SCHOOL_ADMIN`.

- [ ] **Step 1: Write the failing test**

Append to `apps/identity/tests/test_rbac.py` (open the file first to find its existing test class; add these as new methods on it — each test imports what it needs locally, no top-of-file import changes required):

```python
    def test_clinic_officer_role_permissions(self):
        from apps.identity.rbac import ROLE_CLINIC_OFFICER, ROLE_PERMISSIONS
        perms = ROLE_PERMISSIONS[ROLE_CLINIC_OFFICER]
        self.assertIn('clinic.read', perms)
        self.assertIn('clinic.write', perms)
        self.assertIn('student_records.read', perms)

    def test_school_admin_and_foundation_admin_have_clinic_write(self):
        from apps.identity.rbac import ROLE_FOUNDATION_ADMIN, ROLE_SCHOOL_ADMIN, ROLE_PERMISSIONS
        self.assertIn('clinic.write', ROLE_PERMISSIONS[ROLE_FOUNDATION_ADMIN])
        self.assertIn('clinic.write', ROLE_PERMISSIONS[ROLE_SCHOOL_ADMIN])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test apps.identity.tests.test_rbac.RbacMatrixTests.test_clinic_officer_role_permissions -v 2` (use the actual test class name found in the file)
Expected: FAIL with `ImportError: cannot import name 'ROLE_CLINIC_OFFICER'`

- [ ] **Step 3: Implement**

In `apps/identity/models.py`, inside `class RoleAssignment(TenantModel):`:

```python
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
```

(Keep the rest of `ROLE_CHOICES` and the `role = models.CharField(...)` line unchanged.)

In `apps/identity/rbac.py`:

```python
ROLE_CANTEEN_OPERATOR = RoleAssignment.ROLE_CANTEEN_OPERATOR
ROLE_CLINIC_OFFICER = RoleAssignment.ROLE_CLINIC_OFFICER
ROLE_PARENT = RoleAssignment.ROLE_PARENT
```

(insert `ROLE_CLINIC_OFFICER` line after the existing `ROLE_CANTEEN_OPERATOR` line)

Add `'clinic.write'` to both `ROLE_FOUNDATION_ADMIN` and `ROLE_SCHOOL_ADMIN` sets (each currently has `'clinic.read'` — add `'clinic.write'` right after it), and add a new role block after `ROLE_CANTEEN_OPERATOR`'s block:

```python
    ROLE_CLINIC_OFFICER: {
        'student_records.read',
        'clinic.read', 'clinic.write',
        'analytics.event.write',
    },
```

- [ ] **Step 4: Generate migration and run tests**

Run: `python manage.py makemigrations identity`
Run: `python manage.py test apps.identity.tests.test_rbac -v 2`
Expected: PASS, new migration file created under `apps/identity/migrations/`.

- [ ] **Step 5: Commit**

```bash
git add apps/identity/models.py apps/identity/rbac.py apps/identity/migrations/ apps/identity/tests/test_rbac.py
git commit -m "feat(identity): add clinic_officer RBAC role (LIF-006)"
```

---

### Task 2: Notifications — `CLINIC_INCIDENT` category

**Files:**
- Modify: `apps/notifications/models.py`
- Test: `apps/notifications/tests/test_templates_and_preferences.py` (or wherever `CATEGORY_CONFIG` is tested — grep first: `grep -rn "CATEGORY_CONFIG\[" apps/notifications/tests/`)

**Interfaces:**
- Produces: `NotificationCategory.CLINIC_INCIDENT`; `CATEGORY_CONFIG[NotificationCategory.CLINIC_INCIDENT]`.

- [ ] **Step 1: Write the failing test**

Add to a notifications test file (create `apps/notifications/tests/test_clinic_incident_category.py` if no existing file is a clean fit):

```python
from django.test import TestCase

from apps.notifications.models import CATEGORY_CONFIG, NotificationCategory, NotificationPriority


class ClinicIncidentCategoryTests(TestCase):
    def test_clinic_incident_category_exists(self):
        self.assertEqual(NotificationCategory.CLINIC_INCIDENT, 'CLINIC_INCIDENT')

    def test_clinic_incident_config(self):
        config = CATEGORY_CONFIG[NotificationCategory.CLINIC_INCIDENT]
        self.assertEqual(config['priority'], NotificationPriority.HIGH)
        self.assertFalse(config['quiet_hours_respected'])
        self.assertTrue(config['opt_out_allowed'])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test apps.notifications.tests.test_clinic_incident_category -v 2`
Expected: FAIL — `CLINIC_INCIDENT` not a valid `NotificationCategory` value.

- [ ] **Step 3: Implement**

In `apps/notifications/models.py`, add to `class NotificationCategory(models.TextChoices):` (after the `ABSENCE` line):

```python
    CLINIC_INCIDENT = 'CLINIC_INCIDENT', _('Insiden Klinik (Clinic Incident)')
```

Add to `CATEGORY_CONFIG` (after the `NotificationCategory.ABSENCE` entry, matching its shape exactly — same as LIF-003's "immediately notify" requirement, bypassing quiet hours like ABSENCE/ARRIVAL/DEPARTURE):

```python
    NotificationCategory.CLINIC_INCIDENT: {
        # LIF-003: SENT_HOME/REFERRED clinic outcomes notify guardians + homeroom
        # teacher immediately — same urgency tier as ABSENCE, not EMERGENCY
        # (which is reserved for school-wide crises and can't be opted out of).
        'default_channels': [ChannelType.WHATSAPP, ChannelType.PUSH],
        'priority': NotificationPriority.HIGH,
        'quiet_hours_respected': False,
        'opt_out_allowed': True,
    },
```

- [ ] **Step 4: Generate migration and run tests**

Run: `python manage.py makemigrations notifications`
Run: `python manage.py test apps.notifications.tests.test_clinic_incident_category -v 2`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/notifications/models.py apps/notifications/migrations/ apps/notifications/tests/test_clinic_incident_category.py
git commit -m "feat(notifications): add CLINIC_INCIDENT category (LIF-003)"
```

---

### Task 3: Compliance — `has_active_health_consent` helper

**Files:**
- Modify: `apps/compliance/services.py` (add function after `record_consent`, currently ending around line 816)
- Test: `apps/compliance/tests/` (grep for the consent test file: `grep -rln "record_consent" apps/compliance/tests/`)

**Interfaces:**
- Consumes: `apps.compliance.models.ConsentRecord`, `ConsentPurpose.HEALTH_DATA`, `DataSubjectRequestSubjectType`.
- Produces: `has_active_health_consent(subject_type: str, subject_id: int, foundation_id: int) -> bool`.

- [ ] **Step 1: Write the failing test**

Find the existing consent test file (`grep -rln "record_consent" apps/compliance/tests/`) and add:

```python
    def test_has_active_health_consent_true_after_grant(self):
        from apps.compliance.services import has_active_health_consent
        from apps.compliance.models import ConsentPurpose, DataSubjectRequestSubjectType
        from apps.compliance.services import record_consent

        record_consent(
            subject_type=DataSubjectRequestSubjectType.STUDENT,
            subject_id=self.student.id,
            foundation_id=self.foundation.id,
            purpose=ConsentPurpose.HEALTH_DATA,
            granted_by='tester',
        )
        self.assertTrue(has_active_health_consent(
            subject_type=DataSubjectRequestSubjectType.STUDENT,
            subject_id=self.student.id,
            foundation_id=self.foundation.id,
        ))

    def test_has_active_health_consent_false_when_none_granted(self):
        from apps.compliance.services import has_active_health_consent
        from apps.compliance.models import DataSubjectRequestSubjectType

        self.assertFalse(has_active_health_consent(
            subject_type=DataSubjectRequestSubjectType.STUDENT,
            subject_id=self.student.id,
            foundation_id=self.foundation.id,
        ))

    def test_has_active_health_consent_false_after_withdrawal(self):
        from apps.compliance.services import has_active_health_consent, record_consent
        from apps.compliance.models import ConsentPurpose, ConsentRecord, DataSubjectRequestSubjectType
        from django.utils import timezone

        record = record_consent(
            subject_type=DataSubjectRequestSubjectType.STUDENT,
            subject_id=self.student.id,
            foundation_id=self.foundation.id,
            purpose=ConsentPurpose.HEALTH_DATA,
            granted_by='tester',
        )
        record.withdrawn_at = timezone.now()
        record.save(update_fields=['withdrawn_at'])

        self.assertFalse(has_active_health_consent(
            subject_type=DataSubjectRequestSubjectType.STUDENT,
            subject_id=self.student.id,
            foundation_id=self.foundation.id,
        ))
```

(Adjust `self.student`/`self.foundation` to whatever fixture attributes the existing test class in that file already sets up in `setUp` — read the file first.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test apps.compliance -k has_active_health_consent -v 2` (or the specific test path found in step 1)
Expected: FAIL — `ImportError: cannot import name 'has_active_health_consent'`

- [ ] **Step 3: Implement**

In `apps/compliance/services.py`, add after the `record_consent` function:

```python
def has_active_health_consent(subject_type: str, subject_id: int, foundation_id: int) -> bool:
    """True if the subject's latest HEALTH_DATA consent version is granted and not withdrawn (CMP-009, LIF-004)."""
    latest = (
        ConsentRecord.objects.filter(
            foundation_id=foundation_id,
            subject_type=subject_type,
            subject_id=subject_id,
            purpose=ConsentPurpose.HEALTH_DATA,
        )
        .order_by('-version')
        .first()
    )
    return bool(latest and latest.withdrawn_at is None)
```

- [ ] **Step 4: Run tests**

Run: `python manage.py test apps.compliance -v 2`
Expected: PASS, full `apps.compliance` suite still green.

- [ ] **Step 5: Commit**

```bash
git add apps/compliance/services.py apps/compliance/tests/
git commit -m "feat(compliance): add has_active_health_consent helper (LIF-004)"
```

---

### Task 4: Campus — clinic note encryption (`apps/campus/crypto.py`)

**Files:**
- Create: `apps/campus/crypto.py`
- Test: `apps/campus/tests/test_clinic_crypto.py`

**Interfaces:**
- Produces: `encrypt_note(raw_text: str) -> str`, `decrypt_note(ciphertext: str) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# apps/campus/tests/test_clinic_crypto.py
from django.test import TestCase, override_settings

from apps.campus.crypto import decrypt_note, encrypt_note


class ClinicCryptoTests(TestCase):
    def test_round_trip(self):
        ciphertext = encrypt_note("Demam tinggi, alergi obat penisilin.")
        self.assertNotEqual(ciphertext, "Demam tinggi, alergi obat penisilin.")
        self.assertEqual(decrypt_note(ciphertext), "Demam tinggi, alergi obat penisilin.")

    @override_settings(EDUCORE_CLINIC_FERNET_KEY='')
    def test_round_trip_with_secret_key_fallback(self):
        ciphertext = encrypt_note("Catatan rahasia")
        self.assertEqual(decrypt_note(ciphertext), "Catatan rahasia")

    def test_decrypt_wrong_key_raises(self):
        from cryptography.fernet import Fernet
        ciphertext = Fernet(Fernet.generate_key()).encrypt(b"data").decode()
        with self.assertRaises(RuntimeError):
            decrypt_note(ciphertext)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test apps.campus.tests.test_clinic_crypto -v 2`
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.campus.crypto'`

- [ ] **Step 3: Implement**

```python
# apps/campus/crypto.py
"""Clinic visit note encryption at rest (spec/10 LIF-007).

Same Fernet-per-app-key convention as apps.hardware.crypto / apps.partners.crypto:
key comes from EDUCORE_CLINIC_FERNET_KEY, falling back to a SECRET_KEY-derived
key so dev/test work out of the box — production must set an explicit key.
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _get_fernet() -> Fernet:
    raw = getattr(settings, 'EDUCORE_CLINIC_FERNET_KEY', '') or ''
    if raw:
        key = raw if isinstance(raw, bytes) else raw.encode()
        try:
            Fernet(key)
            return Fernet(key)
        except Exception:
            key = base64.urlsafe_b64encode(hashlib.sha256(key).digest())
            return Fernet(key)
    key = base64.urlsafe_b64encode(hashlib.sha256(str(settings.SECRET_KEY).encode()).digest())
    return Fernet(key)


def encrypt_note(raw_text: str) -> str:
    return _get_fernet().encrypt(raw_text.encode()).decode()


def decrypt_note(ciphertext: str) -> str:
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        raise RuntimeError("Clinic note decryption failed: Fernet key mismatch.")
```

- [ ] **Step 4: Run tests**

Run: `python manage.py test apps.campus.tests.test_clinic_crypto -v 2`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/campus/crypto.py apps/campus/tests/test_clinic_crypto.py
git commit -m "feat(campus): Fernet encryption for clinic notes (LIF-007)"
```

---

### Task 5: Campus — clinic models

**Files:**
- Modify: `apps/campus/models.py` (append after `BehaviourCase`)
- Modify: `apps/campus/admin.py` (register new models)
- Create: migration via `python manage.py makemigrations campus`
- Test: `apps/campus/tests/test_clinic_models.py`

**Interfaces:**
- Produces: `ClinicOutcome` (TextChoices: `RETURNED_TO_CLASS`/`SENT_HOME`/`REFERRED`), `ClinicPolicy`, `HealthProfile` (with `.has_medical_alert` property), `MedicationStock`, `ClinicVisit`.

- [ ] **Step 1: Write the failing test**

```python
# apps/campus/tests/test_clinic_models.py
import datetime

from django.test import TestCase

from apps.academic.tests.base import build_academic_fixture
from apps.campus.models import ClinicOutcome, ClinicPolicy, ClinicVisit, HealthProfile, MedicationStock


class ClinicModelTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()

    def test_clinic_policy_defaults(self):
        policy = ClinicPolicy.objects.create(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
        )
        self.assertTrue(policy.teacher_sees_allergies)

    def test_health_profile_has_medical_alert_false_when_empty(self):
        profile = HealthProfile.objects.create(
            foundation_id=self.fx['foundation'].id,
            student=self.fx['student'],
        )
        self.assertFalse(profile.has_medical_alert)

    def test_health_profile_has_medical_alert_true_with_allergies(self):
        profile = HealthProfile.objects.create(
            foundation_id=self.fx['foundation'].id,
            student=self.fx['student'],
            allergies=['Penisilin'],
        )
        self.assertTrue(profile.has_medical_alert)

    def test_medication_stock_creation(self):
        stock = MedicationStock.objects.create(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            name='Paracetamol 500mg',
            unit='tablet',
            quantity=100,
            expiry_date=datetime.date(2027, 1, 1),
            reorder_level=20,
        )
        self.assertEqual(stock.quantity, 100)

    def test_clinic_visit_creation(self):
        visit = ClinicVisit.objects.create(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            complaint_encrypted='ciphertext-placeholder',
            outcome=ClinicOutcome.RETURNED_TO_CLASS,
            handled_by=self.fx['teacher'],
        )
        self.assertEqual(visit.outcome, ClinicOutcome.RETURNED_TO_CLASS)
        self.assertIsNone(visit.guardian_notified_at)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test apps.campus.tests.test_clinic_models -v 2`
Expected: FAIL — `ImportError: cannot import name 'ClinicPolicy'`

- [ ] **Step 3: Implement**

Append to `apps/campus/models.py` (after the `BehaviourCase` class, keep existing `BehaviourCategory`/`CaseStatus` imports/classes untouched):

```python
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
```

Update `apps/campus/admin.py` — add import and registrations:

```python
from django.contrib import admin
from .models import (
    BehaviourCase, BehaviourPolicy, BehaviourReason, BehaviourRecord,
    ClinicPolicy, ClinicVisit, HealthProfile, MedicationStock,
)
```

(keep existing `BehaviourPolicyAdmin`/etc. unchanged, then append:)

```python
@admin.register(ClinicPolicy)
class ClinicPolicyAdmin(admin.ModelAdmin):
    list_display = ('school', 'teacher_sees_allergies')
    search_fields = ('school__name',)


@admin.register(HealthProfile)
class HealthProfileAdmin(admin.ModelAdmin):
    list_display = ('student', 'blood_type')
    search_fields = ('student__person__full_name', 'student__nis')


@admin.register(MedicationStock)
class MedicationStockAdmin(admin.ModelAdmin):
    list_display = ('name', 'school', 'quantity', 'reorder_level', 'expiry_date')
    list_filter = ('school',)
    search_fields = ('name',)


@admin.register(ClinicVisit)
class ClinicVisitAdmin(admin.ModelAdmin):
    list_display = ('student', 'school', 'occurred_at', 'outcome', 'handled_by')
    list_filter = ('outcome', 'school')
    search_fields = ('student__person__full_name', 'student__nis')
```

- [ ] **Step 4: Generate migration and run tests**

Run: `python manage.py makemigrations campus`
Run: `python manage.py test apps.campus.tests.test_clinic_models -v 2`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/campus/models.py apps/campus/admin.py apps/campus/migrations/ apps/campus/tests/test_clinic_models.py
git commit -m "feat(campus): clinic domain models — ClinicPolicy, HealthProfile, MedicationStock, ClinicVisit (LIF-001..007)"
```

---

### Task 6: Campus — clinic policy/health-profile/stock-alert services

**Files:**
- Create: `apps/campus/services_clinic.py`
- Test: `apps/campus/tests/test_clinic_services.py`

**Interfaces:**
- Consumes: `ClinicPolicy`, `HealthProfile`, `MedicationStock` (Task 5).
- Produces: `get_or_create_clinic_policy(school) -> ClinicPolicy`, `get_or_create_health_profile(student) -> HealthProfile`, `get_medication_stock_alerts(school) -> QuerySet[MedicationStock]`.

- [ ] **Step 1: Write the failing test**

```python
# apps/campus/tests/test_clinic_services.py
import datetime

from django.test import TestCase
from django.utils import timezone

from apps.academic.tests.base import build_academic_fixture
from apps.campus.models import MedicationStock
from apps.campus.services_clinic import (
    get_medication_stock_alerts,
    get_or_create_clinic_policy,
    get_or_create_health_profile,
)


class ClinicPolicyAndAlertServiceTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()

    def test_get_or_create_clinic_policy_is_idempotent(self):
        p1 = get_or_create_clinic_policy(self.fx['school'])
        p2 = get_or_create_clinic_policy(self.fx['school'])
        self.assertEqual(p1.id, p2.id)

    def test_get_or_create_health_profile_is_idempotent(self):
        h1 = get_or_create_health_profile(self.fx['student'])
        h2 = get_or_create_health_profile(self.fx['student'])
        self.assertEqual(h1.id, h2.id)

    def test_medication_stock_alerts_below_reorder_level(self):
        low = MedicationStock.objects.create(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'],
            name='Oralit', unit='sachet', quantity=2, reorder_level=10,
            expiry_date=datetime.date(2030, 1, 1),
        )
        MedicationStock.objects.create(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'],
            name='Perban', unit='pcs', quantity=50, reorder_level=10,
            expiry_date=datetime.date(2030, 1, 1),
        )
        alerts = get_medication_stock_alerts(self.fx['school'])
        self.assertIn(low, list(alerts))
        self.assertEqual(alerts.count(), 1)

    def test_medication_stock_alerts_near_expiry(self):
        near_expiry = MedicationStock.objects.create(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'],
            name='Antiseptik', unit='botol', quantity=100, reorder_level=5,
            expiry_date=(timezone.now().date() + datetime.timedelta(days=10)),
        )
        alerts = get_medication_stock_alerts(self.fx['school'])
        self.assertIn(near_expiry, list(alerts))

    def test_medication_stock_alerts_excludes_healthy_stock(self):
        MedicationStock.objects.create(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'],
            name='Vitamin C', unit='tablet', quantity=200, reorder_level=10,
            expiry_date=datetime.date(2030, 1, 1),
        )
        alerts = get_medication_stock_alerts(self.fx['school'])
        self.assertEqual(alerts.count(), 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test apps.campus.tests.test_clinic_services -v 2`
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.campus.services_clinic'`

- [ ] **Step 3: Implement**

```python
# apps/campus/services_clinic.py
import logging

from django.db.models import F, Q
from django.utils import timezone

from apps.identity.models import School, Student
from .models import ClinicPolicy, HealthProfile, MedicationStock

logger = logging.getLogger(__name__)


def get_or_create_clinic_policy(school: School) -> ClinicPolicy:
    """Gets or creates the ClinicPolicy for a given school (LIF-006)."""
    policy, _ = ClinicPolicy.objects.get_or_create(
        foundation_id=school.foundation_id,
        school=school,
        defaults={'teacher_sees_allergies': True},
    )
    return policy


def get_or_create_health_profile(student: Student) -> HealthProfile:
    """Gets or creates the HealthProfile for a given student (LIF-002)."""
    profile, _ = HealthProfile.objects.get_or_create(
        foundation_id=student.foundation_id,
        student=student,
    )
    return profile


def get_medication_stock_alerts(school: School):
    """LIF-005: stock rows below reorder_level or within 30 days of expiry."""
    today = timezone.now().date()
    horizon = today + timezone.timedelta(days=30)
    return MedicationStock.objects.filter(
        foundation_id=school.foundation_id,
        school=school,
        deleted_at__isnull=True,
    ).filter(
        Q(quantity__lt=F('reorder_level')) | Q(expiry_date__lte=horizon)
    ).order_by('expiry_date')
```

- [ ] **Step 4: Run tests**

Run: `python manage.py test apps.campus.tests.test_clinic_services -v 2`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/campus/services_clinic.py apps/campus/tests/test_clinic_services.py
git commit -m "feat(campus): clinic policy/health-profile/stock-alert services (LIF-002, LIF-005, LIF-006)"
```

---

### Task 7: Campus — `record_clinic_visit` core service (visit + consent + stock decrement)

**Files:**
- Modify: `apps/campus/services_clinic.py` (append)
- Test: `apps/campus/tests/test_clinic_services.py` (append)

**Interfaces:**
- Consumes: `apps.campus.crypto.encrypt_note` (Task 4); `apps.compliance.services.has_active_health_consent` (Task 3); `apps.compliance.models.DataSubjectRequestSubjectType`; `apps.core.services.audit`, `record_domain_event`.
- Produces: `record_clinic_visit(foundation_id, school, student, handled_by, complaint, outcome, treatment='', vitals=None, medication=None, medication_quantity=None, guardian_consent_confirmed=False, guardian_consent_note='', occurred_at=None) -> ClinicVisit`. Raises `django.core.exceptions.ValidationError` on invalid input, missing consent, or insufficient stock.

- [ ] **Step 1: Write the failing test**

Append to `apps/campus/tests/test_clinic_services.py`:

```python
import datetime as _dt

from django.core.exceptions import ValidationError

from apps.campus.crypto import decrypt_note
from apps.campus.models import ClinicOutcome, ClinicVisit, MedicationStock
from apps.campus.services_clinic import record_clinic_visit
from apps.compliance.models import ConsentPurpose, DataSubjectRequestSubjectType
from apps.compliance.services import record_consent
from apps.core.models import AuditEvent


class RecordClinicVisitTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        self.stock = MedicationStock.objects.create(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'],
            name='Paracetamol', unit='tablet', quantity=50, reorder_level=10,
            expiry_date=_dt.date(2030, 1, 1),
        )

    def test_record_visit_returned_to_class_no_medication(self):
        visit = record_clinic_visit(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            handled_by=self.fx['teacher'],
            complaint='Sakit kepala ringan',
            outcome=ClinicOutcome.RETURNED_TO_CLASS,
        )
        self.assertEqual(visit.outcome, ClinicOutcome.RETURNED_TO_CLASS)
        self.assertEqual(decrypt_note(visit.complaint_encrypted), 'Sakit kepala ringan')
        self.assertIsNone(visit.guardian_notified_at)
        self.assertTrue(AuditEvent.objects.filter(action='campus.clinic_visit.recorded', entity_id=str(visit.id)).exists())

    def test_record_visit_with_medication_requires_consent(self):
        with self.assertRaises(ValidationError):
            record_clinic_visit(
                foundation_id=self.fx['foundation'].id,
                school=self.fx['school'],
                student=self.fx['student'],
                handled_by=self.fx['teacher'],
                complaint='Demam',
                outcome=ClinicOutcome.RETURNED_TO_CLASS,
                medication=self.stock,
                medication_quantity=1,
            )

    def test_record_visit_with_medication_and_standing_consent_decrements_stock(self):
        record_consent(
            subject_type=DataSubjectRequestSubjectType.STUDENT,
            subject_id=self.fx['student'].id,
            foundation_id=self.fx['foundation'].id,
            purpose=ConsentPurpose.HEALTH_DATA,
            granted_by='tester',
        )
        visit = record_clinic_visit(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            handled_by=self.fx['teacher'],
            complaint='Demam',
            outcome=ClinicOutcome.RETURNED_TO_CLASS,
            medication=self.stock,
            medication_quantity=2,
        )
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, 48)
        self.assertEqual(visit.medication_quantity_used, 2)

    def test_record_visit_with_medication_and_per_incident_confirmation(self):
        visit = record_clinic_visit(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            handled_by=self.fx['teacher'],
            complaint='Demam',
            outcome=ClinicOutcome.RETURNED_TO_CLASS,
            medication=self.stock,
            medication_quantity=1,
            guardian_consent_confirmed=True,
            guardian_consent_note='Dikonfirmasi via telepon oleh ibu kandung',
        )
        self.assertTrue(visit.guardian_consent_confirmed)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, 49)

    def test_record_visit_rejects_insufficient_stock(self):
        with self.assertRaises(ValidationError):
            record_clinic_visit(
                foundation_id=self.fx['foundation'].id,
                school=self.fx['school'],
                student=self.fx['student'],
                handled_by=self.fx['teacher'],
                complaint='Demam',
                outcome=ClinicOutcome.RETURNED_TO_CLASS,
                medication=self.stock,
                medication_quantity=999,
                guardian_consent_confirmed=True,
            )
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, 50)

    def test_record_visit_rejects_wrong_school_student(self):
        other_fx = build_academic_fixture(foundation_name="Yayasan Lain")
        with self.assertRaises(ValidationError):
            record_clinic_visit(
                foundation_id=self.fx['foundation'].id,
                school=self.fx['school'],
                student=other_fx['student'],
                handled_by=self.fx['teacher'],
                complaint='Demam',
                outcome=ClinicOutcome.RETURNED_TO_CLASS,
            )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test apps.campus.tests.test_clinic_services.RecordClinicVisitTests -v 2`
Expected: FAIL — `ImportError: cannot import name 'record_clinic_visit'`

- [ ] **Step 3: Implement**

Append to `apps/campus/services_clinic.py` (add these imports at the top of the file, alongside the existing ones):

```python
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.core.services import audit, record_domain_event
from apps.identity.models import Staff
from .models import ClinicOutcome, ClinicVisit
```

Then append the function:

```python
def record_clinic_visit(
    foundation_id: int,
    school: School,
    student: Student,
    handled_by: Staff,
    complaint: str,
    outcome: str,
    treatment: str = '',
    vitals: dict = None,
    medication: MedicationStock = None,
    medication_quantity: int = None,
    guardian_consent_confirmed: bool = False,
    guardian_consent_note: str = '',
    occurred_at=None,
) -> ClinicVisit:
    """Records a clinic visit end-to-end (LIF-001, LIF-003, LIF-004, LIF-007)."""
    from .crypto import encrypt_note

    if outcome not in ClinicOutcome.values:
        raise ValidationError(f"Outcome klinik tidak valid: {outcome}")
    if student.school_id != school.id:
        raise ValidationError("Siswa tidak terdaftar di sekolah yang bersangkutan.")
    if not complaint or not complaint.strip():
        raise ValidationError("Keluhan wajib diisi.")

    if occurred_at is None:
        occurred_at = timezone.now()
    vitals = vitals or {}

    with transaction.atomic():
        if medication is not None:
            if medication.school_id != school.id:
                raise ValidationError("Stok obat tidak sesuai dengan sekolah yang bersangkutan.")
            if not medication_quantity or medication_quantity <= 0:
                raise ValidationError("Jumlah obat yang diberikan wajib diisi.")

            from apps.compliance.models import DataSubjectRequestSubjectType
            from apps.compliance.services import has_active_health_consent

            has_standing_consent = has_active_health_consent(
                subject_type=DataSubjectRequestSubjectType.STUDENT,
                subject_id=student.id,
                foundation_id=foundation_id,
            )
            if not has_standing_consent and not guardian_consent_confirmed:
                raise ValidationError(
                    "Pemberian obat memerlukan persetujuan wali (consent standing atau konfirmasi per-insiden)."
                )

            stock = MedicationStock.objects.select_for_update().get(pk=medication.pk)
            if stock.quantity - medication_quantity < 0:
                raise ValidationError(f"Stok obat '{stock.name}' tidak mencukupi.")
            stock.quantity = stock.quantity - medication_quantity
            stock.save(update_fields=['quantity', 'updated_at'])

        visit = ClinicVisit.objects.create(
            foundation_id=foundation_id,
            school=school,
            student=student,
            occurred_at=occurred_at,
            complaint_encrypted=encrypt_note(complaint.strip()),
            treatment_encrypted=encrypt_note(treatment.strip()) if treatment and treatment.strip() else '',
            vitals=vitals,
            medication_given=medication,
            medication_quantity_used=medication_quantity if medication is not None else None,
            outcome=outcome,
            handled_by=handled_by,
            guardian_consent_confirmed=guardian_consent_confirmed,
            guardian_consent_note=guardian_consent_note or '',
        )

        audit(
            action='campus.clinic_visit.recorded',
            entity_type='ClinicVisit',
            entity_id=str(visit.id),
            actor_id=str(handled_by.user_id) if handled_by else None,
            foundation_id=foundation_id,
            school_id=school.id,
            diff={
                'student_id': student.id,
                'outcome': outcome,
                'medication_id': medication.id if medication else None,
            },
        )
        record_domain_event(
            name='campus.clinic_visit.recorded',
            foundation_id=foundation_id,
            payload={'visit_id': visit.id, 'student_id': student.id, 'outcome': outcome},
        )

    return visit
```

- [ ] **Step 4: Run tests**

Run: `python manage.py test apps.campus.tests.test_clinic_services -v 2`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/campus/services_clinic.py apps/campus/tests/test_clinic_services.py
git commit -m "feat(campus): record_clinic_visit service with consent gate + stock decrement (LIF-001, LIF-004)"
```

---

### Task 8: Campus — SAKIT override + `CLINIC_INCIDENT` notification on SENT_HOME/REFERRED

**Files:**
- Modify: `apps/campus/services_clinic.py` (append helpers, wire into `record_clinic_visit`)
- Test: `apps/campus/tests/test_clinic_services.py` (append)

**Interfaces:**
- Consumes: `apps.attendance.models.AttendanceDay/AttendanceSource/AttendanceStatus`; `apps.identity.models.GuardianLink`; `apps.academic.models.ClassEnrollment`; `apps.notifications.services.dispatch_intent`; `apps.notifications.models.NotificationCategory.CLINIC_INCIDENT` (Task 2).
- Produces: `record_clinic_visit` now applies a `SAKIT` `AttendanceDay` override and dispatches `CLINIC_INCIDENT` notifications when `outcome in (SENT_HOME, REFERRED)`, and stamps `guardian_notified_at`.

- [ ] **Step 1: Write the failing test**

Append to `apps/campus/tests/test_clinic_services.py`:

```python
from apps.academic.models import ClassEnrollment
from apps.attendance.models import AttendanceDay, AttendanceStatus
from apps.identity.models import Guardian, GuardianLink, Person, User
from apps.notifications.models import NotificationCategory, NotificationIntent


def enroll_student_in_class(fx):
    return ClassEnrollment.objects.create(
        foundation_id=fx['foundation'].id,
        student=fx['student'],
        class_group=fx['class_group'],
        enrolled_at=_dt.date(2026, 7, 1),
        is_active=True,
    )


def attach_guardian_to(fx, nik="3471010101019999", full_name="Pak Joko"):
    person = Person.all_tenants.create(foundation_id=fx['foundation'].id, nik=nik, full_name=full_name)
    user = User.objects.create(
        foundation_id=fx['foundation'].id,
        phone_e164=f"+62818{nik[-7:]}",
        email=f"{nik}@wali.sch.id",
        full_name=full_name,
    )
    guardian = Guardian.all_tenants.create(foundation_id=fx['foundation'].id, person=person, user=user)
    GuardianLink.all_tenants.create(
        foundation_id=fx['foundation'].id, guardian=guardian, student=fx['student'],
        relation=GuardianLink.RELATION_FATHER, financial_responsible=True,
    )
    return guardian


class ClinicVisitAttendanceAndNotificationTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        enroll_student_in_class(self.fx)
        self.guardian = attach_guardian_to(self.fx)

    def test_returned_to_class_does_not_touch_attendance_or_notify(self):
        visit = record_clinic_visit(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'], student=self.fx['student'],
            handled_by=self.fx['teacher'], complaint='Pusing ringan', outcome=ClinicOutcome.RETURNED_TO_CLASS,
        )
        self.assertIsNone(visit.guardian_notified_at)
        self.assertFalse(AttendanceDay.objects.filter(student=self.fx['student']).exists())
        self.assertFalse(NotificationIntent.objects.filter(category=NotificationCategory.CLINIC_INCIDENT).exists())

    def test_sent_home_creates_sakit_override_and_notifies_guardian_and_homeroom(self):
        visit = record_clinic_visit(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'], student=self.fx['student'],
            handled_by=self.fx['teacher'], complaint='Demam tinggi', outcome=ClinicOutcome.SENT_HOME,
        )
        visit.refresh_from_db()
        self.assertIsNotNone(visit.guardian_notified_at)

        att_day = AttendanceDay.objects.get(student=self.fx['student'], date=visit.occurred_at.date())
        self.assertEqual(att_day.status, AttendanceStatus.SAKIT)
        self.assertTrue(att_day.is_override)

        intents = NotificationIntent.objects.filter(category=NotificationCategory.CLINIC_INCIDENT)
        recipient_users = set(intents.values_list('recipient_user_id', flat=True))
        self.assertIn(self.guardian.user_id, recipient_users)
        self.assertIn(self.fx['teacher_user'].id, recipient_users)

    def test_referred_also_creates_sakit_override(self):
        visit = record_clinic_visit(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'], student=self.fx['student'],
            handled_by=self.fx['teacher'], complaint='Cedera serius', outcome=ClinicOutcome.REFERRED,
        )
        att_day = AttendanceDay.objects.get(student=self.fx['student'], date=visit.occurred_at.date())
        self.assertEqual(att_day.status, AttendanceStatus.SAKIT)

    def test_sent_home_overrides_existing_attendance_day(self):
        AttendanceDay.objects.create(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'], student=self.fx['student'],
            date=timezone.now().date(), status=AttendanceStatus.HADIR,
        )
        record_clinic_visit(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'], student=self.fx['student'],
            handled_by=self.fx['teacher'], complaint='Demam', outcome=ClinicOutcome.SENT_HOME,
        )
        att_day = AttendanceDay.objects.get(student=self.fx['student'], date=timezone.now().date())
        self.assertEqual(att_day.status, AttendanceStatus.SAKIT)
        self.assertEqual(att_day.original_status, AttendanceStatus.HADIR)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test apps.campus.tests.test_clinic_services.ClinicVisitAttendanceAndNotificationTests -v 2`
Expected: FAIL — `AttendanceDay.DoesNotExist` (no override applied yet).

- [ ] **Step 3: Implement**

Append to `apps/campus/services_clinic.py`:

```python
def _get_homeroom_teacher(student: Student):
    from apps.academic.models import ClassEnrollment

    enrollment = ClassEnrollment.objects.filter(
        foundation_id=student.foundation_id,
        student=student,
        is_active=True,
        deleted_at__isnull=True,
    ).select_related('class_group__homeroom_teacher__person', 'class_group__homeroom_teacher__user').first()
    return enrollment.class_group.homeroom_teacher if (enrollment and enrollment.class_group) else None


def _apply_sakit_override(visit: ClinicVisit) -> None:
    """LIF-003: create/override today's AttendanceDay to SAKIT for the visit's student."""
    from apps.attendance.models import AttendanceDay, AttendanceSource, AttendanceStatus

    actor_id = str(visit.handled_by.user_id) if visit.handled_by else ''
    note_text = f"Klinik: {visit.get_outcome_display()} (kunjungan #{visit.id})"
    visit_date = visit.occurred_at.date()

    att_day = AttendanceDay.all_tenants.filter(
        foundation_id=visit.foundation_id,
        school=visit.school,
        student=visit.student,
        date=visit_date,
        deleted_at__isnull=True,
    ).first()

    if att_day:
        if not att_day.is_override:
            att_day.original_status = att_day.status
        att_day.status = AttendanceStatus.SAKIT
        att_day.source = AttendanceSource.MANUAL
        att_day.is_override = True
        att_day.note = note_text
        att_day.updated_by = actor_id
        att_day.save(update_fields=['status', 'original_status', 'source', 'is_override', 'note', 'updated_by', 'updated_at'])
    else:
        AttendanceDay.objects.create(
            foundation_id=visit.foundation_id,
            school=visit.school,
            student=visit.student,
            date=visit_date,
            status=AttendanceStatus.SAKIT,
            source=AttendanceSource.MANUAL,
            is_override=True,
            note=note_text,
            created_by=actor_id,
        )


def _dispatch_clinic_incident_notifications(visit: ClinicVisit) -> int:
    """LIF-003: notify all guardians + homeroom teacher immediately."""
    from apps.identity.models import GuardianLink
    from apps.notifications.models import NotificationCategory, NotificationPriority
    from apps.notifications.services import dispatch_intent

    student = visit.student
    school = visit.school
    foundation_id = visit.foundation_id

    guardian_links = GuardianLink.objects.filter(
        foundation_id=foundation_id,
        student=student,
        deleted_at__isnull=True,
    ).select_related('guardian__person', 'guardian__user')

    student_name = student.person.full_name if (student.person and student.person.full_name) else (student.nis or 'Siswa')
    school_name = school.name if school else 'Sekolah'
    outcome_label = visit.get_outcome_display()

    recipients = []
    for link in guardian_links:
        guardian = link.guardian
        recipient_name = guardian.person.full_name if (guardian.person and guardian.person.full_name) else 'Wali Murid'
        recipients.append((guardian.user, recipient_name))

    homeroom_teacher = _get_homeroom_teacher(student)
    if homeroom_teacher and homeroom_teacher.user:
        teacher_name = homeroom_teacher.person.full_name if (homeroom_teacher.person and homeroom_teacher.person.full_name) else 'Wali Kelas'
        recipients.append((homeroom_teacher.user, teacher_name))

    dispatched = 0
    for user, recipient_name in recipients:
        if not user:
            continue
        phone = getattr(user, 'phone_e164', '') or ''
        email = getattr(user, 'email', '') or ''
        dedupe_key = f"clinic_incident:{visit.id}:{user.id}"
        payload = {
            'type': NotificationCategory.CLINIC_INCIDENT,
            'student_id': student.id,
            'student_name': student_name,
            'school_name': school_name,
            'outcome': visit.outcome,
            'outcome_label': outcome_label,
            'visit_id': visit.id,
        }
        dispatch_intent(
            foundation_id=foundation_id,
            school_id=school.id,
            recipient_user=user,
            recipient_phone=phone,
            recipient_email=email,
            recipient_name=recipient_name,
            category=NotificationCategory.CLINIC_INCIDENT,
            template_key='clinic.incident',
            payload=payload,
            priority=NotificationPriority.HIGH,
            dedupe_key=dedupe_key,
            immediate=True,
        )
        dispatched += 1

    if dispatched:
        visit.guardian_notified_at = timezone.now()
        visit.save(update_fields=['guardian_notified_at', 'updated_at'])

    return dispatched
```

Then modify `record_clinic_visit` (from Task 7): change its `return visit` line at the very end to:

```python
    if outcome in (ClinicOutcome.SENT_HOME, ClinicOutcome.REFERRED):
        _apply_sakit_override(visit)
        _dispatch_clinic_incident_notifications(visit)

    return visit
```

(Keep this outside the `with transaction.atomic():` block — attendance/notification side-effects run after the visit + stock decrement commit, same as `record_behaviour`'s `_dispatch_behaviour_notifications` call pattern.)

- [ ] **Step 4: Run tests**

Run: `python manage.py test apps.campus.tests.test_clinic_services -v 2`
Expected: PASS, full file green.

- [ ] **Step 5: Commit**

```bash
git add apps/campus/services_clinic.py apps/campus/tests/test_clinic_services.py
git commit -m "feat(campus): SAKIT attendance override + CLINIC_INCIDENT notification on SENT_HOME/REFERRED (LIF-003)"
```

---

### Task 9: Campus — clinic serializers

**Files:**
- Modify: `apps/campus/serializers.py` (append)
- Test: `apps/campus/tests/test_clinic_serializers.py`

**Interfaces:**
- Consumes: `ClinicPolicy`, `HealthProfile`, `MedicationStock`, `ClinicVisit`, `ClinicOutcome` (Task 5); `apps.campus.crypto.decrypt_note` (Task 4).
- Produces: `ClinicPolicySerializer`, `HealthProfileSerializer`, `MedicationStockSerializer`, `ClinicVisitSerializer` (decrypts `complaint`/`treatment` for output), `RecordClinicVisitInputSerializer`, `StudentMedicalAlertSerializer`.

- [ ] **Step 1: Write the failing test**

```python
# apps/campus/tests/test_clinic_serializers.py
import datetime

from django.test import TestCase

from apps.academic.tests.base import build_academic_fixture
from apps.campus.crypto import encrypt_note
from apps.campus.models import ClinicOutcome, ClinicVisit, MedicationStock
from apps.campus.serializers import ClinicVisitSerializer, RecordClinicVisitInputSerializer


class ClinicSerializerTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()

    def test_clinic_visit_serializer_decrypts_complaint(self):
        visit = ClinicVisit.objects.create(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            complaint_encrypted=encrypt_note('Sakit perut'),
            treatment_encrypted=encrypt_note('Diberi obat maag'),
            outcome=ClinicOutcome.RETURNED_TO_CLASS,
            handled_by=self.fx['teacher'],
        )
        data = ClinicVisitSerializer(visit).data
        self.assertEqual(data['complaint'], 'Sakit perut')
        self.assertEqual(data['treatment'], 'Diberi obat maag')
        self.assertNotIn('complaint_encrypted', data)

    def test_record_clinic_visit_input_serializer_valid(self):
        serializer = RecordClinicVisitInputSerializer(data={
            'student_id': self.fx['student'].id,
            'complaint': 'Demam',
            'outcome': ClinicOutcome.SENT_HOME,
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data['guardian_consent_confirmed'], False)

    def test_record_clinic_visit_input_serializer_rejects_bad_outcome(self):
        serializer = RecordClinicVisitInputSerializer(data={
            'student_id': self.fx['student'].id,
            'complaint': 'Demam',
            'outcome': 'RESTING_IN_UKS',
        })
        self.assertFalse(serializer.is_valid())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test apps.campus.tests.test_clinic_serializers -v 2`
Expected: FAIL — `ImportError: cannot import name 'ClinicVisitSerializer'`

- [ ] **Step 3: Implement**

Append to `apps/campus/serializers.py` (add `ClinicOutcome, ClinicPolicy, ClinicVisit, HealthProfile, MedicationStock` to the existing `from .models import (...)` block at the top of the file):

```python
class ClinicPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = ClinicPolicy
        fields = ['id', 'school', 'teacher_sees_allergies', 'created_at', 'updated_at']
        read_only_fields = ['id', 'school', 'created_at', 'updated_at']


class HealthProfileSerializer(serializers.ModelSerializer):
    has_medical_alert = serializers.BooleanField(read_only=True)

    class Meta:
        model = HealthProfile
        fields = [
            'id', 'student', 'blood_type', 'allergies', 'chronic_conditions',
            'medications', 'emergency_contacts', 'has_medical_alert',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'student', 'created_at', 'updated_at']


class MedicationStockSerializer(serializers.ModelSerializer):
    class Meta:
        model = MedicationStock
        fields = ['id', 'school', 'name', 'unit', 'quantity', 'expiry_date', 'reorder_level', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


class ClinicVisitSerializer(serializers.ModelSerializer):
    student_name = serializers.SerializerMethodField()
    handled_by_name = serializers.SerializerMethodField()
    medication_name = serializers.CharField(source='medication_given.name', read_only=True, default=None)
    complaint = serializers.SerializerMethodField()
    treatment = serializers.SerializerMethodField()

    class Meta:
        model = ClinicVisit
        fields = [
            'id', 'school', 'student', 'student_name', 'occurred_at', 'complaint', 'treatment',
            'vitals', 'medication_given', 'medication_name', 'medication_quantity_used', 'outcome',
            'handled_by', 'handled_by_name', 'guardian_consent_confirmed', 'guardian_consent_note',
            'guardian_notified_at', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_student_name(self, obj):
        return obj.student.person.full_name if (obj.student.person and obj.student.person.full_name) else obj.student.nis

    def get_handled_by_name(self, obj):
        return obj.handled_by.person.full_name if (obj.handled_by and obj.handled_by.person) else ''

    def get_complaint(self, obj):
        from .crypto import decrypt_note
        return decrypt_note(obj.complaint_encrypted) if obj.complaint_encrypted else ''

    def get_treatment(self, obj):
        from .crypto import decrypt_note
        return decrypt_note(obj.treatment_encrypted) if obj.treatment_encrypted else ''


class RecordClinicVisitInputSerializer(serializers.Serializer):
    student_id = serializers.IntegerField(required=True)
    complaint = serializers.CharField(required=True, min_length=1)
    treatment = serializers.CharField(required=False, allow_blank=True, default='')
    vitals = serializers.DictField(required=False, default=dict)
    outcome = serializers.ChoiceField(choices=ClinicOutcome.choices, required=True)
    medication_id = serializers.IntegerField(required=False, allow_null=True)
    medication_quantity = serializers.IntegerField(required=False, allow_null=True)
    guardian_consent_confirmed = serializers.BooleanField(required=False, default=False)
    guardian_consent_note = serializers.CharField(required=False, allow_blank=True, default='')
    occurred_at = serializers.DateTimeField(required=False, allow_null=True)


class StudentMedicalAlertSerializer(serializers.Serializer):
    has_medical_alert = serializers.BooleanField()
    allergies = serializers.ListField(child=serializers.CharField(), required=False, default=list)
```

- [ ] **Step 4: Run tests**

Run: `python manage.py test apps.campus.tests.test_clinic_serializers -v 2`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/campus/serializers.py apps/campus/tests/test_clinic_serializers.py
git commit -m "feat(campus): clinic serializers (LIF-001..007)"
```

---

### Task 10: Campus — clinic views (visits, policy, health profile, medical alert)

**Files:**
- Modify: `apps/campus/views.py` (append)
- Test: `apps/campus/tests/test_clinic_views.py`

**Interfaces:**
- Consumes: everything from Tasks 5-9; `apps.identity.permissions.HasRequiredPermission`; `apps.identity.guardian_access.can_guardian_access_student`, `get_guardian_student_ids`, `STAFF_ROLES`.
- Produces: `ClinicVisitViewSet`, `ClinicPolicyView`, `StudentHealthProfileView`, `StudentMedicalAlertView`.

- [ ] **Step 1: Write the failing test**

```python
# apps/campus/tests/test_clinic_views.py
import datetime as _dt

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.academic.models import ClassEnrollment
from apps.academic.tests.base import build_academic_fixture
from apps.campus.models import ClinicOutcome, ClinicVisit, HealthProfile, MedicationStock
from apps.identity.models import RoleAssignment
from apps.identity.rbac import ROLE_CLINIC_OFFICER, ROLE_SCHOOL_ADMIN, ROLE_TEACHER
from educore.middleware.tenancy import set_current_foundation_id


def assign(fx, user, role, scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=None):
    RoleAssignment.all_tenants.create(
        foundation_id=fx['foundation'].id,
        user=user,
        role=role,
        scope_type=scope_type,
        scope_id=scope_id if scope_id is not None else fx['school'].id,
    )


class ClinicVisitViewSetTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        set_current_foundation_id(self.fx['foundation'].id)
        assign(self.fx, self.fx['teacher_user'], ROLE_CLINIC_OFFICER)
        self.client = APIClient()
        self.client.force_authenticate(user=self.fx['teacher_user'])

    def test_create_clinic_visit_as_clinic_officer(self):
        resp = self.client.post('/api/v1/campus/clinic-visits/', {
            'student_id': self.fx['student'].id,
            'complaint': 'Demam tinggi',
            'outcome': ClinicOutcome.RETURNED_TO_CLASS,
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.content)
        self.assertEqual(resp.data['complaint'], 'Demam tinggi')
        self.assertTrue(ClinicVisit.objects.filter(student=self.fx['student']).exists())

    def test_create_clinic_visit_rejects_bad_outcome(self):
        resp = self.client.post('/api/v1/campus/clinic-visits/', {
            'student_id': self.fx['student'].id,
            'complaint': 'Demam tinggi',
            'outcome': 'RESTING_IN_UKS',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_teacher_without_clinic_write_cannot_create(self):
        other_person_user = self.fx['teacher_user']
        RoleAssignment.all_tenants.filter(user=other_person_user, role=ROLE_CLINIC_OFFICER).delete()
        assign(self.fx, other_person_user, ROLE_TEACHER)
        resp = self.client.post('/api/v1/campus/clinic-visits/', {
            'student_id': self.fx['student'].id,
            'complaint': 'Demam tinggi',
            'outcome': ClinicOutcome.RETURNED_TO_CLASS,
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


class StudentMedicalAlertViewTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        set_current_foundation_id(self.fx['foundation'].id)
        ClassEnrollment.objects.create(
            foundation_id=self.fx['foundation'].id, student=self.fx['student'],
            class_group=self.fx['class_group'], enrolled_at=_dt.date(2026, 7, 1), is_active=True,
        )
        HealthProfile.objects.create(
            foundation_id=self.fx['foundation'].id, student=self.fx['student'],
            allergies=['Penisilin'],
        )
        assign(self.fx, self.fx['teacher_user'], ROLE_TEACHER)
        self.client = APIClient()
        self.client.force_authenticate(user=self.fx['teacher_user'])

    def test_teacher_sees_alert_flag_and_allergies_when_policy_enabled(self):
        resp = self.client.get(f"/api/v1/campus/students/{self.fx['student'].id}/medical-alert/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data['has_medical_alert'])
        self.assertEqual(resp.data['allergies'], ['Penisilin'])

    def test_teacher_sees_flag_only_when_policy_disabled(self):
        from apps.campus.services_clinic import get_or_create_clinic_policy
        policy = get_or_create_clinic_policy(self.fx['school'])
        policy.teacher_sees_allergies = False
        policy.save(update_fields=['teacher_sees_allergies'])

        resp = self.client.get(f"/api/v1/campus/students/{self.fx['student'].id}/medical-alert/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data['has_medical_alert'])
        self.assertEqual(resp.data.get('allergies', []), [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test apps.campus.tests.test_clinic_views -v 2`
Expected: FAIL — 404 (URL not wired yet / view not defined).

- [ ] **Step 3: Implement**

Add to the top-of-file imports in `apps/campus/views.py`:

```python
from .models import ClinicPolicy, ClinicVisit, HealthProfile, MedicationStock
from .serializers import (
    ClinicPolicySerializer,
    ClinicVisitSerializer,
    HealthProfileSerializer,
    MedicationStockSerializer,
    RecordClinicVisitInputSerializer,
    StudentMedicalAlertSerializer,
)
from .services_clinic import (
    get_medication_stock_alerts,
    get_or_create_clinic_policy,
    get_or_create_health_profile,
    record_clinic_visit,
)
```

(Add `Staff` to the existing `from apps.identity.models import Guardian, School, Student` import line, making it `from apps.identity.models import Guardian, School, Staff, Student`.)

Append the view classes:

```python
class ClinicPolicyView(APIView):
    """Retrieve and update school clinic policy (LIF-006)."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'clinic.read'

    def get_required_permission(self):
        if self.request.method in ['PUT', 'PATCH']:
            return 'clinic.write'
        return 'clinic.read'

    def get(self, request, school_id: int):
        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        try:
            school = School.objects.get(pk=school_id, foundation_id=foundation_id, deleted_at__isnull=True)
        except School.DoesNotExist:
            raise exceptions.NotFound("Sekolah tidak ditemukan.")

        policy = get_or_create_clinic_policy(school)
        serializer = ClinicPolicySerializer(policy)
        return Response(serializer.data)

    def put(self, request, school_id: int):
        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        try:
            school = School.objects.get(pk=school_id, foundation_id=foundation_id, deleted_at__isnull=True)
        except School.DoesNotExist:
            raise exceptions.NotFound("Sekolah tidak ditemukan.")

        policy = get_or_create_clinic_policy(school)
        serializer = ClinicPolicySerializer(policy, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class MedicationStockViewSet(viewsets.ModelViewSet):
    """CRUD for UKS medication/first-aid inventory (LIF-004, LIF-005)."""
    queryset = MedicationStock.objects.all()
    serializer_class = MedicationStockSerializer
    permission_classes = [HasRequiredPermission]
    required_permission = 'clinic.read'
    action_permissions = {
        'create': 'clinic.write',
        'update': 'clinic.write',
        'partial_update': 'clinic.write',
        'destroy': 'clinic.write',
    }
    pagination_class = StandardCursorPagination

    def get_queryset(self):
        foundation_id = get_current_foundation_id()
        if not foundation_id:
            return MedicationStock.objects.none()
        qs = MedicationStock.objects.filter(foundation_id=foundation_id, deleted_at__isnull=True).order_by('name')
        school_id = self.request.query_params.get('school_id')
        if school_id:
            qs = qs.filter(school_id=school_id)
        return qs

    def perform_create(self, serializer):
        foundation_id = get_current_foundation_id() or getattr(self.request.user, 'foundation_id', None)
        serializer.save(foundation_id=foundation_id)


class MedicationStockAlertView(APIView):
    """LIF-005: below-reorder-level or near-expiry medication stock for the clinic dashboard."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'clinic.read'

    def get(self, request, school_id: int):
        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        try:
            school = School.objects.get(pk=school_id, foundation_id=foundation_id, deleted_at__isnull=True)
        except School.DoesNotExist:
            raise exceptions.NotFound("Sekolah tidak ditemukan.")

        alerts = get_medication_stock_alerts(school)
        serializer = MedicationStockSerializer(alerts, many=True)
        return Response(serializer.data)


class StudentHealthProfileView(APIView):
    """Retrieve and update a student's health profile (LIF-002)."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'clinic.read'

    def get_required_permission(self):
        if self.request.method in ['PUT', 'PATCH']:
            return 'clinic.write'
        return 'clinic.read'

    def get(self, request, student_id: int):
        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        try:
            student = Student.objects.get(pk=student_id, foundation_id=foundation_id, deleted_at__isnull=True)
        except Student.DoesNotExist:
            raise exceptions.NotFound("Siswa tidak ditemukan.")

        from apps.identity.guardian_access import can_guardian_access_student
        if not can_guardian_access_student(request.user, student.id, foundation_id):
            raise exceptions.NotFound("Siswa tidak ditemukan.")

        profile = get_or_create_health_profile(student)
        serializer = HealthProfileSerializer(profile)
        return Response(serializer.data)

    def put(self, request, student_id: int):
        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        try:
            student = Student.objects.get(pk=student_id, foundation_id=foundation_id, deleted_at__isnull=True)
        except Student.DoesNotExist:
            raise exceptions.NotFound("Siswa tidak ditemukan.")

        profile = get_or_create_health_profile(student)
        serializer = HealthProfileSerializer(profile, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class StudentMedicalAlertView(APIView):
    """LIF-006: teacher-facing non-clinical medical alert flag."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'behaviour.read'

    def get(self, request, student_id: int):
        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        try:
            student = Student.objects.get(pk=student_id, foundation_id=foundation_id, deleted_at__isnull=True)
        except Student.DoesNotExist:
            raise exceptions.NotFound("Siswa tidak ditemukan.")

        profile = get_or_create_health_profile(student)
        policy = get_or_create_clinic_policy(student.school)

        data = {'has_medical_alert': profile.has_medical_alert}
        if policy.teacher_sees_allergies:
            data['allergies'] = profile.allergies
        serializer = StudentMedicalAlertSerializer(data)
        return Response(serializer.data)


class ClinicVisitViewSet(viewsets.ModelViewSet):
    """Records and lists UKS clinic visits (LIF-001, LIF-003, LIF-004, LIF-007)."""
    queryset = ClinicVisit.objects.all().select_related('student__person', 'handled_by__person', 'medication_given')
    serializer_class = ClinicVisitSerializer
    permission_classes = [HasRequiredPermission]
    required_permission = 'clinic.read'
    action_permissions = {
        'create': 'clinic.write',
    }
    pagination_class = StandardCursorPagination

    def get_queryset(self):
        foundation_id = get_current_foundation_id()
        if not foundation_id:
            return ClinicVisit.objects.none()
        qs = ClinicVisit.objects.filter(foundation_id=foundation_id, deleted_at__isnull=True).select_related(
            'student__person', 'handled_by__person', 'medication_given'
        ).order_by('-occurred_at')

        user = self.request.user
        if not user or not user.is_authenticated:
            return ClinicVisit.objects.none()

        if not user.is_superuser:
            from apps.identity.models import RoleAssignment
            from apps.identity.guardian_access import get_guardian_student_ids, STAFF_ROLES

            has_fnd_admin = RoleAssignment.all_tenants.filter(
                foundation_id=foundation_id,
                user=user,
                role=RoleAssignment.ROLE_FOUNDATION_ADMIN,
                scope_type=RoleAssignment.SCOPE_FOUNDATION,
                deleted_at__isnull=True,
            ).exists()

            if not has_fnd_admin:
                staff_school_ids = set(RoleAssignment.all_tenants.filter(
                    foundation_id=foundation_id,
                    user=user,
                    role__in=STAFF_ROLES | {RoleAssignment.ROLE_CLINIC_OFFICER},
                    scope_type=RoleAssignment.SCOPE_SCHOOL,
                    deleted_at__isnull=True,
                ).values_list('scope_id', flat=True))

                parent_student_ids = get_guardian_student_ids(user, foundation_id)

                if staff_school_ids and parent_student_ids:
                    qs = qs.filter(Q(school_id__in=staff_school_ids) | Q(student_id__in=parent_student_ids))
                elif staff_school_ids:
                    qs = qs.filter(school_id__in=staff_school_ids)
                elif parent_student_ids:
                    qs = qs.filter(student_id__in=parent_student_ids)
                else:
                    return ClinicVisit.objects.none()

        student_id = self.request.query_params.get('student_id')
        if student_id:
            qs = qs.filter(student_id=student_id)
        school_id = self.request.query_params.get('school_id')
        if school_id:
            qs = qs.filter(school_id=school_id)
        return qs

    def create(self, request, *args, **kwargs):
        serializer = RecordClinicVisitInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        try:
            student = Student.objects.get(pk=data['student_id'], foundation_id=foundation_id, deleted_at__isnull=True)
        except Student.DoesNotExist:
            raise exceptions.NotFound("Siswa tidak ditemukan.")

        handled_by = Staff.objects.filter(user=request.user, foundation_id=foundation_id, deleted_at__isnull=True).first()
        if not handled_by:
            raise exceptions.ValidationError("Pengguna saat ini tidak terdaftar sebagai staf.")

        medication = None
        if data.get('medication_id'):
            try:
                medication = MedicationStock.objects.get(pk=data['medication_id'], foundation_id=foundation_id, deleted_at__isnull=True)
            except MedicationStock.DoesNotExist:
                raise exceptions.NotFound("Stok obat tidak ditemukan.")

        try:
            visit = record_clinic_visit(
                foundation_id=foundation_id,
                school=student.school,
                student=student,
                handled_by=handled_by,
                complaint=data['complaint'],
                treatment=data.get('treatment', ''),
                vitals=data.get('vitals') or {},
                outcome=data['outcome'],
                medication=medication,
                medication_quantity=data.get('medication_quantity'),
                guardian_consent_confirmed=data.get('guardian_consent_confirmed', False),
                guardian_consent_note=data.get('guardian_consent_note', ''),
                occurred_at=data.get('occurred_at'),
            )
        except Exception as exc:
            raise exceptions.ValidationError(str(exc))

        output_serializer = ClinicVisitSerializer(visit)
        return Response(output_serializer.data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        raise exceptions.MethodNotAllowed("PUT", detail="Kunjungan klinik tidak dapat diubah setelah dicatat.")

    def partial_update(self, request, *args, **kwargs):
        raise exceptions.MethodNotAllowed("PATCH", detail="Kunjungan klinik tidak dapat diubah setelah dicatat.")

    def destroy(self, request, *args, **kwargs):
        raise exceptions.MethodNotAllowed("DELETE", detail="Kunjungan klinik tidak dapat dihapus.")
```

Add `Q` to the existing `from rest_framework import ...` imports section — actually `Q` comes from Django, so add `from django.db.models import Q` near the top of `apps/campus/views.py` (it isn't imported yet in that file).

- [ ] **Step 4: Wire URLs**

Modify `apps/campus/urls.py`:

```python
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    BehaviourCaseViewSet,
    BehaviourPolicyView,
    BehaviourReasonViewSet,
    BehaviourRecordViewSet,
    ClinicPolicyView,
    ClinicVisitViewSet,
    MedicationStockAlertView,
    MedicationStockViewSet,
    StudentBehaviourSummaryView,
    StudentHealthProfileView,
    StudentMedicalAlertView,
)

router = DefaultRouter()
router.register(r'behaviour-reasons', BehaviourReasonViewSet, basename='behaviour-reasons')
router.register(r'behaviour-records', BehaviourRecordViewSet, basename='behaviour-records')
router.register(r'behaviour-cases', BehaviourCaseViewSet, basename='behaviour-cases')
router.register(r'clinic-visits', ClinicVisitViewSet, basename='clinic-visits')
router.register(r'medication-stock', MedicationStockViewSet, basename='medication-stock')

urlpatterns = [
    path('schools/<int:school_id>/behaviour-policy/', BehaviourPolicyView.as_view(), name='behaviour-policy'),
    path('schools/<int:school_id>/clinic-policy/', ClinicPolicyView.as_view(), name='clinic-policy'),
    path('schools/<int:school_id>/medication-stock/alerts/', MedicationStockAlertView.as_view(), name='medication-stock-alerts'),
    path('students/<int:student_id>/behaviour/', StudentBehaviourSummaryView.as_view(), name='student-behaviour-summary'),
    path('students/<int:student_id>/health-profile/', StudentHealthProfileView.as_view(), name='student-health-profile'),
    path('students/<int:student_id>/medical-alert/', StudentMedicalAlertView.as_view(), name='student-medical-alert'),
    path('', include(router.urls)),
]
```

Before running tests, confirm the mount prefix used in the test URLs above (`/api/v1/campus/...`) matches the real project URL config: run `grep -rn "apps.campus.urls\|'campus/'" educore/urls.py apps/*/urls.py`. If the actual prefix differs, use that prefix in the test file instead (fix the test, not the app).

- [ ] **Step 5: Run tests**

Run: `python manage.py test apps.campus.tests.test_clinic_views -v 2`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/campus/views.py apps/campus/urls.py apps/campus/tests/test_clinic_views.py
git commit -m "feat(campus): clinic REST endpoints — visits, policy, health profile, medical alert (LIF-001..007)"
```

---

### Task 11: Cross-tenant isolation tests (Layer 3 tenancy)

**Files:**
- Modify: `apps/campus/tests/test_clinic_views.py` (append)

**Interfaces:**
- Consumes: `ClinicVisitViewSet`, `MedicationStockViewSet` (Task 10).

- [ ] **Step 1: Write the failing test**

Append to `apps/campus/tests/test_clinic_views.py`:

```python
class ClinicCrossTenantIsolationTests(TestCase):
    def setUp(self):
        self.fx_a = build_academic_fixture(foundation_name="Yayasan A")
        self.fx_b = build_academic_fixture(foundation_name="Yayasan B")
        set_current_foundation_id(self.fx_a['foundation'].id)
        assign(self.fx_a, self.fx_a['teacher_user'], ROLE_CLINIC_OFFICER)

        self.visit_b = ClinicVisit.objects.create(
            foundation_id=self.fx_b['foundation'].id,
            school=self.fx_b['school'],
            student=self.fx_b['student'],
            complaint_encrypted='x',
            outcome=ClinicOutcome.RETURNED_TO_CLASS,
            handled_by=self.fx_b['teacher'],
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.fx_a['teacher_user'])

    def test_cannot_read_other_foundation_clinic_visit(self):
        set_current_foundation_id(self.fx_a['foundation'].id)
        resp = self.client.get(f'/api/v1/campus/clinic-visits/{self.visit_b.id}/')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_medication_stock_alerts_scoped_to_own_school(self):
        MedicationStock.objects.create(
            foundation_id=self.fx_b['foundation'].id, school=self.fx_b['school'],
            name='Obat B', unit='tablet', quantity=1, reorder_level=100,
            expiry_date=_dt.date(2030, 1, 1),
        )
        set_current_foundation_id(self.fx_a['foundation'].id)
        resp = self.client.get(f"/api/v1/campus/schools/{self.fx_a['school'].id}/medication-stock/alerts/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data, [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test apps.campus.tests.test_clinic_views.ClinicCrossTenantIsolationTests -v 2`
Expected: This should actually already PASS given `TenantManager`'s fail-closed-by-`foundation_id` scoping and the viewset's `get_current_foundation_id()`-scoped `get_queryset` — run it first to confirm the isolation genuinely holds (it is a regression guard, not new behavior to build).

- [ ] **Step 3: If it fails, fix**

If `test_cannot_read_other_foundation_clinic_visit` unexpectedly returns 200, the bug is in `ClinicVisitViewSet.get_queryset` not filtering by `foundation_id` before the RBAC-scoping block — re-check Task 10's `get_queryset` matches the pattern given exactly (it filters `foundation_id=foundation_id` as the very first clause).

- [ ] **Step 4: Run tests**

Run: `python manage.py test apps.campus.tests.test_clinic_views -v 2`
Expected: PASS, full file green.

- [ ] **Step 5: Commit**

```bash
git add apps/campus/tests/test_clinic_views.py
git commit -m "test(campus): cross-tenant isolation guard for clinic endpoints (memory/00_CORE.md Layer 3)"
```

---

### Task 12: Full-suite verification and Notion sync

**Files:** none (verification-only task)

- [ ] **Step 1: Run the full `apps.campus`, `apps.identity`, `apps.notifications`, `apps.compliance`, and `apps.attendance` suites**

Run: `python manage.py test apps.campus apps.identity apps.notifications apps.compliance apps.attendance -v 2`
Expected: All PASS (or only the two pre-existing unrelated finance failures already known to be present on `main`, per prior retrospectives in `memory/01_PROJECT.md` — confirm via `git stash` + re-run on `main` if any unexpected failure appears, same protocol used in every prior task in this repo).

- [ ] **Step 2: Run the full project test suite**

Run: `python manage.py test`
Expected: Same baseline as step 1 — no new failures introduced outside `apps.campus`/`apps.identity`/`apps.notifications`/`apps.compliance`.

- [ ] **Step 3: Rebase on latest `main` and resolve conflicts if any**

Run: `git fetch origin main`
Run: `git rebase origin/main`
If conflicts appear, follow `AGENTS.md`'s PRESERVATION RULE — never discard upstream logic, blend both sides, re-run the targeted test files touched by the conflict (not the full suite) after each resolution.

- [ ] **Step 4: Open the PR**

Push the branch and open a PR against `main` with a summary covering LIF-001..007, the new `clinic_officer` role, and the test counts from Step 1/2.

- [ ] **Step 5: Update the Notion task**

Once the PR is confirmed mergeable (not yet merged), update the Notion page `3df347a6-6594-81b1-9dbd-d9e52eed7328` (`[Open Item] Campus Life: Clinic / UKS Unit Management (LIF-001..007)`) `Logs` property with the PR link and current test counts. Leave `Status` as `In progress` until the user confirms the PR is merged — per `USER.md` §2.3, `Status` only moves to `Done` at merge, with the PR link and test summary, never before.
