# 10 — Campus Life (Clinic, Behaviour, Library, Counselling)

## 1. Scope

The modules that make EduCore an operating system rather than a finance tool:
school health unit (UKS), behaviour and discipline, e-library and asset checkout,
and guidance counselling (BK).

## 2. Entities

```
clinic_visits(id, school_id, student_id, occurred_at, complaint, vitals jsonb,
              treatment, medication_given, outcome[RETURNED_TO_CLASS|SENT_HOME|REFERRED],
              handled_by, guardian_notified_at)
health_profiles(id, student_id, blood_type, allergies[], chronic_conditions[],
                medications[], emergency_contacts jsonb, consent_version)
medication_stock(id, school_id, name, unit, quantity, expiry_date, reorder_level)
behaviour_reasons(id, school_id, code, label, points, category[POSITIVE|MINOR|MAJOR], active)
behaviour_records(id, school_id, student_id, reason_id, points, note, occurred_at, recorded_by, acknowledged_by_guardian_at)
behaviour_cases(id, student_id, opened_at, trigger, status, assigned_counsellor_id, resolution, closed_at)
counselling_sessions(id, case_id?, student_id, counsellor_id, occurred_at, type, notes_encrypted, follow_up_at, confidentiality[NORMAL|RESTRICTED])
library_items(id, school_id, type[BOOK|EBOOK|EQUIPMENT], title, author, isbn?, copies_total, copies_available, location)
loans(id, item_id, borrower_type[STUDENT|STAFF], borrower_id, borrowed_at, due_at, returned_at, fine, status)
```

## 3. Clinic (UKS)

| ID | Requirement |
|---|---|
| `LIF-001` | A clinic visit MUST be recordable in under 60 seconds: student lookup (card tap or search) → complaint → treatment → outcome. |
| `LIF-002` | Opening a student record MUST surface allergies, chronic conditions and current medications **above the fold**, in a visually distinct alert block. |
| `LIF-003` | Outcome `SENT_HOME` or `REFERRED` MUST immediately notify all guardians and the homeroom teacher, and MUST create a corresponding `SAKIT` attendance adjustment for the remaining periods. |
| `LIF-004` | Administering medication MUST require a recorded guardian consent (standing consent on the health profile, or per-incident confirmation), and MUST decrement `medication_stock`. |
| `LIF-005` | Stock below `reorder_level` or within 30 days of expiry MUST appear on a clinic dashboard alert. |
| `LIF-006` | Health data MUST be restricted to `clinic_officer`, `school_admin`, and the student's guardians. Teachers see only a non-clinical "has medical alert" flag unless the school enables `teacher_sees_allergies` (default ON for allergies only, because it is a safety matter). |
| `LIF-007` | Clinic notes MUST be encrypted at rest with the PII key and excluded from all general exports. |

## 4. Behaviour and discipline

| ID | Requirement |
|---|---|
| `LIF-008` | Points MUST accumulate per term and reset at term boundaries; lifetime history remains queryable. |
| `LIF-009` | Configurable escalation thresholds MUST auto-open a `behaviour_case` and assign a counsellor (e.g. −25 points in a term). |
| `LIF-010` | Positive points MUST be first-class, and the UI MUST show positive and negative totals separately — never only a net number. |
| `LIF-011` | Guardians MUST be notified of `MAJOR` records immediately and of `MINOR` records in a daily digest. |
| `LIF-012` | A guardian MUST be able to acknowledge a record; acknowledgement is timestamped and visible to staff. |
| `LIF-013` | A behaviour record MUST NOT be deletable; corrections are a superseding record with a reason, and the original is struck through in the UI. |
| `LIF-014` | Behaviour summary MUST appear on the report card when the school enables `rapor_includes_behaviour`. |

## 5. Counselling (BK)

| ID | Requirement |
|---|---|
| `LIF-015` | Counselling notes MUST support `RESTRICTED` confidentiality: visible only to the authoring counsellor and the principal, never to teachers or guardians, and excluded from report cards and exports. |
| `LIF-016` | Access to any `RESTRICTED` note MUST write an audit event naming the reader. |
| `LIF-017` | Safeguarding escalation path MUST exist: a session may be flagged `URGENT`, which notifies the principal directly and bypasses normal queues. |
| `LIF-018` | Follow-up dates MUST generate counsellor task reminders. |

## 6. Library and assets

| ID | Requirement |
|---|---|
| `LIF-019` | Checkout by student card tap; loan period and max concurrent loans configurable per school and borrower type. |
| `LIF-020` | Overdue fines MUST be configurable (per day, with a cap) and MUST post to the student's invoice as an `OTHER` fee line rather than being collected ad hoc in cash. |
| `LIF-021` | Lost item handling MUST charge a configured replacement cost through the same invoice mechanism, with staff approval. |
| `LIF-022` | Equipment checkout (laptops, sports gear) MUST record condition on issue and return. |
| `LIF-023` | Availability MUST be accurate under concurrent checkout (row-level lock on `copies_available`). |

## 7. API

```
GET/POST /clinic/visits | GET /students/:id/health-profile | PUT /students/:id/health-profile
GET/POST /clinic/medication-stock
GET/POST /behaviour-reasons | POST /behaviour-records
GET  /students/:id/behaviour?term_id
POST /behaviour-records/:id/acknowledge
GET/POST /behaviour-cases | PATCH /behaviour-cases/:id
POST /counselling/sessions   (confidentiality-aware)
GET/POST /library/items | POST /library/loans | POST /library/loans/:id/return
GET  /library/overdue
```

## 8. Acceptance criteria

1. Opening a student with a peanut allergy shows the allergy alert before any other content.
2. `SENT_HOME` at 10:15 creates `SAKIT` for periods 4–8 and notifies both guardians within 30s.
3. A teacher querying a `RESTRICTED` counselling note receives 403 and the attempt is audited.
4. A Rp 500/day overdue fine appears on next month's invoice, not as a cash demand.
5. Two librarians checking out the last copy simultaneously — exactly one succeeds.
