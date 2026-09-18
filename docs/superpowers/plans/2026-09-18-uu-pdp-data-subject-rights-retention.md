# UU PDP Data Subject Rights & Retention Sweeper (CMP-011..013) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `apps.compliance` a data-subject-rights admin tool (DSAR access export + right-to-erasure, CMP-011/012) and an automated gate-photo retention sweeper with dry-run (CMP-013), per `spec/14-compliance-and-integrations.md` §3 and Notion "[Open Item] Compliance: UU PDP Data Subject Rights & Retention Sweeper (CMP-011..013)".

**Architecture — revised after a concurrent merge:** PR #132 (`feat(compliance): implement universal PII export watermarking and access log`, CMP-016) landed on `main` while this plan was being written, adding `apps.core.services.register_export_pii`/`register_pii_export_logger` + `apps.compliance.models.PiiExportAccessLog`, wired generically into the existing `ExportJob` pipeline (`run_export_job`). The DSAR **access** export (CMP-011) now reuses that pipeline directly — a new `dsar_access` report_key rendering an XLSX bundle (Identity+Academic+Attendance+Finance sheets, same shape as the existing DAPODIK/EMIS exporter's per-entity-sheet convention) through the pre-existing `POST /foundation/exports` / `GET /foundation/exports/:job_id` endpoints. This gets CMP-016's watermark + access log for free and needs **no new endpoint, no new encryption, no new model** for the access side. **Erasure** (CMP-012) is unrelated to file export — it keeps its own small `DataSubjectRequest` model (erasure-only now) and endpoint. A `purge_gate_photos` management command (in `apps.attendance`, where `GateEvent` lives) purges `photo_key` past a retention window with `--dry-run`, unchanged from the original design.

**Tech Stack:** Django 5.x, DRF, the existing `core.storage`/GCS-backed `StoredFile` + `ExportJob` + `PiiExportAccessLog` pipeline, `CronHostCommand` + `advisory_lock` + `JobRun` cron pattern.

## Global Constraints

