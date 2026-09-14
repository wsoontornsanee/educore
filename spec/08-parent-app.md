# 08 — Parent App (Wali Murid)

## 1. Scope

The React Native mobile client. This is the surface that makes the school look
modern to the paying customer — and the channel through which money arrives.

**Design constraint:** the three things a parent opens the app for are
*Is my child safe? What do I owe? How are they doing?* Everything else is secondary.

## 2. Information architecture

```
Home (per-child)          -> today's status card, alerts, quick pay, wallet balance
Attendance               -> calendar heatmap, arrival times, absence request
Payments                 -> outstanding invoices, history, receipts, payment plans
Wallet                   -> balance, top-up, spend history, controls, nutrition
Academic                 -> grades, homework, report cards, timetable
Messages                 -> school announcements, teacher messages, permission slips
Profile                  -> children, guardians, notification prefs, language, logout
```

Child switcher is **persistent in the header**, not buried in settings.

## 3. Requirements

| ID | Requirement |
|---|---|
| `PAR-001` | Login is phone + OTP only (see 02 `IAM-002`). No password path for parents. |
| `PAR-002` | Home MUST show, for the selected child: today's attendance state with arrival time, outstanding balance, wallet balance, unread announcements, and next due date. |
| `PAR-003` | A guardian with multiple children MUST switch child in one tap; the app MUST remember the last selected child. |
| `PAR-004` | Arrival/departure notifications MUST deep-link into the attendance screen for that date. |
| `PAR-005` | Paying an invoice MUST take ≤3 taps from Home: Home → Pay → confirm method. |
| `PAR-006` | Payment screen MUST show the base amount, any convenience fee, and the total, before confirmation. |
| `PAR-007` | After VA/QRIS intent creation, the app MUST show clear step-by-step instructions per bank in Indonesian, a copyable VA number, and a countdown to expiry. |
| `PAR-008` | Payment confirmation MUST arrive as a push within 30 seconds of settlement; the invoice list MUST reflect it without manual refresh. |
| `PAR-009` | Receipts MUST be downloadable as PDF and shareable. |
| `PAR-010` | Grades MUST show only published assessments and published report cards. |
| `PAR-011` | Absence request submission with photo attachment MUST work on a 3G connection (compress images client-side to ≤1MB). |
| `PAR-012` | Permission slips (field trips etc.) MUST support a signed digital acknowledgement with timestamp; the school sees a live consent tally. |
| `PAR-013` | Notification preferences MUST be per-category (arrival, departure, payment, grades, announcements, canteen) and per-channel, with a quiet-hours window. |
| `PAR-014` | Default language `id-ID`; `en-US` switchable in Profile, applied without reinstall. |
| `PAR-015` | The app MUST degrade gracefully offline: last-loaded home, attendance and invoice data remain visible with a "last updated" stamp; write actions are disabled with an explanatory state, not a crash. |
| `PAR-016` | Accessibility: minimum touch target 44dp, text scalable to 200%, contrast ≥4.5:1, all icons labelled for screen readers. |
| `PAR-017` | A guardian without `financial_responsible` MUST NOT see amounts owed — the Payments tab is hidden entirely, not shown empty. |
| `PAR-018` | Session on a device MUST survive app restarts for 30 days; biometric unlock optional. |
| `PAR-019` | Cold start to usable Home ≤3s on a mid-range Android device (`NFR-007`). |
| `PAR-020` | No child's photo, name or data may be cached unencrypted on device; use secure storage for tokens and encrypted storage for cached PII. |

## 4. Key screens — acceptance detail

**Home status card.** States: `Sudah di sekolah` (with time), `Belum tiba`,
`Sudah pulang` (with time), `Sakit`, `Izin`, `Tidak hadir`, `Libur`.
Each state has a distinct colour token and an explicit timestamp when applicable.

**Quick pay.** If exactly one invoice is outstanding, the CTA pays it directly.
If multiple, the CTA opens the invoice list with oldest pre-selected.

**Wallet controls.** Daily limit as a stepper with Rp 5,000 increments; category
blocks as toggles; changes confirm with an explicit "applies within 1 minute" note.

## 5. Analytics events (product instrumentation)

`app_open, child_switch, invoice_view, pay_start, pay_method_selected,
pay_intent_created, pay_completed, topup_completed, absence_submitted,
grades_view, report_card_view, notification_opened{category}`

Every event carries `foundation_id, school_id, role` — never PII.

## 6. Acceptance criteria

1. From a cold start with a push notification tap, the app lands on the correct child's attendance detail for the correct date.
2. Paying via QRIS updates the invoice to `PAID` in the app within 30s of settlement without the user pulling to refresh.
3. Turning on airplane mode still renders Home from cache with a visible staleness stamp.
4. A non-financial guardian's build has no Payments tab in the tab bar.
5. Switching to English changes every visible string on every tab.
