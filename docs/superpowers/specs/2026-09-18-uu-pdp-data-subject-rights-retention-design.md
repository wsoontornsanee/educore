# UU PDP Data Subject Rights & Retention Sweeper (CMP-011..013)

Spec: `spec/14-compliance-and-integrations.md` §3 (`CMP-011`..`CMP-013`), §7 acceptance criteria 2-4.
Notion: [Open Item] Compliance: UU PDP Data Subject Rights & Retention Sweeper (CMP-011..013).

## Context

`apps.compliance` already exists (DAPODIK/EMIS statutory export tooling, CMP-017..021,
PR #103/#105). This adds the UU PDP data-subject-rights toolset to the same app.

Grep-verified before writing this spec (no fabricated citations): no biometric/face-template
model exists anywhere in this repo (face recognition is an unbuilt P2 milestone); the only
real retention target for CMP-013 is `apps.attendance.GateEvent.photo_key`. `enforce_retention`
(existing cron) purges `IdempotencyRecord` only — unrelated, not extended by this work.

## Scope decisions (confirmed with user)

- All three sub-requirements (CMP-011 access/erasure admin tool, CMP-012 erasure/retention
  reconciliation, CMP-013 automated retention job) ship in one PR — they share the
  person-scoped data-collection logic.
- Erasure is refused unless the subject (Student/Staff) is already in a departed/inactive
  terminal status — matches spec's literal "departed students/staff" wording.
- DSAR access bundle is a Fernet-encrypted JSON file (new `apps/compliance/crypto.py`,
  same pattern as `apps/partners/crypto.py`), not a password-protected zip — no zip-encryption
  library exists in this repo and this avoids adding one.
- v1 bundle covers Identity + Academic + Attendance + Finance (the four domains with real
  person-linked data). Notifications/wallet/campus-life data are logged as a follow-up
  Open Item rather than pulled into this PR.
- DSAR/erasure endpoints reuse the existing `school_config.write` permission key rather than
  minting a new `compliance.*` key.
- Biometric template deletion (CMP-010/CMP-012's biometric clause) is not built: no target
  model exists. Logged as its own Notion Open Item (biometric enrollment / face-template
  model, P2 face-recognition milestone) rather than faked here.

## 1. Models (`apps/compliance/models.py`)

```python
class DataSubjectRequestType(TextChoices):
    ACCESS = 'ACCESS'
    ERASURE = 'ERASURE'

class DataSubjectRequestStatus(TextChoices):
    PENDING = 'PENDING'
    COMPLETED = 'COMPLETED'
    REFUSED = 'REFUSED'

class DataSubjectRequestSubjectType(TextChoices):
    STUDENT = 'STUDENT'
    STAFF = 'STAFF'

class DataSubjectRequest(TenantModel):
    subject_type: CharField(choices=DataSubjectRequestSubjectType)
    subject_id: BigIntegerField()          # Student.id or Staff.id, not an FK — the row
                                            # must survive the subject being anonymized/erased
    request_type: CharField(choices=DataSubjectRequestType)
    status: CharField(choices=DataSubjectRequestStatus, default=PENDING)
    requested_by: CharField(max_length=64)
    requested_by_name: CharField(max_length=128, blank=True, default='')
    completed_at: DateTimeField(null=True, blank=True)
    result_key: CharField(max_length=500, blank=True, default='')   # encrypted bundle path, ACCESS only
    refusal_reason: CharField(max_length=255, blank=True, default='')
```

One row per request gives CMP-011's "admin tool, not a manual SQL task" an audit trail with
zero extra plumbing — no separate `ComplianceAuditLog` (rejected the Notion body's invented
name; every mutating action already writes through `core.AuditEvent.audit()` per
Non-Negotiable Rule #4, reused here unchanged).

## 2. DSAR access export (CMP-011)

`apps/compliance/services.py`:

```python
def collect_person_data_bundle(subject_type: str, subject_id: int, foundation_id: int) -> dict:
    """Person + Student/Staff + academic + attendance + finance records for one subject."""
```

- Resolves the `Person` via `Student`/`Staff`.
- Academic: `ClassEnrollment` rows, published `ReportCard`s, `HomeworkSubmission`s (student only).
- Attendance: `AttendanceDay`, `PeriodAttendance`, `GateEvent` (photo_key referenced by key,
  never raw image bytes fetched into the bundle).
- Finance (student only): `Invoice`, `Payment`, ledger entries touching that student.
- `_meta`: `requested_by`, `requested_by_name`, `generated_at` — the CMP-011 "watermark",
  done as bundle metadata (there is no PDF layer in v1 to stamp visually).

`apps/compliance/crypto.py` (new, mirrors `apps/partners/crypto.py` exactly): Fernet
encrypt/decrypt keyed by `EDUCORE_COMPLIANCE_FERNET_KEY` (dev fallback derived from
`SECRET_KEY`, same as partners).

Bundle is serialized to JSON, encrypted, and written via `apps.core.services.write_generated_file`
(purpose `dsar_bundle`) — correcting an earlier assumption in this spec: `core.storage`'s
GCS-backed `write_generated_file`/`StoredFile` pipeline is already the live convention for
every other generated document (report cards, settlement statements), not a raw `MEDIA_ROOT`
write. `DataSubjectRequest.result_key` stores the returned `StoredFile.key`.

## 3. Right to erasure (CMP-012)

```python
def erase_person(subject_type: str, subject_id: int, foundation_id: int, requested_by: str) -> DataSubjectRequest:
```

- Loads the `Student`/`Staff`. Raises `PersonNotErasableError` unless status is a terminal
  departed state: `Student.STATUS_GRADUATED`/`STATUS_TRANSFERRED_OUT` (both terminal per
  `Student.VALID_TRANSITIONS`), or `Staff.STATUS_OFFBOARDED` — confirmed against
  `apps/identity/models.py`, no other constants needed.
- Anonymizes the `Person` row in place: `full_name` → `f"[ERASED-{person.id}]"`; blanks `nik`,
  `dob`, `address`, `birth_city`, `birth_certificate_number`, `religion`, and every structured
  address field (`rt`/`rw`/`dusun`/`kelurahan`/`kecamatan`/`kabupaten_kota`/`provinsi`/`postal_code`).
- Never touches `Student`/`Staff`/`Invoice`/`Payment`/ledger/academic rows — they keep their FK
  to the now-anonymized `Person`, so 10-year financial retention and default-permanent academic
  retention are preserved automatically. No special-casing needed (CMP-012's reconciliation
  requirement falls out of the existing "PII lives only in `Person`" vault design, memory #7).
- Writes one `AuditEvent` (`action='compliance.person.erase'`) with a redacted diff.
- Creates/completes the `DataSubjectRequest` row (`ERASURE`, `COMPLETED` or `REFUSED` +
  `refusal_reason`).
- Biometric template deletion: no-op — no target model. Not faked; tracked as its own gap.

## 4. REST API

`POST /foundation/compliance/dsar/` — body `{subject_type, subject_id, request_type}`.
Runs the request synchronously (bundles are small; matches spec/14 §7 criterion 3's "within
one admin session" literally — no async `ExportJob` needed here) and returns the created
`DataSubjectRequest` (for ACCESS: a `result_key` the caller uses with a paired decrypt endpoint;
for ERASURE: `COMPLETED`/`REFUSED` + reason).

`GET /foundation/compliance/dsar/` — list past requests (cross-tenant 404 test as usual).

Permission: `school_config.write` on both (reused, no new permission key minted).

## 5. Retention sweeper (CMP-013)

New management command `apps/attendance/management/commands/purge_gate_photos.py` (lives with
the data it purges, same reasoning as `PeriodAttendance` living in `apps.attendance` not
`apps.academic`):

- `--retention-days` (default 90, spec/14 §7 criterion 4), `--dry-run`.
- Selects `GateEvent` rows with non-empty `photo_key` and `occurred_at` older than the cutoff;
  dry-run lists them (count + ids), real run blanks `photo_key` and deletes the underlying file
  from `MEDIA_ROOT` if present.
- `CronHostCommand` + `advisory_lock('educore:purge_gate_photos')` + `JobRun`, wired into
  `deploy/crontab` — identical shape to every other cron command in this repo
  (`mark_absent_students`, `sync_payment_status`, etc.).
- Biometric-template purge: no-op, same reason as above.

## Testing

- `test_dsar_bundle_completeness`: all 4 domains present for a known student fixture.
- `test_erasure_refused_when_active`: `PersonNotErasableError` on an `ACTIVE` student.
- `test_erasure_preserves_ledger`: after erasure, invoice/payment/ledger rows for that student
  are untouched and still balance; only `Person` fields change.
- `test_erasure_writes_audit_event`.
- `test_dsar_cross_tenant_404`.
- `purge_gate_photos` dry-run vs real-run tests (counts match, files actually removed on real run,
  untouched below the retention window).

## Non-goals (logged as separate new Open Items, not built here)

- Biometric enrollment / face-template model (blocks CMP-010's and CMP-012's biometric clauses)
  — P2 face-recognition milestone.
- Rectification/restriction/portability admin tooling beyond what already exists: rectification
  is already served by existing Person/Student/Staff edit endpoints (no new work needed);
  a dedicated "restrict processing" flag is a genuinely new, separable feature.
- Notifications/wallet/campus-life data in the DSAR bundle.
