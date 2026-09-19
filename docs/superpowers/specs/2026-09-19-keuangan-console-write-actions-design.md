# Keuangan Web Console: Write Actions — Design

**Notion task:** [Web Console: Keuangan write actions](https://app.notion.com/p/3df347a665948114b428cb2f915d7a7e)

**Builds on:** PR #201 (read-only Keuangan console pages: `apps/finance/web_views.py`, `web_urls.py`, `frontend/templates/pages/finance_*.html`).

## Problem

The Keuangan console pages list pending discounts, pending write-off requests, gateway discrepancies and invoices, but every mutation still requires the JSON API. Finance staff cannot act on what they see. This slice adds the four write actions the read-only slice deferred.

## Scope (confirmed with user)

In scope:

1. Resolve a gateway reconciliation discrepancy (settle / waive / escalate, with notes).
2. Approve or reject a discount request (reason required).
3. Approve or reject an invoice write-off request (notes).
4. Record a cash payment from the Tagihan page.

Out of scope: refund approvals, invoice cancel, write-off request *creation*, a student search widget, HTMX partial swaps.

## Existing behavior this reuses (no new domain logic)

| Action | Service | Already enforces |
|---|---|---|
| Resolve discrepancy | `apps.finance.services.reconciliation.resolve_discrepancy(discrepancy_id, resolution, resolved_by, foundation_id, notes)` | PENDING-only transition, allowed resolutions `MANUAL_SETTLED`/`WAIVED`/`ESCALATED` |
| Approve / reject discount | `approve_discount` / `reject_discount` (`services/invoicing.py`) | PENDING_APPROVAL-only, `is_foundation_admin`, non-empty reason, audit event |
| Approve / reject write-off | `approve_invoice_write_off` / `reject_invoice_write_off` (`services/invoicing.py`) | PENDING-only, `is_foundation_admin`, ledger journal on approve, audit event |
| Cash payment | `apps.finance.services.payments.record_cash_payment(school, student, amount, invoice_ids, received_by, notes)` | amount > 0, receipt number, allocation to invoices (oldest first when `invoice_ids` is None), ledger journal, domain event, audit event |

Two existing gaps this slice closes because the console makes them reachable by more users:

- `resolve_discrepancy` writes **no audit event** (AGENTS.md red line: every mutating action audits). Add an `audit()` call inside the service (`finance.reconciliation.discrepancy_resolved`, diff: resolution and notes) so the JSON API benefits too. Covered by a service-level test.
- `record_cash_payment` audits with `role='STAFF'` and is already complete; no change.

## Architecture

### Approach: plain POST, redirect back, flash message

Each action is a POST-only view. On success or failure it adds a `django.contrib.messages` message and redirects back to the originating page (POST/redirect/GET). No JavaScript beyond what `base.html` already loads, no per-row partial templates. Django messages middleware and context processor are already installed; the console base template gains one shared partial that renders queued messages.

Rejected alternative: HTMX row swaps. They need a partial template per row type and duplicate the page's own rendering. Revisit if a page needs live updates.

### Views (`apps/finance/web_views.py`)

Extend the existing `FinanceConsoleView` gate into a shared mixin so read and write views share one gate:

- `FinanceConsoleGateMixin`: login required, `has_permission_in_any_scope(required_permission)`, `has_staff_profile`, resolves `foundation_id` and `school_ids = staff_school_scope(...)`, and exposes `scoped(model, school_field)`. `FinanceConsoleView` (read pages) is refactored onto it with no behavior change.
- `FinanceActionView(FinanceConsoleGateMixin, View)`: POST-only (`http_method_names = ['post']`). Template method: `redirect_url_name`, `required_permission`, `get_object()` (scoped, 404 on miss), `perform(obj)` (calls the service). It converts `ValidationError`/`ValueError` to an error message and `PermissionDenied` from a *service* to an error message ("Anda tidak berwenang…") rather than a 403 page, since the user was allowed to reach the view. Unexpected exceptions propagate.

Concrete views and URLs (all under `/web/finance/`):

| View | URL | `required_permission` | Object lookup (scoped) |
|---|---|---|---|
| `DiscrepancyResolveView` | `reconciliation/discrepancies/<int:pk>/resolve/` | `finance.payment.write` | `PaymentDiscrepancy`, foundation-scoped (batches are foundation-level, same as the read page and API) |
| `DiscountDecisionView` | `receivables/discounts/<int:pk>/<approve\|reject>/` | `finance.invoice.write` | `Discount`, `student__school_id` in scope |
| `WriteOffDecisionView` | `receivables/write-offs/<int:pk>/<approve\|reject>/` | `finance.invoice.write` | `InvoiceWriteOffRequest`, `school_id` in scope |
| `CashPaymentView` | `billing/cash/` | `finance.invoice.write` | student by NIS, `school_id` in scope |

The `approve|reject` path segment is a `<str:decision>` restricted by the view (anything else is 404).

### Foundation-admin gating in the UI

The services require `is_foundation_admin` for approve/reject. The receivables template only renders the approve/reject controls when the view puts `can_decide = is_foundation_admin(user, foundation_id)` in the context; other users see the read-only row. The service check remains the enforcement; the hidden buttons are a courtesy so finance officers are not shown actions that always fail.

### Reason / notes input

Each row action is a small inline `<form method="post">` with a `reason`/`notes` text field and the button(s), CSRF token included. Discount approve/reject require a non-empty reason (service enforces; the view passes it through and shows the service's message). Write-off notes are optional on approve, and reject uses `notes` as the rejection reason. Discrepancy resolve has a select (`MANUAL_SETTLED`, `WAIVED`, `ESCALATED`) plus notes; the view validates the select against the allowed set before calling the service, and shows only rows whose `resolution == PENDING` with the form.

### Cash payment form (Tagihan page)

Fields: `nis` (text), `amount` (text parsed as `Decimal`), `notes` (optional). The view:

1. Looks up `Student` by `nis` within the caller's scope (`school_id` in `school_ids`, foundation-scoped). Not found is an error message (does not reveal whether the NIS exists in another school or tenant).
2. Parses `amount` with `Decimal`, rejecting non-numeric, non-positive, or more than 2 decimal places.
3. Calls `record_cash_payment(school=student.school, student=student, amount=amount, received_by=request.user, notes=notes)` inside `tenant_context(foundation_id)` (the service uses tenant-scoped managers).
4. Flashes success with the receipt number and redirects to the billing page. The new payment appears in "Pembayaran terbaru".

Allocation uses the service's existing default (open invoices, oldest first). No invoice picker in this slice.

### Idempotency and double submit

Row actions are state transitions guarded by the services' status checks: a second submit gets the service's "already processed" error as a flash message. Cash payment has no natural idempotency key. A double click creates two payments, so the submit button disables itself on submit (inline `onsubmit` handler, no framework) and the spec accepts that a determined double POST is a staff data-entry mistake correctable by the existing refund flow, matching the JSON API's behavior. (No `Idempotency-Key` on browser form posts; that rule targets ingest endpoints.)

### Messages partial

`frontend/templates/components/_console_messages.html` renders `messages` with level-based styling (`role="status"` for success, `role="alert"` for error). It is included at the top of the three finance pages. Strings go through the existing translation catalog.

## Security and tenancy

- Every write view uses the same gate as the read pages plus the write permission; anonymous users redirect to login, others get 403.
- Object lookups filter by `foundation_id` and the caller's school scope; other-tenant and out-of-scope ids are 404, never a "not permitted" that would confirm existence.
- CSRF protection stays on (standard Django form posts; no `csrf_exempt`).
- No PII in logs: flash messages contain receipt numbers and counts, never student names or NIS.
- Money is parsed as `Decimal` from a string and never touches float.

## i18n

New user-facing strings use `gettext`/`{% translate %}` with Indonesian source text. EN entries are hand-added to `locale/en/LC_MESSAGES/django.po` and compiled with `msgfmt` (never `makemessages`, per the repo's standing fuzzy-clobber rule). Only msgids not already present are appended.

## Testing

`apps/finance/tests/test_web_console_actions.py`, one focused class per action:

- **Each action:** happy path (state changed, flash message, redirect back), audit event written, anonymous redirect, 403 without the write permission, 403 without a Staff profile, 404 for another tenant's id, 404 for an out-of-scope school's id.
- **Approve/reject discount and write-off:** foundation admin succeeds; a finance officer without foundation-admin authority sees an error message and the row is unchanged; rejecting a discount without a reason shows the service error; a second approve shows "already processed"; approve buttons absent from the page for non-admins (render test).
- **Discrepancy resolve:** invalid resolution value is rejected before the service; already-resolved row shows an error; `MANUAL_SETTLED` settles the linked payment (existing behavior, asserted); service-level test for the new audit event.
- **Cash payment:** payment recorded with receipt number and allocated to the oldest open invoice; unknown NIS, another school's NIS (school-scoped user), amount `0`, `-5`, `abc`, `10.999` all show an error and create nothing; another tenant's NIS is not found.
- **GET on a write URL** returns 405.
- Regression: existing `test_web_console` and API tests (`test_gateway_reconciliation`, discount and write-off tests) still pass; known baseline failures (`test_send_time_validation_cancels_when_paid`, `test_api_cancel_and_write_off`, `test_discount_approval_action`) are pre-existing on `main` and unaffected.

## Known limitations (not addressed here)

- `resolve_discrepancy(MANUAL_SETTLED)` flips a linked payment to SETTLED without allocating it to invoices or posting a ledger journal. This was existing service behavior when this spec was written; it was fixed independently in PRs #220 and #222 (allocation and ledger journal now happen). The console dropdown still forces a deliberate choice (placeholder option, `required`) because settling is money-affecting.
- No student search widget: cash entry requires knowing the NIS.
- Cash payments target open invoices oldest-first; the operator cannot choose specific invoices from the console.

## Rollout

Additive: three URL registrations, view classes, template partial, and small template changes. No migrations. No feature flag; the actions are gated by the same RBAC permissions as the API.

## Update: MISSING_IN_SYSTEM and the data-repair report

`resolve_discrepancy(MANUAL_SETTLED)` now refuses a discrepancy with no linked Payment (a MISSING_IN_SYSTEM record): there is nothing to allocate or journal, so recording it as settled would claim money the books do not hold. It raises an id-ID `ValueError` before any state change (the console flashes it, the API returns 400). WAIVED and ESCALATED still work, and the console no longer offers "Selesaikan manual" on payment-less rows. `manage.py report_unbooked_settled_payments` is a read-only report of SETTLED payments missing a ledger journal, allocation or receipt number (the leftovers of the old status-only paths); it never writes. AMOUNT_MISMATCH settling at the system amount still awaits finance sign-off.
