# 14 — Compliance and External Integrations

## 1. Scope

UU PDP (Law 27/2022) obligations, data residency, government reporting
(DAPODIK / EMIS), payment gateway and banking integrations, and the security
posture required to sell to enterprise foundations.

**This module is a sales gate, not a nice-to-have.** A foundation's legal review
blocks the contract if these are absent.

## 2. Data residency and security

| ID | Requirement |
|---|---|
| `CMP-001` | All production data — database, object storage, backups, logs — MUST reside in Indonesian data centres. No cross-border replication. |
| `CMP-002` | Third-party subprocessors MUST be listed publicly with purpose and location; adding one requires a documented DPIA. |
| `CMP-003` | Encryption: TLS 1.3 in transit; AES-256 at rest; separate KMS keys for general PII, biometric templates, and health/counselling data. |
| `CMP-004` | Key rotation annually; a compromised key MUST be rotatable without data loss (envelope encryption). |
| `CMP-005` | Production access by staff MUST be via time-boxed, approved, audited sessions — no standing production DB credentials. |
| `CMP-006` | Penetration test annually; critical findings remediated within 30 days. |
| `CMP-007` | Backups MUST be encrypted and restore-tested quarterly (`NFR-004`). |

## 3. UU PDP obligations

| ID | Requirement |
|---|---|
| `CMP-008` | Lawful basis MUST be recorded per processing purpose. The school (foundation) is the **controller**; EduCore is the **processor**. The DPA template MUST reflect this. |
| `CMP-009` | Consent records MUST be versioned and timestamped for: biometric enrolment, photo/media use, health data processing, and marketing communication. Consent is per-purpose, never bundled. |
| `CMP-010` | A guardian MUST be able to withdraw consent for biometrics and media from the parent app; withdrawal MUST take effect within 24 hours (templates deleted, student reverts to card-only). |
| `CMP-011` | Data subject rights MUST be servable within 30 days: access (export), rectification, erasure, restriction, portability. An admin tool MUST exist for each — not a manual SQL task. |
| `CMP-012` | Erasure MUST reconcile with statutory retention: financial records retained 10 years, academic records per school policy (default permanent), biometrics deleted on exit or consent withdrawal, gate photos retained 90 days by default. |
| `CMP-013` | Retention policies MUST be enforced by an automated job with a dry-run report, not by hope. |
| `CMP-014` | A personal data breach MUST be detectable and reportable: an incident workflow with a 3×24-hour notification clock, evidence capture, and affected-subject listing. |
| `CMP-015` | Children's data MUST default to the most restrictive sharing settings; any widening is an explicit school-admin action that is audited. |
| `CMP-016` | Every PII export (any report containing NIK, NISN, DOB, address, health) MUST be watermarked with the requesting user and logged. |

## 4. Government reporting

| ID | Requirement |
|---|---|
| `CMP-017` | The data model MUST hold every field DAPODIK requires for students, staff and rombel, validated at entry (NISN 10 digits, NIK 16 digits, NPSN 8 digits). |
| `CMP-018` | Export MUST produce DAPODIK-compatible files for the current schema version, with a pre-export validation report listing every incomplete record and the specific missing field. |
| `CMP-019` | Madrasah schools MUST have the equivalent EMIS export path. |
| `CMP-020` | The export schema version MUST be configurable data, not code — ministry formats change between cycles. |
| `CMP-021` | If/when an official API becomes available, the adapter interface MUST allow swapping file export for API sync without domain changes. |
| `CMP-022` | Rapor data MUST be exportable in a format suitable for the national report-card system. |

## 5. Payment and banking integrations

| ID | Requirement |
|---|---|
| `CMP-023` | Gateway adapters MUST implement a common `PaymentProvider` interface: `createVirtualAccount`, `createQris`, `getStatus`, `refund`, `verifyWebhook`, `fetchSettlement`. |
| `CMP-024` | Launch providers: **Midtrans** and **Xendit**. Direct bank VA (BCA/Mandiri/BRI) MAY be added later behind the same interface. |
| `CMP-025` | Provider credentials MUST be per-foundation, stored encrypted, rotatable without redeploy. |
| `CMP-026` | Settlement files MUST be fetched daily and reconciled (`FIN-024`). |
| `CMP-027` | EduCore MUST NOT hold funds in a way that constitutes unlicensed payment services: settlement flows to the school's own account; the wallet float MUST be held per the licensed partner's arrangement. **Confirm the structure with counsel before go-live.** |
| `CMP-028` | PCI scope MUST be avoided entirely — no card PAN ever touches EduCore systems. |

## 6. Other integrations

| Integration | Purpose | Priority |
|---|---|---|
| WhatsApp Business API (Meta / BSP) | Primary parent channel | P0 |
| SMS gateway | OTP + fallback | P0 |
| Google Workspace / Microsoft 365 | Staff SSO, calendar sync | P2 |
| Accounting export (Accurate, Jurnal) | Foundation finance handoff | P2 |
| Zoom / Google Meet | Online class links on timetable | P3 |

## 7. Acceptance criteria

1. A DAPODIK export dry-run lists every student missing a NISN, by name and class.
2. Withdrawing biometric consent deletes the face template within 24h and the student still enters with a card.
3. A data-access request produces a complete per-student export bundle within one admin session.
4. The retention job dry-run shows exactly which gate photos older than 90 days would be deleted.
5. Switching a foundation from Midtrans to Xendit requires only a credential change, no code deploy.
6. No log, error report or analytics payload anywhere contains a NIK.
