# 02 — Identity, Tenancy and Access Control

## 1. Scope

Authentication, session handling, multi-tenancy enforcement, RBAC, user
provisioning, and feature entitlements. Everything else depends on this module.

## 2. Core entities

```
foundations(id, legal_name, brand_name, npwp, address, timezone, plan_tier, status)
schools(id, foundation_id, name, npsn, level[SD|SMP|SMA|SMK|MI|MTs|MA], curriculum, timezone)
users(id, foundation_id, email?, phone_e164, full_name, status, password_hash?, mfa_secret?)
persons(id, foundation_id, nik?, full_name, dob, gender, address) -- PII vault
students(id, foundation_id, school_id, person_id, nisn?, nis, photo_key?, status)
guardians(id, foundation_id, person_id, user_id, occupation?)
guardian_links(guardian_id, student_id, relation[FATHER|MOTHER|GUARDIAN], is_primary, can_pickup, financial_responsible)
staff(id, foundation_id, person_id, user_id, nip?, employment_type, join_date)
role_assignments(id, user_id, role, scope_type[FOUNDATION|SCHOOL], scope_id)
```

**Rule:** identity-bearing PII (NIK, DOB, address) lives only in `persons`.
Other tables reference `person_id`. This makes UU PDP erasure tractable (see 14 §4).

## 3. Authentication

| ID | Requirement |
|---|---|
| `IAM-001` | Staff MUST authenticate with email + password, or phone + OTP where no email exists. |
| `IAM-002` | Parents MUST authenticate with phone (E.164, `+62…`) + 6-digit OTP via WhatsApp, with SMS fallback after 60s. |
| `IAM-003` | OTP: 6 digits, 5-minute TTL, max 5 attempts, max 3 sends per phone per 15 minutes. |
| `IAM-004` | Access token 15 min; refresh token 30 days, single-use rotating, reuse detection revokes the whole family. |
| `IAM-005` | `foundation_admin` and `finance_officer` MUST have TOTP MFA enforced. Others MAY enable it. |
| `IAM-006` | Passwords: ≥10 chars, checked against a breached-password list, Argon2id hashed. |
| `IAM-007` | A device that is not seen for 90 days MUST be logged out and require full re-auth. |
| `IAM-008` | Account lockout after 10 failed attempts in 15 min; unlock by OTP or admin action. |
| `IAM-009` | A parent account with links to students at multiple schools MUST see all of them under one login. |

## 4. Authorization

### 4.1 Model
Role → permission set, scoped to `FOUNDATION` or `SCHOOL`. Permissions are
`module.resource.action` strings, e.g. `finance.invoice.issue`.

| ID | Requirement |
|---|---|
| `IAM-010` | Every API handler MUST declare required permissions; a handler with no declaration fails closed in CI. |
| `IAM-011` | Postgres RLS MUST enforce `foundation_id` on every tenant table — app-layer filtering alone is insufficient. |
| `IAM-012` | A `SCHOOL`-scoped role MUST NOT read data from a sibling school. |
| `IAM-013` | `teacher` MUST only read grades/attendance for classes they are assigned to, plus all students if they are homeroom teacher of that class. |
| `IAM-014` | `parent` MUST only access data for students they have an active `guardian_link` to. |
| `IAM-015` | Financial detail (invoice amounts, arrears) MUST only be visible to guardians with `financial_responsible = true`. |
| `IAM-016` | `platform_support` access to tenant data MUST require an explicit, time-boxed (max 8h) support grant approved by a `foundation_admin`, and every action is audited. |

### 4.2 Baseline permission matrix

| Permission group | foundation_admin | school_admin | finance_officer | teacher | counsellor | parent |
|---|---|---|---|---|---|---|
| School config | RW (all) | RW (own) | — | — | — | — |
| Student records | RW (all) | RW (own) | R | R (own classes) | R (own school) | R (own child) |
| Grades | R | RW | — | RW (own classes) | R | R (own child, published only) |
| Attendance | R | RW | — | RW (own classes) | R | R (own child) |
| Invoices & payments | RW | R | RW | — | — | R (own child) |
| Wallet top-up | R | R | RW | — | — | RW (own child) |
| Behaviour records | R | RW | R | RW (own classes) | RW | R (own child) |
| Clinic records | R | R | — | — | R | R (own child) |
| Payroll | RW | R (own) | RW | — | — | — |
| Hardware devices | RW | RW (own) | — | — | — | — |
| Audit log | R | R (own) | R (finance only) | — | — | — |

## 5. Lifecycle

| ID | Requirement |
|---|---|
| `IAM-017` | Bulk student import via XLSX/CSV with a dry-run diff preview; import MUST be atomic per file. |
| `IAM-018` | Import validates NISN (10 digits), NIK (16 digits, checksum-free but length-enforced), phone E.164, duplicate detection on (NISN) then (full_name + dob). |
| `IAM-019` | Student status machine: `PROSPECT → ACTIVE → (INACTIVE | GRADUATED | TRANSFERRED_OUT)`. Status change MUST cascade: stop invoice generation, freeze wallet, revoke card. |
| `IAM-020` | Deactivating a student MUST NOT delete history; wallet residual balance goes to a refund queue (see 07 §7). |
| `IAM-021` | Staff offboarding MUST revoke sessions within 60 seconds and reassign owned classes via a forced picker. |
| `IAM-022` | Year rollover: promote all `ACTIVE` students to next-grade classes with a reviewable mapping screen; retained students are flagged, not blocked. |

## 6. Feature entitlements

| ID | Requirement |
|---|---|
| `IAM-023` | `foundation_entitlements(foundation_id, module_key, enabled, limits jsonb)` gates every module. |
| `IAM-024` | A disabled module MUST return `403 MODULE_NOT_ENTITLED` and MUST be hidden from navigation, not merely disabled visually. |
| `IAM-025` | Module keys: `academic, attendance, finance, wallet, campus_life, payroll, analytics, hardware`. |

## 7. API surface

```
POST   /auth/login                  {identifier, password?}          -> {challenge|tokens}
POST   /auth/otp/request            {phone}                          -> {challenge_id, expires_at}
POST   /auth/otp/verify             {challenge_id, code}             -> {tokens}
POST   /auth/refresh                {refresh_token}                  -> {tokens}
POST   /auth/logout
GET    /me                                                           -> {user, roles, schools, entitlements}
POST   /me/mfa/enroll | /me/mfa/verify
GET    /schools | POST /schools | PATCH /schools/:id
GET    /students?school_id&class_id&status&q
POST   /students | PATCH /students/:id | POST /students/:id/status
POST   /students/import           (multipart, ?dry_run=true)
GET    /students/:id/guardians | POST /students/:id/guardians
GET    /staff | POST /staff | POST /staff/:id/offboard
POST   /roles/assign | POST /roles/revoke
POST   /academic-years/:id/rollover  {mapping}
```

## 8. Acceptance criteria

1. A `school_admin` of School A receives 404 (not 403 — do not leak existence) for a School B student id.
2. A parent linked to two children at two schools sees both after one OTP login.
3. Importing a 1,200-row student XLSX with 3 duplicate NISNs rejects the whole file and reports exactly the 3 offending rows.
4. Revoking a teacher's role invalidates their active access token within 60s.
5. Disabling the `wallet` entitlement removes the wallet tab from the parent app on next launch.
