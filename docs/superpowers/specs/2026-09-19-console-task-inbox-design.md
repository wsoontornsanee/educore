# Web Console: Kotak tugas (Task Inbox) — Design

**Notion task:** [Web Console: Build Kotak tugas (task inbox) module](https://app.notion.com/p/3df347a66594818b99abc3f0a58706a6)

## Decision

v1 is a **read-only** per-user queue at `/web/home/inbox/` (`console-inbox`), replacing the `inbox` nav item's `coming_soon` stub. Confirmed with the user: no approve/reject buttons yet — there is no web page for any of the underlying workflows (only JSON APIs), and money-moving actions on the web deserve their own review. Each module's own console page will add actions when it ships.

The Notion item said "no existing backing feature to reuse". Investigation found the *workflows* already exist in their owning apps; only the per-user aggregation was missing. So this adds no model, no migration and no new state — `apps/identity/inbox.py` is a set of filtered queries over existing models.

## Sources and who sees them

An item is shown only to a user who could act on it, using the same predicate as the corresponding decide endpoint/service:

| Section | Source | Shown to |
|---|---|---|
| Persetujuan keuangan | `foundation.approvals.list_foundation_approvals(status='pending')` (discount / waiver / refund) | foundation admin (`is_foundation_admin`, what the approve services enforce) |
| Penghapusbukuan piutang | `InvoiceWriteOffRequest` PENDING | foundation admin |
| Izin & sakit siswa | `AbsenceRequest` PENDING | holders of `attendance.write`, only for schools they hold it in |
| Rapor menunggu tinjauan | current `ReportCard` PENDING_REVIEW | holders of `school_config.write` (the approve action's permission), same school scoping |
| Permintaan mengganti kelas | `TimetableSubstitution` PENDING, date ≥ today | the assigned substitute (own `Staff` row); personal, no permission |

School scoping: `None` when the permission is held foundation-wide, else the set of schools where the user holds it (empty ⇒ section skipped without querying).

Empty sections are omitted; no sections ⇒ an empty state. Rows are capped at 25 per section with the real total shown ("Menampilkan N dari M item").

## Non-goals (follow-ups)

- Approve/reject actions from the inbox.
- Payroll approvals (no payroll domain model — see the existing Open Item).
- Overdue-item / notification feed sources; badge count in the nav.
