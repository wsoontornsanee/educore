# Campus Life: Clinic / UKS Unit Management (LIF-001..007) — Design

Date: 2026-09-18
Spec source: `spec/10-campus-life.md` §3 (LIF-001..007)
Notion open item: Campus Life: Clinic / UKS Unit Management (LIF-001..007)

## 1. Scope

Backend slice for the school health unit (UKS): clinic visit recording, health
profile alerts, medication stock, guardian consent gating, and the resulting
attendance/notification side-effects. No frontend exists in this repo (same
backend-only precedent as every other Campus Life / Teacher Suite slice) — the
"under 60 seconds" (LIF-001) and card-tap lookup requirements are satisfied by
API shape (single POST creates the whole visit; student lookup accepts either
`student_id` or a search query), not by literal timing tests.

## 2. Location

New submodule inside the existing `apps.campus` app, not a new Django app.
`apps.campus` already owns the rest of spec/10 (Behaviour, LIF-008/009+), so
Clinic stays under the same spec-to-app mapping rather than splitting one
spec document across two apps.

New files:
- `apps/campus/models.py` — append `ClinicPolicy`, `HealthProfile`,
  `MedicationStock`, `ClinicVisit`.
- `apps/campus/crypto.py` — Fernet encrypt/decrypt helpers for clinic free
  text, same per-app-key convention as `apps.hardware.crypto` /
  `apps.partners.crypto` (`EDUCORE_CLINIC_FERNET_KEY`, SECRET_KEY-derived
  fallback for dev/test).
- `apps/campus/services_clinic.py` — clinic domain services, split out from
  `services.py` (which stays behaviour-only) since clinic logic is a
  sizable, independent unit.
- `apps/campus/views_clinic.py` / additions to `apps/campus/serializers.py`
  and `apps/campus/urls.py` — REST endpoints.

## 3. Models

```
ClinicPolicy(TenantModel)
  school            OneToOneField(identity.School)
  teacher_sees_allergies  BooleanField(default=True)   # LIF-006

HealthProfile(TenantModel)
  student            OneToOneField(identity.Student)
  blood_type          CharField(blank=True, default='')
  allergies           JSONField(default=list)
  chronic_conditions  JSONField(default=list)
  medications         JSONField(default=list)
  emergency_contacts   JSONField(default=dict)

MedicationStock(TenantModel)
  school        ForeignKey(identity.School)
  name          CharField
  unit          CharField
  quantity      IntegerField
  expiry_date   DateField
  reorder_level IntegerField

ClinicVisit(TenantModel)
  student                    ForeignKey(identity.Student)
  occurred_at                DateTimeField
  complaint_encrypted        TextField    # Fernet ciphertext, LIF-007
  treatment_encrypted        TextField    # Fernet ciphertext, LIF-007
  vitals                     JSONField(default=dict)   # plaintext, structured
  medication_given           ForeignKey(MedicationStock, null=True)
  medication_quantity_used   IntegerField(null=True)
  outcome                    CharField(choices=RETURNED_TO_CLASS|SENT_HOME|REFERRED)
  handled_by                 ForeignKey(identity.Staff)
  guardian_consent_confirmed BooleanField(default=False)
  guardian_consent_note      CharField(blank=True, default='')
  guardian_notified_at       DateTimeField(null=True)
```

`vitals` and `outcome`/`medication_given` stay plaintext (queryable for
dashboards/reports); only the two free-text narrative fields
(`complaint`, `treatment`) are encrypted — these are the fields that can
contain unbounded, sensitive free text, matching LIF-007's "clinic notes"
wording without making the stock/outcome dashboard (LIF-005) pay a
decrypt-per-row cost.

## 4. RBAC

New migration in `apps.identity`:
- `RoleAssignment.ROLE_CLINIC_OFFICER = 'clinic_officer'` added to
  `ROLE_CHOICES`.
- `apps/identity/rbac.py`: `ROLE_PERMISSIONS['clinic_officer'] = {
  'student_records.read', 'clinic.read', 'clinic.write',
  'analytics.event.write' }`.
- Add `'clinic.write'` to `ROLE_FOUNDATION_ADMIN` and `ROLE_SCHOOL_ADMIN`
  (both already hold `clinic.read`).
- `ROLE_PARENT` already holds `clinic.read` (pre-existing) — no change
  needed there; the endpoint itself will still 403 in practice since no
  guardian-scoping helper is built this slice (see §8 Non-goals).

Health-data restriction (LIF-006): `HealthProfile`/`ClinicVisit` detail
endpoints require `clinic.read`/`clinic.write`. Teacher-facing endpoints
(existing `behaviour.read`-gated student summary) gain a
`has_medical_alert: bool` field (true if any of allergies/chronic/meds is
non-empty) always, and the raw `allergies` list only when
`ClinicPolicy.teacher_sees_allergies` is enabled for that school.

