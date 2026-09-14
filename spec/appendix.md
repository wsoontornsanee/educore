# Appendix — Build Order and Working Agreement

## 1. Suggested implementation sequence

Each step is shippable and demoable on its own.

| Step | Build | Spec | Demo moment |
|---|---|---|---|
| 0 | Django project skeleton, `core` app: `TenantModel`, `MoneyField`, `AuditEvent`, `DomainEvent`, `TaskQueue`, `JobRun`, `drain_tasks`, crontab | 01, 16 | `manage.py migrate` on empty MySQL; a cron job logs a run |
| 1 | Tenancy, auth, RBAC, seed data | 02, 01 | Foundation admin logs in, sees two schools |
| 2 | Student/staff records + bulk import | 02 | Import 1,200 students from the pilot school's XLSX |
| 3 | Credentials + gate events + edge agent stub | 05, 12 | Card tap shows on the live gate console |
| 4 | Notifications service + WhatsApp arrival message | 13 | Parent's phone buzzes at 06:55 |
| 5 | Parent app v1: login, home, attendance | 08 | The pilot's "wow" demo |
| 6 | Fee catalogue, invoice generation, VA/QRIS, webhooks, ledger | 06 | Parent pays SPP in the app; ledger balances |
| 7 | Arrears ladder + AR aging | 06, 15 | Bendahara stops chasing on WhatsApp manually |
| 8 | Foundation dashboard on the read model | 03, 15 | The renewal conversation has numbers |
| 9 | Academic: gradebook, assessments, timetable | 04 | Teacher enters a term of scores with a keyboard |
| 10 | Teacher suite mobile: period attendance, behaviour | 09, 10 | 15-second attendance |
| 11 | Report cards | 04 | A branded rapor PDF a principal signs off |
| 12 | Wallet + POS offline-first + kiosks | 07, 12 | Canteen runs a full lunch service offline |
| 13 | Campus life: clinic, library, counselling | 10 | UKS visit → parent notified → attendance adjusted |
| 14 | Payroll | 11 | A run that reconciles to the rupiah |
| 15 | DAPODIK/EMIS export, PDP tooling, analytics | 14, 15 | Legal review passes |

## 2. Non-negotiable engineering rules

1. **Tenancy is enforced in three layers** (`TenantModel` + `TenantManager` + per-viewset cross-tenant test). Any tenant table without a `foundation` FK fails CI.
2. **Money is `DECIMAL(18,2)` + currency, and double-entry.** No float, no integer minor units, no single-sided adjustments. Display formatting is the frontend's job.
2b. **One Django monolith, MySQL only, cron for all jobs.** Introducing Redis, Celery, a broker or a second datastore requires an explicit decision record.
3. **Nothing financial or academic is hard-deleted.** Corrections are new records.
4. **Every mutating action writes an audit event.** No exceptions for "internal" jobs.
5. **Statutory rates, curriculum structures and export schemas are data, not code.**
6. **Every ingest endpoint is idempotent.** Devices retry; assume duplicates always.
7. **Offline is a first-class state for gates, POS and teacher mobile.** Design the sync before the screen.
8. **No PII in logs, analytics, error reports or SMS bodies.**
9. **Fail closed on permissions.** An undeclared handler is a build failure.
10. **id-ID first.** English is the translation, not the source.

## 3. What to ask the product owner before building

These are genuinely unresolved and will change the schema if answered late:

- Does the pilot foundation use a single bank, and does it support stable per-student VA numbers?
- Is the convenience fee absorbed by the school or passed to parents, per school or foundation-wide?
- Is `block_rapor_on_arrears` culturally acceptable to the pilot foundation? (`ACD-014`)
- Who legally holds the canteen wallet float? (`CMP-027`)
- Which curriculum variants are in scope at launch — Merdeka only, or also madrasah-specific subjects?
- What is the pilot's report-card template, exactly? Obtain a real signed sample before building 04 §4.
- Is face recognition in scope for the pilot, or card-only? (Consent workload is non-trivial.)
- Which non-IDR currencies are actually needed at launch, and by which schools? (Affects whether spec 16 §5 FX reporting ships in P0 or P3.)
- Does the pilot deployment have a dedicated cron host, or is it a single VM? (`ARC-013` assumes exactly one cron host.)