- MySQL 8 only / no new external services (Non-Negotiable Rule #2) — this work adds no infrastructure dependency, and (per the revised design) no new encryption convention either.
- 3-layer tenancy: every new model inherits `TenantModel`; every new view enforces foundation scoping and has a cross-tenant-404 test (Non-Negotiable Rule #1).
- No hard-deletion of academic/financial data (Non-Negotiable Rule #3) — erasure anonymizes `Person` only, never deletes `Student`/`Staff`/`Invoice`/`Payment`/ledger rows.
- Every mutating action writes an audit event via `core.services.audit()` (Non-Negotiable Rule #4) — no new `ComplianceAuditLog` table.
- No PII in logs/exports beyond the DSAR bundle itself, which is PII-bearing by nature and MUST be registered via `register_export_pii` so it gets CMP-016's watermark + access log (Non-Negotiable Rule #8).
- Reuse existing permission keys (`school_config.write`) — no new permission key minted.
- `id-ID` first: user-facing error strings in Indonesian, matching `apps/compliance/services.py`'s existing convention.
- **This branch already has migration `apps/compliance/migrations/0002_piiexportaccesslog.py`** — the new model in Task 1 must be `0003_...`, not `0002_...`.

---

### Task 1: `DataSubjectRequest` model (erasure-only) + migration

**Files:**
- Modify: `apps/compliance/models.py`
- Create: `apps/compliance/migrations/0003_datasubjectrequest.py`
- Test: `apps/compliance/tests/test_dsar.py` (new file, this task adds the first test class)

**Interfaces:**
- Produces: `DataSubjectRequestSubjectType` (`STUDENT`, `STAFF`), `DataSubjectRequestStatus` (`COMPLETED`, `REFUSED`), `DataSubjectRequest` model with fields `subject_type`, `subject_id` (int), `status`, `requested_by` (str), `requested_by_name` (str), `refusal_reason` (str). No `PENDING` status and no `completed_at` field — erasure runs synchronously to a terminal outcome in one call, so there is no intermediate state to track, and `TenantModel.created_at` already timestamps the one moment that matters.
- No `request_type`/`ACCESS` value: access-export requests are tracked entirely through the existing `ExportJob` + `PiiExportAccessLog` pipeline (CMP-016), not through this model — adding an unused `ACCESS` choice here would be exactly the "single-implementation interface" this codebase avoids building speculatively.

- [ ] **Step 1: Write the failing test**

```python
# apps/compliance/tests/test_dsar.py
"""Tests for UU PDP data subject rights & retention tooling (spec/14 §3, CMP-011..013).

CMP-011 (DSAR access export) is covered by apps/compliance/tests/test_statutory_export.py-style
ExportJob renderer tests in Task 2, reusing the CMP-016 PII-export pipeline directly — there is
no separate DataSubjectRequest bookkeeping for access requests. This file covers erasure
(CMP-012) and, via apps/attendance/tests/test_purge_gate_photos.py, retention (CMP-013).
"""
from django.test import TestCase

from apps.compliance.models import (
    DataSubjectRequest,
    DataSubjectRequestStatus,
    DataSubjectRequestSubjectType,
)
from apps.identity.models import Foundation
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id


class DataSubjectRequestModelTests(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Uji", brand_name="Uji", npwp="01.000.000.0-001.000",
        )
        set_current_foundation_id(self.foundation.id)

    def tearDown(self):
        clear_current_foundation_id()

    def test_create_refused_erasure_request(self):
        req = DataSubjectRequest.objects.create(
            foundation_id=self.foundation.id,
            subject_type=DataSubjectRequestSubjectType.STUDENT,
            subject_id=1,
            status=DataSubjectRequestStatus.REFUSED,
            requested_by="42",
            requested_by_name="Ketua Yayasan",
            refusal_reason="Status saat ini bukan status keluar/lulus.",
        )
        self.assertEqual(req.status, DataSubjectRequestStatus.REFUSED)
        self.assertTrue(req.refusal_reason)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `EDUCORE_USE_SQLITE=1 python3 manage.py test apps.compliance.tests.test_dsar.DataSubjectRequestModelTests -v 2`
Expected: FAIL — `ImportError: cannot import name 'DataSubjectRequest'`

- [ ] **Step 3: Write minimal implementation**

Check `apps/compliance/models.py`'s current imports first — it does not import `gettext_lazy` yet
(`StatutoryExportSchema`/`PiiExportAccessLog` use plain English `help_text` strings, but
`_()` IS already imported and used by `PiiExportAccessLog`, added in the CMP-016 merge — reuse it,
do not re-add the import). Append:

```python
class DataSubjectRequestSubjectType(models.TextChoices):
    """Who the request is about (CMP-011/012)."""
    STUDENT = 'STUDENT', _('Siswa')
    STAFF = 'STAFF', _('Staf')


class DataSubjectRequestStatus(models.TextChoices):
    COMPLETED = 'COMPLETED', _('Selesai')
    REFUSED = 'REFUSED', _('Ditolak')


class DataSubjectRequest(TenantModel):
    """Right-to-erasure request audit trail (CMP-012): the "admin tool, not a
    manual SQL task" spec/14 §3 requires. Runs synchronously to a terminal
    outcome (COMPLETED or REFUSED) in one call — there is no PENDING state.

    Access-export requests (CMP-011) are NOT tracked here: they reuse the
    existing ExportJob + PiiExportAccessLog pipeline (CMP-016), which already
    is that domain's "admin tool, not manual SQL" audit trail — see
    apps/compliance/exports.py's `dsar_access` report_key (Task 2).
    """
    subject_type = models.CharField(max_length=16, choices=DataSubjectRequestSubjectType.choices, db_index=True)
    subject_id = models.BigIntegerField(
        help_text="Student.id or Staff.id — a plain integer, not an FK, so this row "
                   "survives the subject's Person row being anonymized."
    )
    status = models.CharField(max_length=16, choices=DataSubjectRequestStatus.choices, db_index=True)
    requested_by = models.CharField(max_length=64, blank=True, default='')
    requested_by_name = models.CharField(max_length=128, blank=True, default='')
    refusal_reason = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        db_table = 'data_subject_erasure_requests'
        indexes = [
            models.Index(fields=['foundation_id', 'subject_type', 'subject_id']),
            models.Index(fields=['foundation_id', 'status']),
        ]

    def __str__(self):
        return f"Erasure {self.subject_type}#{self.subject_id} ({self.status})"
```

Generate the migration:

```bash
python3 manage.py makemigrations compliance
```

Verify the generated file is `apps/compliance/migrations/0003_datasubjectrequest.py` and depends
on `('compliance', '0002_piiexportaccesslog')` — Django resolves this automatically from the
existing migration graph; do not hand-edit the dependency unless `makemigrations` picks a
different name/number, in which case rename the file to match this task's `Files:` list.

- [ ] **Step 4: Run test to verify it passes**

Run: `EDUCORE_USE_SQLITE=1 python3 manage.py test apps.compliance.tests.test_dsar.DataSubjectRequestModelTests -v 2`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/compliance/models.py apps/compliance/migrations/0003_datasubjectrequest.py apps/compliance/tests/test_dsar.py
git commit -m "feat(compliance): add DataSubjectRequest model for erasure requests (CMP-012)"
```

---

### Task 2: DSAR access export — `collect_person_data_bundle` + `dsar_access` ExportJob renderer (CMP-011)

**Files:**
- Modify: `apps/compliance/services.py`
- Modify: `apps/compliance/exports.py`
- Test: `apps/compliance/tests/test_dsar_access_export.py` (new file)

**Interfaces:**
- Consumes: `apps.identity.models.{Student, Staff, Person}`, `apps.academic.models.{ClassEnrollment, ReportCard}`, `apps.attendance.models.{AttendanceDay, PeriodAttendance}`, `apps.finance.models.{Invoice, Payment}`, `apps.core.services.{register_export_renderer, register_export_formats, register_export_permission, register_export_pii}`, `apps.core.models.ExportJob`.
- Produces: `apps.compliance.services.collect_person_data_bundle(subject_type: str, subject_id: int, foundation_id: int) -> dict` and `PersonNotFoundError(ValueError)`; `apps.compliance.exports.REPORT_KEY_DSAR_ACCESS = 'dsar_access'` and its registered renderer.

- [ ] **Step 1: Write the failing test**

Create `apps/compliance/tests/test_dsar_access_export.py`:

```python
"""DSAR access export (CMP-011), reusing the CMP-016 PII-export ExportJob pipeline."""
import datetime
from decimal import Decimal

from django.test import TestCase

from apps.academic.models import AcademicYear, ClassEnrollment, ClassGroup
from apps.attendance.models import AttendanceDay, AttendanceStatus
from apps.compliance.exports import REPORT_KEY_DSAR_ACCESS
from apps.compliance.services import PersonNotFoundError, collect_person_data_bundle
from apps.core.models import ExportJob
from apps.core.services import get_export_allowed_formats, get_export_renderer, is_export_pii
from apps.finance.models import Invoice, InvoiceStatus
from apps.identity.models import Foundation, Person, School, Student
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id


class CollectPersonDataBundleTests(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Uji", brand_name="Uji", npwp="01.000.000.0-002.000",
        )
        set_current_foundation_id(self.foundation.id)
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SMA Uji", npsn="40100099", level=School.LEVEL_SMA,
        )
        self.person = Person.all_tenants.create(
            foundation_id=self.foundation.id, full_name="Siti Aminah",
            nik="3171010101019999", dob=datetime.date(2009, 1, 1),
        )
        self.student = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, person=self.person,
            nis="2026099", nisn="0099999999", status=Student.STATUS_ACTIVE,
        )
        self.academic_year = AcademicYear.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school,
            name="2026/2027", start_date=datetime.date(2026, 7, 1), end_date=datetime.date(2027, 6, 30),
        )
        self.rombel = ClassGroup.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school,
            academic_year=self.academic_year, grade_level=10, name="X IPA 1",
        )
        ClassEnrollment.all_tenants.create(
            foundation_id=self.foundation.id, student=self.student,
            class_group=self.rombel, enrolled_at=datetime.date(2026, 7, 15), is_active=True,
        )
        AttendanceDay.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, student=self.student,
            date=datetime.date(2026, 8, 3), status=AttendanceStatus.HADIR,
        )
        Invoice.objects.create(
            foundation_id=self.foundation.id, school=self.school, student=self.student,
            number="INV/UJI/2026/000099", period="2026-08", due_date=datetime.date(2026, 8, 10),
            subtotal=Decimal('1000000.00'), total=Decimal('1000000.00'), currency='IDR',
            status=InvoiceStatus.ISSUED,
        )

    def tearDown(self):
        clear_current_foundation_id()

    def test_bundle_contains_all_four_domains(self):
        bundle = collect_person_data_bundle('STUDENT', self.student.id, self.foundation.id)
        self.assertEqual(bundle['identity']['full_name'], 'Siti Aminah')
        self.assertEqual(bundle['identity']['nik'], '3171010101019999')
        self.assertEqual(len(bundle['academic_enrollments']), 1)
        self.assertEqual(bundle['academic_enrollments'][0]['class_group'], 'X IPA 1')
        self.assertEqual(len(bundle['attendance_days']), 1)
        self.assertEqual(bundle['attendance_days'][0]['status'], 'HADIR')
        self.assertEqual(len(bundle['invoices']), 1)
        self.assertEqual(bundle['invoices'][0]['number'], 'INV/UJI/2026/000099')

    def test_unknown_student_raises(self):
        with self.assertRaises(PersonNotFoundError):
            collect_person_data_bundle('STUDENT', 999999, self.foundation.id)


class DsarAccessExportRegistrationTests(TestCase):
    """CMP-016 wiring: dsar_access must be PII-registered so watermark +
    PiiExportAccessLog fire automatically via run_export_job (unchanged from
    the DAPODIK/EMIS exporter's own registration)."""

    def test_renderer_registered(self):
        self.assertIsNotNone(get_export_renderer(REPORT_KEY_DSAR_ACCESS))

    def test_registered_as_pii(self):
        self.assertTrue(is_export_pii(REPORT_KEY_DSAR_ACCESS))

    def test_allowed_formats_xlsx_only(self):
        self.assertEqual(get_export_allowed_formats(REPORT_KEY_DSAR_ACCESS), {ExportJob.FORMAT_XLSX})


class DsarAccessExportRendererTests(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Uji Render", brand_name="Uji Render", npwp="01.000.000.0-007.000",
        )
        set_current_foundation_id(self.foundation.id)
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SMA Uji Render", npsn="40100093", level=School.LEVEL_SMA,
        )
        self.person = Person.all_tenants.create(
            foundation_id=self.foundation.id, full_name="Rendi Saputra", nik="3171010101014444",
        )
        self.student = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, person=self.person,
            nis="2026093", status=Student.STATUS_ACTIVE,
        )

    def tearDown(self):
        clear_current_foundation_id()

    def test_renderer_produces_xlsx_bytes(self):
        renderer = get_export_renderer(REPORT_KEY_DSAR_ACCESS)
        job = ExportJob.objects.create(
            foundation_id=self.foundation.id,
            report_key=REPORT_KEY_DSAR_ACCESS,
            format=ExportJob.FORMAT_XLSX,
            filters={'subject_type': 'STUDENT', 'subject_id': self.student.id},
        )
        data, content_type, filename = renderer(job)
        self.assertTrue(len(data) > 0)
        self.assertIn('spreadsheetml', content_type)
        self.assertTrue(filename.endswith('.xlsx'))

    def test_renderer_unknown_subject_raises(self):
        from apps.compliance.services import PersonNotFoundError

        renderer = get_export_renderer(REPORT_KEY_DSAR_ACCESS)
        job = ExportJob.objects.create(
            foundation_id=self.foundation.id,
            report_key=REPORT_KEY_DSAR_ACCESS,
            format=ExportJob.FORMAT_XLSX,
            filters={'subject_type': 'STUDENT', 'subject_id': 999999},
        )
        with self.assertRaises(PersonNotFoundError):
            renderer(job)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `EDUCORE_USE_SQLITE=1 python3 manage.py test apps.compliance.tests.test_dsar_access_export -v 2`
Expected: FAIL — `ImportError: cannot import name 'collect_person_data_bundle'`

- [ ] **Step 3: Write minimal implementation**

Append to `apps/compliance/services.py` (its current imports are
`import re`, `from abc import ABC, abstractmethod`, `from apps.academic.models import ClassEnrollment, ClassGroup`,
`from apps.compliance.models import StatutoryExportSchema, StatutorySystem`,
`from apps.identity.models import Person, School, Staff, Student` — extend rather than duplicate):

```python
class PersonNotFoundError(ValueError):
    """Raised when a DSAR request targets a subject that doesn't exist in this foundation."""


def _serialize_date(value):
    return value.isoformat() if value else None


def collect_person_data_bundle(subject_type: str, subject_id: int, foundation_id: int) -> dict:
    """DSAR access-export bundle (CMP-011): Identity + Academic + Attendance +
    Finance for one Student or Staff. Notifications/wallet/campus-life data
    are a documented follow-up, not pulled in here. Shape is sheet-ready:
    every key except 'identity' is a flat list of row-dicts, consumed
    directly by apps/compliance/exports.py's dsar_access renderer."""
    from apps.attendance.models import AttendanceDay, PeriodAttendance
    from apps.finance.models import Invoice, Payment

    if subject_type == 'STUDENT':
        student = Student.objects.filter(foundation_id=foundation_id, id=subject_id).select_related('person').first()
        if student is None:
            raise PersonNotFoundError(f"Student {subject_id} not found in foundation {foundation_id}")
        person = student.person

        from apps.academic.models import ReportCard

        academic_enrollments = [
            {
                'class_group': e.class_group.name,
                'enrolled_at': _serialize_date(e.enrolled_at),
                'is_active': e.is_active,
            }
            for e in ClassEnrollment.objects.filter(student=student).select_related('class_group')
        ]
        report_cards = [
            {
                'term_id': rc.term_id,
                'status': rc.status,
                'published_at': rc.published_at.isoformat() if rc.published_at else None,
            }
            for rc in ReportCard.objects.filter(student=student, is_current=True)
        ]
        attendance_days = [
            {'date': _serialize_date(a.date), 'status': a.status}
            for a in AttendanceDay.objects.filter(student=student).order_by('date')
        ]
        period_attendances = [
            {'date': _serialize_date(p.date), 'status': p.status, 'source': p.source}
            for p in PeriodAttendance.objects.filter(student=student).order_by('date')
        ]
        invoices = [
            {
                'number': inv.number, 'period': inv.period, 'total': str(inv.total),
                'currency': inv.currency, 'status': inv.status,
            }
            for inv in Invoice.objects.filter(student=student)
        ]
        payments = [
            {
                'reference': p.reference, 'amount': str(p.amount), 'currency': p.currency,
                'status': p.status, 'paid_at': p.paid_at.isoformat() if p.paid_at else None,
            }
            for p in Payment.objects.filter(student=student)
        ]

        return {
            'identity': {
                'subject_type': 'STUDENT', 'full_name': person.full_name, 'nik': person.nik,
                'dob': _serialize_date(person.dob), 'gender': person.gender, 'address': person.address,
                'nis': student.nis, 'nisn': student.nisn, 'status': student.status,
            },
            'academic_enrollments': academic_enrollments,
            'academic_report_cards': report_cards,
            'attendance_days': attendance_days,
            'period_attendances': period_attendances,
            'invoices': invoices,
            'payments': payments,
        }

    if subject_type == 'STAFF':
        staff = Staff.objects.filter(foundation_id=foundation_id, id=subject_id).select_related('person').first()
        if staff is None:
            raise PersonNotFoundError(f"Staff {subject_id} not found in foundation {foundation_id}")
        person = staff.person
        return {
            'identity': {
                'subject_type': 'STAFF', 'full_name': person.full_name, 'nik': person.nik,
                'dob': _serialize_date(person.dob), 'gender': person.gender, 'address': person.address,
                'nip': staff.nip, 'nuptk': staff.nuptk, 'status': staff.status,
            },
            'academic_enrollments': [],
            'academic_report_cards': [],
            'attendance_days': [],
            'period_attendances': [],
            'invoices': [],
            'payments': [],
        }

    raise PersonNotFoundError(f"Unknown subject_type: {subject_type}")
```

Append to `apps/compliance/exports.py` (current imports already include
`from apps.compliance.models import PiiExportAccessLog, StatutorySystem` and the `register_*`
helpers from `apps.core.services` — add `register_export_pii` if not already imported, it is,
per the CMP-016 merge; add `collect_person_data_bundle`/`PersonNotFoundError` from
`apps.compliance.services`):

```python
from apps.compliance.services import PersonNotFoundError, collect_person_data_bundle

REPORT_KEY_DSAR_ACCESS = 'dsar_access'

_DSAR_SHEET_ORDER = [
    'academic_enrollments', 'academic_report_cards',
    'attendance_days', 'period_attendances',
    'invoices', 'payments',
]


def _dsar_bundle_to_xlsx(bundle: dict) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)

    for sheet_name in _DSAR_SHEET_ORDER:
        rows = bundle.get(sheet_name, [])
        ws = wb.create_sheet(title=sheet_name[:31])
        if rows:
            columns = list(rows[0].keys())
            ws.append(columns)
            for row in rows:
                ws.append([row.get(col) for col in columns])
        else:
            ws.append(['(kosong / empty)'])

    identity = bundle.get('identity', {})
    info = wb.create_sheet(title='Info', index=0)
    for key, value in identity.items():
        info.append([key, value])

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def render_dsar_access_export(job: ExportJob):
    """CMP-011 DSAR access export, riding the CMP-016 PII-export pipeline
    (run_export_job watermarks + logs to PiiExportAccessLog automatically
    because dsar_access is registered via register_export_pii below)."""
    filters = job.filters or {}
    subject_type = filters.get('subject_type')
    subject_id = filters.get('subject_id')
    bundle = collect_person_data_bundle(subject_type, subject_id, job.foundation_id)
    data = _dsar_bundle_to_xlsx(bundle)
    filename = f'dsar_{str(subject_type).lower()}_{subject_id}_{job.id}.xlsx'
    return data, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', filename


register_export_renderer(REPORT_KEY_DSAR_ACCESS)(render_dsar_access_export)
register_export_formats(REPORT_KEY_DSAR_ACCESS, {ExportJob.FORMAT_XLSX})
register_export_permission(REPORT_KEY_DSAR_ACCESS, 'school_config.write')
register_export_pii(REPORT_KEY_DSAR_ACCESS)
```

Note `register_export_renderer` is used as a decorator everywhere else in this file
(`@register_export_renderer(REPORT_KEY_DAPODIK)`); the direct-call form above
(`register_export_renderer(REPORT_KEY_DSAR_ACCESS)(render_dsar_access_export)`) is
equivalent and avoids reordering the function definition — either form is fine, but
use the decorator form (`@register_export_renderer(REPORT_KEY_DSAR_ACCESS)` directly
above `def render_dsar_access_export(job):`) to match the file's existing style exactly.

- [ ] **Step 4: Run test to verify it passes**

Run: `EDUCORE_USE_SQLITE=1 python3 manage.py test apps.compliance.tests.test_dsar_access_export -v 2`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/compliance/services.py apps/compliance/exports.py apps/compliance/tests/test_dsar_access_export.py
git commit -m "feat(compliance): DSAR access export via the CMP-016 PII-export pipeline (CMP-011)"
```

---

### Task 3: `erase_person` — right to erasure (CMP-012)

**Files:**
- Modify: `apps/compliance/services.py`
- Test: `apps/compliance/tests/test_dsar.py` (append)

**Interfaces:**
- Consumes: `apps.identity.models.{Student, Staff, Person}`, `apps.core.services.audit`, `DataSubjectRequest`/`DataSubjectRequestSubjectType`/`DataSubjectRequestStatus` (Task 1).
- Produces: `apps.compliance.services.erase_person(subject_type, subject_id, foundation_id, requested_by, requested_by_name) -> DataSubjectRequest`; `PersonNotErasableError(ValueError)`.

- [ ] **Step 1: Write the failing test**

Append to `apps/compliance/tests/test_dsar.py`:

```python
import datetime
from decimal import Decimal

from apps.compliance.services import PersonNotErasableError, erase_person
from apps.core.models import AuditEvent
from apps.finance.models import Invoice, InvoiceStatus, Payment, PaymentMethod, PaymentStatus
from apps.identity.models import Person, School, Student


class ErasePersonTests(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Uji", brand_name="Uji", npwp="01.000.000.0-004.000",
        )
        set_current_foundation_id(self.foundation.id)
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SMA Uji Hapus", npsn="40100097", level=School.LEVEL_SMA,
        )
        self.person = Person.all_tenants.create(
            foundation_id=self.foundation.id, full_name="Andi Lulus",
            nik="3171010101017777", address="Jl. Contoh No. 1",
        )
        self.student = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, person=self.person,
            nis="2026097", status=Student.STATUS_GRADUATED,
        )
        self.invoice = Invoice.objects.create(
            foundation_id=self.foundation.id, school=self.school, student=self.student,
            number="INV/UJI/2026/000097", period="2026-06", due_date=datetime.date(2026, 6, 10),
            subtotal=Decimal('500000.00'), total=Decimal('500000.00'), paid=Decimal('500000.00'),
            currency='IDR', status=InvoiceStatus.PAID,
        )
        self.payment = Payment.objects.create(
            foundation_id=self.foundation.id, school=self.school, student=self.student,
            invoice=self.invoice, amount=Decimal('500000.00'), currency='IDR',
            method=PaymentMethod.VA, channel='BCA_VA', reference="PAY/UJI/2026/000097",
            status=PaymentStatus.SETTLED,
        )

    def tearDown(self):
        clear_current_foundation_id()

    def test_erasure_refused_when_active(self):
        active_person = Person.all_tenants.create(
            foundation_id=self.foundation.id, full_name="Masih Aktif", nik="3171010101016666",
        )
        active_student = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, person=active_person,
            nis="2026096", status=Student.STATUS_ACTIVE,
        )
        with self.assertRaises(PersonNotErasableError):
            erase_person(
                subject_type='STUDENT', subject_id=active_student.id, foundation_id=self.foundation.id,
                requested_by='7', requested_by_name='Ketua Yayasan',
            )
        active_person.refresh_from_db()
        self.assertEqual(active_person.full_name, 'Masih Aktif')

    def test_erasure_anonymizes_person_preserves_ledger(self):
        dsar = erase_person(
            subject_type='STUDENT', subject_id=self.student.id, foundation_id=self.foundation.id,
            requested_by='7', requested_by_name='Ketua Yayasan',
        )
        self.assertEqual(dsar.status, DataSubjectRequestStatus.COMPLETED)

        self.person.refresh_from_db()
        self.assertEqual(self.person.full_name, f"[ERASED-{self.person.id}]")
        self.assertIsNone(self.person.nik)
        self.assertEqual(self.person.address, '')

        # Financial rows untouched — 10-year retention (CMP-012) falls out
        # of the PII-vault design automatically.
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.total, Decimal('500000.00'))
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.amount, Decimal('500000.00'))

    def test_erasure_writes_audit_event(self):
        erase_person(
            subject_type='STUDENT', subject_id=self.student.id, foundation_id=self.foundation.id,
            requested_by='7', requested_by_name='Ketua Yayasan',
        )
        self.assertTrue(
            AuditEvent.objects.filter(action='compliance.person.erase', entity_id=str(self.person.id)).exists()
        )

    def test_erasure_unknown_subject_raises(self):
        with self.assertRaises(PersonNotErasableError):
            erase_person(
                subject_type='STUDENT', subject_id=999999, foundation_id=self.foundation.id,
                requested_by='7', requested_by_name='Ketua Yayasan',
            )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `EDUCORE_USE_SQLITE=1 python3 manage.py test apps.compliance.tests.test_dsar.ErasePersonTests -v 2`
Expected: FAIL — `ImportError: cannot import name 'erase_person'`

- [ ] **Step 3: Write minimal implementation**

Append to `apps/compliance/services.py`:

```python
from apps.compliance.models import DataSubjectRequest, DataSubjectRequestStatus, DataSubjectRequestSubjectType
from apps.core.services import audit


class PersonNotErasableError(ValueError):
    """Raised when erasure is requested for a subject that is not yet in a
    departed/terminal status (CMP-012: "processing erasure requests for
    departed students/staff"), or that doesn't exist."""


_STUDENT_ERASABLE_STATUSES = {Student.STATUS_GRADUATED, Student.STATUS_TRANSFERRED_OUT}
_STAFF_ERASABLE_STATUSES = {Staff.STATUS_OFFBOARDED}


def _anonymize_person(person) -> None:
    person.full_name = f"[ERASED-{person.id}]"
    person.nik = None
    person.dob = None
    person.address = ''
    person.birth_city = ''
    person.birth_certificate_number = ''
    person.religion = ''
    person.rt = ''
    person.rw = ''
    person.dusun = ''
    person.kelurahan = ''
    person.kecamatan = ''
    person.kabupaten_kota = ''
    person.provinsi = ''
    person.postal_code = ''
    person.save()


def erase_person(subject_type, subject_id, foundation_id, requested_by, requested_by_name) -> DataSubjectRequest:
    """CMP-012 right to erasure. Refuses on anyone not already departed, or
    unknown. Anonymizes the Person PII-vault row only — Student/Staff/
    academic/financial rows keep their FK to the now-anonymized Person, so
    retention obligations (10yr financial, permanent-default academic) are
    preserved with zero special-casing."""
    if subject_type == DataSubjectRequestSubjectType.STUDENT:
        subject = Student.objects.filter(foundation_id=foundation_id, id=subject_id).select_related('person').first()
        erasable_statuses = _STUDENT_ERASABLE_STATUSES
    elif subject_type == DataSubjectRequestSubjectType.STAFF:
        subject = Staff.objects.filter(foundation_id=foundation_id, id=subject_id).select_related('person').first()
        erasable_statuses = _STAFF_ERASABLE_STATUSES
    else:
        subject = None
        erasable_statuses = set()

    if subject is None:
        DataSubjectRequest.objects.create(
            foundation_id=foundation_id, subject_type=subject_type, subject_id=subject_id,
            status=DataSubjectRequestStatus.REFUSED, requested_by=requested_by,
            requested_by_name=requested_by_name,
            refusal_reason=f"{subject_type} {subject_id} tidak ditemukan di yayasan {foundation_id}.",
        )
        raise PersonNotErasableError(f"{subject_type} {subject_id} not found in foundation {foundation_id}")

    if subject.status not in erasable_statuses:
        refusal_reason = (
            f"Tidak dapat menghapus data: status saat ini '{subject.status}' bukan status "
            f"keluar/lulus (CMP-012 hanya mengizinkan penghapusan untuk yang sudah keluar)."
        )
        DataSubjectRequest.objects.create(
            foundation_id=foundation_id, subject_type=subject_type, subject_id=subject_id,
            status=DataSubjectRequestStatus.REFUSED, requested_by=requested_by,
            requested_by_name=requested_by_name, refusal_reason=refusal_reason,
        )
        raise PersonNotErasableError(refusal_reason)

    person = subject.person
    person_id = person.id
    _anonymize_person(person)

    audit(
        action='compliance.person.erase',
        entity_type='Person',
        entity_id=str(person_id),
        actor_id=requested_by,
        foundation_id=foundation_id,
        diff={'erased': True, 'subject_type': subject_type, 'subject_id': subject_id},
    )

    return DataSubjectRequest.objects.create(
        foundation_id=foundation_id, subject_type=subject_type, subject_id=subject_id,
        status=DataSubjectRequestStatus.COMPLETED, requested_by=requested_by,
        requested_by_name=requested_by_name,
    )
```

Note this changes `erase_person`'s contract from the original spec draft: it now **raises**
`PersonNotErasableError` on refusal (rather than returning a REFUSED row) in every case,
recording the REFUSED `DataSubjectRequest` row first so the audit trail exists either way.
The REST view in Task 4 catches this exception and reports it as a 200 with the refusal reason
(a client error the caller should see structured, not a 500).

- [ ] **Step 4: Run test to verify it passes**

Run: `EDUCORE_USE_SQLITE=1 python3 manage.py test apps.compliance.tests.test_dsar.ErasePersonTests -v 2`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/compliance/services.py apps/compliance/tests/test_dsar.py
git commit -m "feat(compliance): right to erasure for departed students/staff (CMP-012)"
```

---

### Task 4: REST API — `POST`/`GET /foundation/compliance/erasure-requests`

**Files:**
- Modify: `apps/compliance/views.py`
- Modify: `apps/foundation/urls.py`
- Test: `apps/compliance/tests/test_dsar.py` (append)

**Interfaces:**
- Consumes: `erase_person`, `PersonNotErasableError` (Task 3), `DataSubjectRequest` (Task 1), `HasRequiredPermission`, `get_current_foundation_id`. DSAR **access** requests are NOT handled here — they go through the pre-existing `POST /foundation/exports` with `{"report": "dsar_access", "format": "XLSX", "filters": {"subject_type": "STUDENT", "subject_id": <id>}}`, already wired end-to-end by Task 2.
- Produces: `ErasureRequestView` (POST create + GET list) in `apps/compliance/views.py`; URL `foundation/compliance/erasure-requests`.

- [ ] **Step 1: Write the failing test**

Append to `apps/compliance/tests/test_dsar.py`:

```python
from rest_framework import status as http_status
from rest_framework.test import APITestCase

from apps.identity.models import User
from apps.identity.rbac import ROLE_FOUNDATION_ADMIN, ROLE_TEACHER, SCOPE_FOUNDATION, assign_role
from educore.middleware.tenancy import tenant_context


class ErasureRequestEndpointTests(APITestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Uji", brand_name="Uji", npwp="01.000.000.0-005.000",
        )
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SMA Uji Endpoint", npsn="40100096", level=School.LEVEL_SMA,
        )
        self.person = Person.all_tenants.create(
            foundation_id=self.foundation.id, full_name="Citra Dewi", nik="3171010101015555",
        )
        self.student = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, person=self.person,
            nis="2026095", status=Student.STATUS_GRADUATED,
        )
        self.admin = User.all_tenants.create_user(
            phone_e164="+6281999999991", foundation_id=self.foundation.id, full_name="Ketua Yayasan",
        )
        assign_role(
            user=self.admin, role=ROLE_FOUNDATION_ADMIN, scope_type=SCOPE_FOUNDATION,
            scope_id=self.foundation.id, foundation_id=self.foundation.id,
        )
        self.teacher = User.all_tenants.create_user(
            phone_e164="+6281999999992", foundation_id=self.foundation.id, full_name="Guru",
        )
        assign_role(
            user=self.teacher, role=ROLE_TEACHER, scope_type=SCOPE_FOUNDATION,
            scope_id=self.foundation.id, foundation_id=self.foundation.id,
        )

    def tearDown(self):
        clear_current_foundation_id()

    def test_admin_erases_graduated_student(self):
        self.client.force_authenticate(user=self.admin)
        with tenant_context(self.foundation.id):
            response = self.client.post('/api/v1/foundation/compliance/erasure-requests', {
                'subject_type': 'STUDENT', 'subject_id': self.student.id,
            }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_201_CREATED)
        self.assertEqual(response.json()['status'], 'COMPLETED')

    def test_teacher_forbidden(self):
        self.client.force_authenticate(user=self.teacher)
        with tenant_context(self.foundation.id):
            response = self.client.post('/api/v1/foundation/compliance/erasure-requests', {
                'subject_type': 'STUDENT', 'subject_id': self.student.id,
            }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_403_FORBIDDEN)

    def test_refused_when_active_returns_200_with_reason(self):
        active_person = Person.all_tenants.create(
            foundation_id=self.foundation.id, full_name="Masih Aktif", nik="3171010101014444",
        )
        active_student = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, person=active_person,
            nis="2026094", status=Student.STATUS_ACTIVE,
        )
        self.client.force_authenticate(user=self.admin)
        with tenant_context(self.foundation.id):
            response = self.client.post('/api/v1/foundation/compliance/erasure-requests', {
                'subject_type': 'STUDENT', 'subject_id': active_student.id,
            }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.assertEqual(response.json()['status'], 'REFUSED')
        self.assertTrue(response.json()['refusal_reason'])

    def test_cross_tenant_list_is_scoped(self):
        other_foundation = Foundation.objects.create(
            legal_name="Yayasan Lain", brand_name="Lain", npwp="02.000.000.0-005.000",
        )
        with tenant_context(other_foundation.id):
            other_admin = User.all_tenants.create_user(
                phone_e164="+6281999999993", foundation_id=other_foundation.id, full_name="Admin Lain",
            )
            assign_role(
                user=other_admin, role=ROLE_FOUNDATION_ADMIN, scope_type=SCOPE_FOUNDATION,
                scope_id=other_foundation.id, foundation_id=other_foundation.id,
            )

        self.client.force_authenticate(user=self.admin)
        with tenant_context(self.foundation.id):
            self.client.post('/api/v1/foundation/compliance/erasure-requests', {
                'subject_type': 'STUDENT', 'subject_id': self.student.id,
            }, format='json')

        self.client.force_authenticate(user=other_admin)
        with tenant_context(other_foundation.id):
            response = self.client.get('/api/v1/foundation/compliance/erasure-requests')
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.assertEqual(response.json(), [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `EDUCORE_USE_SQLITE=1 python3 manage.py test apps.compliance.tests.test_dsar.ErasureRequestEndpointTests -v 2`
Expected: FAIL — 404 (no such URL) on the first POST

- [ ] **Step 3: Write minimal implementation**

Append to `apps/compliance/views.py` (its current imports already include
`from apps.compliance.models import PiiExportAccessLog, StatutorySystem`,
`from apps.identity.permissions import HasRequiredPermission`,
`from educore.middleware.tenancy import get_current_foundation_id` — extend the models import,
do not duplicate the others):

```python
from apps.compliance.models import DataSubjectRequest
from apps.compliance.services import PersonNotErasableError, erase_person


class ErasureRequestView(views.APIView):
    """POST /foundation/compliance/erasure-requests — run a CMP-012 right-to-
    erasure request synchronously. GET — list past requests for this
    foundation."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'school_config.write'

    def post(self, request):
        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        if not foundation_id:
            return Response({"detail": "Konteks Yayasan tidak ditemukan."}, status=status.HTTP_400_BAD_REQUEST)

        subject_type = request.data.get('subject_type')
        subject_id = request.data.get('subject_id')
        if subject_type not in ('STUDENT', 'STAFF') or not subject_id:
            return Response(
                {"detail": "subject_type (STUDENT/STAFF) dan subject_id wajib diisi."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        requested_by = str(request.user.id)
        requested_by_name = getattr(request.user, 'full_name', '') or ''

        try:
            dsar = erase_person(
                subject_type=subject_type, subject_id=subject_id, foundation_id=foundation_id,
                requested_by=requested_by, requested_by_name=requested_by_name,
            )
            return Response(_serialize_erasure_request(dsar), status=status.HTTP_201_CREATED)
        except PersonNotErasableError:
            dsar = (
                DataSubjectRequest.objects.filter(
                    foundation_id=foundation_id, subject_type=subject_type, subject_id=subject_id,
                )
                .order_by('-id')
                .first()
            )
            return Response(_serialize_erasure_request(dsar), status=status.HTTP_200_OK)

    def get(self, request):
        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        if not foundation_id:
            return Response({"detail": "Konteks Yayasan tidak ditemukan."}, status=status.HTTP_400_BAD_REQUEST)
        requests_qs = DataSubjectRequest.objects.filter(foundation_id=foundation_id).order_by('-id')[:100]
        return Response([_serialize_erasure_request(r) for r in requests_qs])


def _serialize_erasure_request(dsar: DataSubjectRequest) -> dict:
    return {
        'id': dsar.id,
        'subject_type': dsar.subject_type,
        'subject_id': dsar.subject_id,
        'status': dsar.status,
        'requested_by_name': dsar.requested_by_name,
        'refusal_reason': dsar.refusal_reason,
    }
```

Wire the URL in `apps/foundation/urls.py`:

```python
from apps.compliance.views import ErasureRequestView, StatutoryValidationView
```

```python
    path('foundation/compliance/erasure-requests', ErasureRequestView.as_view(), name='foundation-compliance-erasure-requests'),
```

(add it into the existing `urlpatterns` list alongside `foundation/statutory-validation`; keep the
existing `from apps.compliance.views import StatutoryValidationView` import merged into one line
with the new name, do not add a second import line for the same module.)

- [ ] **Step 4: Run test to verify it passes**

Run: `EDUCORE_USE_SQLITE=1 python3 manage.py test apps.compliance.tests.test_dsar.ErasureRequestEndpointTests -v 2`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/compliance/views.py apps/foundation/urls.py apps/compliance/tests/test_dsar.py
git commit -m "feat(compliance): erasure-request REST endpoint (CMP-012)"
```

---

### Task 5: `purge_gate_photos` management command (CMP-013)

**Files:**
- Create: `apps/attendance/management/commands/purge_gate_photos.py`
- Modify: `deploy/crontab`
- Test: `apps/attendance/tests/test_purge_gate_photos.py` (new file)

**Interfaces:**
- Consumes: `apps.attendance.models.GateEvent`, `apps.core.management.base.CronHostCommand`, `apps.core.locks.advisory_lock`, `apps.core.models.JobRun`.
- Produces: `python manage.py purge_gate_photos [--retention-days N] [--dry-run]`.

- [ ] **Step 1: Write the failing test**

Create `apps/attendance/tests/test_purge_gate_photos.py`:

```python
"""Tests for the gate-photo retention sweeper (spec/14 §3/§7, CMP-013)."""
import datetime
import uuid

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.attendance.models import GateDirection, GateEvent, GateEventStatus, GateMethod
from apps.core.models import JobRun
from apps.hardware.models import Device, DeviceClass, DeviceDirection
from apps.identity.models import Foundation, Person, School, Student
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id


class PurgeGatePhotosTests(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Uji Retensi", brand_name="Uji Retensi", npwp="01.000.000.0-006.000",
        )
        set_current_foundation_id(self.foundation.id)
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SMA Uji Retensi", npsn="40100095", level=School.LEVEL_SMA,
        )
        self.device = Device.objects.create(
            foundation_id=self.foundation.id, school=self.school, device_code="GATE-RET-001",
            name="Gerbang Retensi", device_class=DeviceClass.GATE_READER, direction=DeviceDirection.IN,
            ip_address="192.168.1.60", mac_address="00:11:22:33:44:66",
        )
        self.person = Person.all_tenants.create(foundation_id=self.foundation.id, full_name="Siswa Retensi")
        self.student = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, person=self.person,
            nis="2026094", status=Student.STATUS_ACTIVE,
        )

        now = timezone.now()
        self.old_event = GateEvent.objects.create(
            foundation_id=self.foundation.id, school=self.school, device=self.device, student=self.student,
            event_uuid=uuid.uuid4(), direction=GateDirection.IN, occurred_at=now - datetime.timedelta(days=91),
            method=GateMethod.FACE, status=GateEventStatus.ACCEPTED, photo_key='gate/old_photo.jpg',
        )
        self.recent_event = GateEvent.objects.create(
            foundation_id=self.foundation.id, school=self.school, device=self.device, student=self.student,
            event_uuid=uuid.uuid4(), direction=GateDirection.IN, occurred_at=now - datetime.timedelta(days=5),
            method=GateMethod.FACE, status=GateEventStatus.ACCEPTED, photo_key='gate/recent_photo.jpg',
        )
        self.no_photo_event = GateEvent.objects.create(
            foundation_id=self.foundation.id, school=self.school, device=self.device, student=self.student,
            event_uuid=uuid.uuid4(), direction=GateDirection.IN, occurred_at=now - datetime.timedelta(days=200),
            method=GateMethod.RFID, status=GateEventStatus.ACCEPTED, photo_key='',
        )

    def tearDown(self):
        clear_current_foundation_id()

    def test_dry_run_reports_without_deleting(self):
        call_command('purge_gate_photos', '--dry-run', '--force')

        self.old_event.refresh_from_db()
        self.assertEqual(self.old_event.photo_key, 'gate/old_photo.jpg')

    def test_real_run_purges_only_photos_past_retention(self):
        call_command('purge_gate_photos', '--force')

        self.old_event.refresh_from_db()
        self.assertEqual(self.old_event.photo_key, '')

        self.recent_event.refresh_from_db()
        self.assertEqual(self.recent_event.photo_key, 'gate/recent_photo.jpg')

        job_run = JobRun.objects.filter(job_name='purge_gate_photos').latest('started_at')
        self.assertEqual(job_run.status, JobRun.STATUS_SUCCESS)
        self.assertEqual(job_run.items_processed, 1)

    def test_custom_retention_days(self):
        call_command('purge_gate_photos', '--retention-days=3', '--force')

        self.recent_event.refresh_from_db()
        self.assertEqual(self.recent_event.photo_key, '')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `EDUCORE_USE_SQLITE=1 python3 manage.py test apps.attendance.tests.test_purge_gate_photos -v 2`
Expected: FAIL — `Unknown command: 'purge_gate_photos'`

- [ ] **Step 3: Write minimal implementation**

Create `apps/attendance/management/commands/purge_gate_photos.py`:

```python
"""Gate-photo retention sweeper (spec/14 §3/§7, CMP-013).

Purges GateEvent.photo_key for events older than the retention window
(default 90 days, spec/14's CMP-012 "gate photos retained 90 days by
default"). Biometric-template purge is deliberately NOT included: no
face-template model exists anywhere in this repo yet (tracked as its own
Notion Open Item) — this command only touches the one real target,
GateEvent.photo_key.
"""
from datetime import timedelta

from django.utils import timezone

from apps.attendance.models import GateEvent
from apps.core.locks import advisory_lock
from apps.core.management.base import CronHostCommand
from apps.core.models import JobRun

GATE_PHOTO_RETENTION_DAYS = 90  # spec/14 §7 criterion 4


class Command(CronHostCommand):
    help = "Purge GateEvent.photo_key older than the retention window (default 90 days, CMP-013)."

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            '--retention-days', type=int, default=GATE_PHOTO_RETENTION_DAYS,
            help=f"Days to retain gate photos (default {GATE_PHOTO_RETENTION_DAYS}).",
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help="List which rows would be purged without deleting anything.",
        )

    def handle(self, *args, **options):
        retention_days = options['retention_days']
        dry_run = options['dry_run']
        lock_name = 'educore:purge_gate_photos'

        with advisory_lock(lock_name, timeout=0) as acquired:
            if not acquired:
                self.stdout.write(self.style.WARNING(
                    f"Advisory lock for '{lock_name}' already held. Exiting cleanly."))
                return

            job_run = JobRun.objects.create(job_name='purge_gate_photos')
            try:
                cutoff = timezone.now() - timedelta(days=retention_days)
                candidates = GateEvent.objects.exclude(photo_key='').filter(occurred_at__lt=cutoff)
                count = candidates.count()

                if dry_run:
                    for event in candidates[:50]:
                        self.stdout.write(f"Would purge photo_key for GateEvent#{event.id} ({event.occurred_at})")
                    self.stdout.write(self.style.SUCCESS(
                        f"purge_gate_photos --dry-run: {count} gate photo(s) older than {retention_days}d would be purged."))
                    job_run.status = JobRun.STATUS_SUCCESS
                    job_run.items_processed = 0
                    job_run.finished_at = timezone.now()
                    job_run.save()
                    return

                updated = candidates.update(photo_key='')

                job_run.status = JobRun.STATUS_SUCCESS
                job_run.items_processed = updated
                job_run.finished_at = timezone.now()
                job_run.save()
                self.stdout.write(self.style.SUCCESS(
                    f"purge_gate_photos: purged {updated} gate photo(s) older than {retention_days}d."))
            except Exception as exc:
                job_run.status = JobRun.STATUS_FAILED
                job_run.error_text = str(exc)[:2000]
                job_run.finished_at = timezone.now()
                job_run.save()
                raise
```

`deploy/crontab` uses one-line-per-job, `<schedule> python /app/manage.py <command> [args] # comment`.
Add, directly after the existing `0 22 * * * ... enforce_retention ...` line (both are daily
retention jobs at the same hour, keeping the file's jobs grouped by purpose):

```
0 22 * * *     python /app/manage.py purge_gate_photos --retention-days=90     # CMP-013: gate photo retention (spec/14 §3/§7)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `EDUCORE_USE_SQLITE=1 python3 manage.py test apps.attendance.tests.test_purge_gate_photos -v 2`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/attendance/management/commands/purge_gate_photos.py deploy/crontab apps/attendance/tests/test_purge_gate_photos.py
git commit -m "feat(attendance): purge_gate_photos retention sweeper with dry-run (CMP-013)"
```

---

### Task 6: Full-suite regression check

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `EDUCORE_USE_SQLITE=1 python3 manage.py test`
Expected: All tests pass except the pre-existing unrelated finance failures already documented
on `main` (verify with `git log`/`git stash` if any new failure looks suspicious — this branch
is ahead of the plan-writing session's local `main`, so re-confirm which failures are pre-existing
on today's `main` HEAD, not the older count cited in past memory entries).

- [ ] **Step 2: Run Django system checks**

Run: `python3 manage.py check`
Expected: No new warnings (in particular no new `W036` unique-constraint-with-condition warnings
from `DataSubjectRequest` — it has none).

- [ ] **Step 3: Commit any fixups**

If Step 1 or 2 surfaced anything, fix and commit:

```bash
git add -A
git commit -m "fix(compliance): address regression from full-suite run"
```

---

## Self-Review Notes (for the plan author, already applied above)

- **Spec coverage:** CMP-011 (access export, Task 2, reusing CMP-016's pipeline) ✓, CMP-012 (erasure + retention reconciliation, Task 3/4) ✓, CMP-013 (automated retention job with dry-run, Task 5) ✓. Rectification/restriction/portability remain documented non-goals per the approved spec (`docs/superpowers/specs/2026-09-18-uu-pdp-data-subject-rights-retention-design.md`), not silently dropped.
- **No placeholders:** every step has real code, grounded in what actually exists on this branch (verified `apps/compliance/migrations/0002_piiexportaccesslog.py`, `apps/compliance/models.py`'s current classes, `apps/compliance/exports.py`'s and `apps/compliance/views.py`'s current imports, and `deploy/crontab`'s real format, all read directly rather than assumed).
- **Type consistency:** `DataSubjectRequest.status`/`subject_type` string values match across Tasks 1, 3, 4 (`'STUDENT'`/`'STAFF'`, `'COMPLETED'`/`'REFUSED'`). `collect_person_data_bundle`'s return shape (Task 2) matches exactly what `_dsar_bundle_to_xlsx` consumes (same key names: `academic_enrollments`, `academic_report_cards`, `attendance_days`, `period_attendances`, `invoices`, `payments`, `identity`).
- **Superseded design note:** the original plan's Task 2 (`apps/compliance/crypto.py`, Fernet encryption) and original Task 4's `write_generated_file`/`download_bytes`/`DataSubjectRequest.result_key` machinery are dropped entirely — superseded by reusing the CMP-016 `ExportJob`/`PiiExportAccessLog` pipeline per the user's explicit decision after the mid-plan conflict was surfaced. `apps/core/storage.py` is untouched by this revised plan.
