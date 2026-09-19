# Kantin & dompet console: refund and reconciliation actions — Design

**Notion task:** Operasional console: write actions (remaining slice). Slice 1 (gate check-in/override, exam publish) shipped in PR #216.

Follows the established console write pattern (gate console #216, Keuangan write actions #217): POST-only views over the same services the JSON API calls, flash message, redirect back to the page for the same school. No new domain logic, no migration.

## What this adds

For holders of `finance.payment.write` in the selected school, the canteen page gains two work queues:

- **Refund saldo dompet** (WAL-026): pending `WalletRefundRequest`s. *Bayar ke rekening wali* (`mark_refund_paid`, bank details required in the form) and *Donasikan* (`mark_refund_donated`, explicit consent checkbox required).
- **Rekonsiliasi kekurangan saldo** (REC-015/026): open `WalletReconciliation` cases with the school's exposure. *Lunasi tunai* (`settle_reconciliation_with_cash`), *Hapus buku* (`write_off_reconciliation_case`, reason required), *Tagihkan* (`invoice_reconciliation_case`), *Kirim ulang pemberitahuan* (`resend_reconciliation_notice`).

Users with only `wallet.topup.read` (e.g. the canteen operator) still get the read-only monitor; the queues and their controls are not rendered for them.

## Authorization and scoping

- Page gate unchanged (`wallet.topup.read` + Staff profile). Each action view requires `finance.payment.write`, the same key as the JSON actions, resolved through `StaffConsoleMixin.console_context`: the school comes from `?school_id=` and must be one the user may *write* for.
- The target row is looked up by id inside that school **and** tenant (`student__school_id`, `foundation_id`); anything else is a 404. A school-scoped officer therefore cannot act on another school's row (the JSON endpoints look up by foundation only).
- Services run inside `tenant_context(foundation_id)` (they use tenant-scoped managers).

## Behavior notes

- Service `ValueError`s (`INVALID_STATE`, `CONSENT_REQUIRED`, `WALLET_NOT_ACTIVE`, ...) are translated to Indonesian operator messages and flashed; a repeat submit gets "sudah diproses" and changes nothing (the services' state checks are the idempotency guard).
- Cash amount is parsed as `Decimal` (positive, ≤ 2 decimal places, finite); never float.
- Destructive/money actions (paid, donate, cash, write-off, invoice) carry a client-side confirm; the audit trail is written by the services.

## Not in scope

- Approving refunds *requests* (those are queued by student exit, not by this console).
- The JSON API's own cross-school gap (found while building this; logged separately).
- Browser/visual verification: covered by rendered-HTML tests only.