## 5. Consent (LIF-004)

Reuse `apps.compliance.ConsentRecord` (purpose `HEALTH_DATA`, already
defined per CMP-009 / PR #136) for standing consent instead of a new
boolean field, so consent stays versioned/append-only/audit-able the same
way as biometric and other purposes.

New small read helper in `apps/compliance/services.py`:

```python
def has_active_health_consent(subject_type: str, subject_id: int, foundation_id: int) -> bool:
    """True if the subject's latest HEALTH_DATA ConsentRecord version is granted and not withdrawn."""
```

`record_clinic_visit` requires, before decrementing stock for a
`medication_given` visit: `has_active_health_consent(...)` **or**
`guardian_consent_confirmed=True` (per-incident path — clinic officer
records who confirmed and how, in `guardian_consent_note`, since no
parent-facing consent-capture client exists in this repo). Neither present
→ `ValidationError`.

## 6. Attendance & notification side-effects (LIF-003)

On `outcome` ∈ {`SENT_HOME`, `REFERRED`}:
- Get-or-create today's `AttendanceDay` for the student, then call
  `apps.attendance.services.override_attendance_day(..., new_status=SAKIT,
  note=<clinic outcome + visit id>)` — reuses the existing day-level
  override mechanism (same one `approve_absence_request` already uses),
  not a new per-period mechanism.
- Dispatch a new `NotificationCategory.CLINIC_INCIDENT` (added to
  `apps/notifications/models.py`) to all of the student's guardians *and*
  the homeroom teacher, `immediate=True`/high-priority so it bypasses
  digesting — same shape as the existing `ABSENCE` category, still routed
  through the standard WA→Push→SMS→Email fallback ladder (not `EMERGENCY`,
  which is reserved for school-wide crises and bypasses quiet hours; a
  single student's clinic outcome doesn't need the quiet-hours bypass).
- Stamp `ClinicVisit.guardian_notified_at` on successful dispatch.

`RETURNED_TO_CLASS` outcomes do neither — no attendance change, no
notification.

## 7. Medication stock (LIF-004, LIF-005)

`record_clinic_visit` decrements `MedicationStock.quantity` under
`select_for_update()` in the same transaction as the visit insert,
rejecting (`ValidationError`) if the resulting quantity would go negative
— administering medication that isn't in stock is a data-entry error, not
a state the system should silently accept.

Dashboard alert (LIF-005) is a plain filtered read, computed on request —
`MedicationStock.objects.filter(Q(quantity__lt=F('reorder_level')) |
Q(expiry_date__lte=today + 30 days))` — no new cron command, no `JobRun`.
This is cheap, always-fresh data with no batch/reconciliation need, unlike
the existing cron jobs (which exist for things genuinely requiring
scheduled batch work: absence sweeps, settlement, digests).

## 8. Non-goals (logged as their own Notion Open Items, not built here)

- **Card-tap hardware student lookup**: LIF-001 mentions "card tap or
  search" — this slice only builds the search/ID-based lookup path (already
  sufficient for a sub-60s single-POST visit flow); wiring a physical
  card-reader terminal into clinic intake is a hardware-integration task
  or the same class as the existing gate/POS card-reuse work, out of scope
  here.
- **Parent-app guardian read scoping for clinic history**: `ROLE_PARENT`
  already carries `clinic.read` in RBAC (pre-existing), but — same
  recurring precedent as `get_visible_report_card`/homework/wallet
  refund — no parent-facing client exists anywhere in this repo yet to
  actually call it with guardian-of-this-student scoping. Endpoint auth
  stays permission-key-gated only; a future parent app slice would add the
  "is this user actually this student's guardian" check.

## 9. Testing plan

- Model/migration tests: new tables, RBAC migration (role + permission
  keys), field constraints.
- `apps/campus/crypto.py`: encrypt/decrypt round-trip, decrypt failure on
  wrong key.
- `services_clinic.record_clinic_visit`: all three outcomes; medication
  path with standing consent, with per-incident confirmation, and
  rejected without either; stock decrement and insufficient-stock
  rejection; SAKIT override + `CLINIC_INCIDENT` dispatch to guardians +
  homeroom teacher on SENT_HOME/REFERRED; no side-effects on
  RETURNED_TO_CLASS.
- RBAC: `clinic_officer` can read/write; `teacher` gets
  `has_medical_alert` only (plus allergies when policy enabled, not when
  disabled); non-clinic roles 403 on detail endpoints.
- Viewset cross-tenant 404 tests (Layer 3 tenancy, per `00_CORE.md` §2).
- Dashboard alert query: below-reorder and near-expiry rows included,
  healthy stock excluded.

Target ≥80% coverage on new business logic per `USER.md` SOP §5.
