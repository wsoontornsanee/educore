# 00 — Product Overview

## 1. What we are building

EduCore is a multi-tenant school operating system for Indonesian private schools
(sekolah swasta, madrasah, pesantren modern, National-Plus/SPK). It replaces the
4–6 disconnected tools a school runs today with one database spanning academics,
attendance hardware, tuition collection, cashless canteen, campus life and payroll.

**The integration is the product.** A gate scan, a canteen purchase, an unpaid
invoice and a report card all resolve to the same `student_id` in the same database.

## 2. Personas

| Persona | Role code | Primary surface | Core job |
|---|---|---|---|
| Foundation head (Ketua Yayasan) | `foundation_admin` | Foundation portal (web) | Cross-campus financial and enrolment oversight, audit |
| School principal (Kepala Sekolah) | `school_admin` | Admin web | Run one campus end to end |
| Finance officer (Bendahara) | `finance_officer` | Admin web | Issue invoices, reconcile, chase arrears |
| Homeroom / subject teacher (Guru) | `teacher` | Teacher suite (web + mobile) | Attendance, grading, behaviour, lesson plans |
| Counsellor (Guru BK) | `counsellor` | Teacher suite | Behaviour cases, counselling logs |
| Clinic officer (Petugas UKS) | `clinic_officer` | Admin web | Health visits, medication, incidents |
| Canteen operator | `canteen_operator` | POS terminal | Ring up sales against student wallets |
| Parent / guardian (Wali Murid) | `parent` | Parent mobile app | Know where the child is, pay, see grades |
| Student | `student` | Smart ID card + student portal | Tap to enter, tap to buy, submit homework |
| Platform support | `platform_support` | Internal console | Tenant onboarding, incident triage |

## 3. Tenancy model

```
Foundation (yayasan)          -- billing + governance boundary
└── School (sekolah/unit)     -- SD / SMP / SMA / MI / MTs / MA, has own NPSN
    └── Academic Year (tahun ajaran, e.g. 2026/2027)
        └── Term (semester ganjil / genap)
            └── Class (rombel, e.g. VII-A)
                └── Enrolment (student ↔ class ↔ term)
```

- A **tenant** = one foundation. Row-level isolation on `foundation_id`.
- A student belongs to exactly one school at a time; transfers preserve history.
- Users may hold roles at **multiple** schools inside one foundation.

## 4. Glossary (Indonesian domain terms — do not translate in code)

| Term | Meaning | Code identifier |
|---|---|---|
| Yayasan | School foundation; legal owner | `foundation` |
| SPP | Monthly tuition fee | `fee_type=SPP` |
| Uang pangkal | One-time entry fee | `fee_type=ENTRY` |
| Rombel | Class group | `class_group` |
| Rapor | Report card | `report_card` |
| Kurikulum Merdeka | National curriculum in force | `curriculum=MERDEKA` |
| TP / CP | Learning objective / learning outcome | `learning_objective` |
| DAPODIK | MoECRT school data system | `integration=dapodik` |
| EMIS | MoRA system for Islamic schools | `integration=emis` |
| NPSN | National school ID (8 digits) | `school.npsn` |
| NISN | National student ID (10 digits) | `student.nisn` |
| NIK | National citizen ID (16 digits) | `person.nik` |
| UKS | School health unit | `clinic` |
| BK | Guidance & counselling | `counselling` |
| QRIS | National QR payment standard | `payment_method=QRIS` |
| VA | Bank virtual account | `payment_method=VA` |
| PPh 21 | Employee income tax | `payroll.pph21` |
| BPJS | National health/employment insurance | `payroll.bpjs` |
| UU PDP | Personal Data Protection Law No. 27/2022 | — |

## 4a. Architecture in one line

One Django monolith, one MySQL database, every module a Django app in a single
repository (frontend included), all scheduled work run by system cron. See spec 01.

## 5. Revenue model (affects product surfaces)

1. **Subscription** — IDR 15k–35k per active student per month, tier-gated by module.
2. **Payment processing** — IDR 2,500–4,000 flat per tuition transaction + 0.7% QRIS.
3. **Campus take-rate** — 1–2% of cashless campus spend (canteen, uniform, stationery).
4. **Hardware** — gates, cameras, POS terminals, card issuance; ~25% gross margin.

Implications the build MUST honour:
- Every module is **feature-flagged per foundation** (see 02 §6).
- Active-student count is a metered, auditable number (see 15 §4).
- Fee logic is configurable per school: who absorbs the convenience fee (school or parent).

## 6. 18-month product milestones

| Phase | Window | Scope |
|---|---|---|
| P0 — Pilot core | M0–M4 | Identity, foundation portal, attendance (RFID), SPP invoicing + VA, parent app v1 |
| P1 — Classroom | M5–M9 | Academic module, teacher suite, report cards, homework, WhatsApp notifications |
| P2 — Campus economy | M10–M13 | Canteen wallet + POS, kiosks, face recognition, campus life |
| P3 — Enterprise | M14–M18 | Payroll, DAPODIK/EMIS export, analytics warehouse, multi-campus consolidation |

Target at M18: 50 schools, 40,000 active students, $300K ARR.

## 7. Non-goals (v1)

- Public-school (negeri) procurement flows.
- Full LMS content authoring / video hosting.
- Lending, BNPL or any credit product against tuition.
- Native desktop apps.
- Real-time currency conversion at point of payment (see spec 16 §5).
- Offline-first parent app (teacher + POS are offline-tolerant; parent app is not).
