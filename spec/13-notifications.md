# 13 — Notifications and Messaging

## 1. Scope

Every outbound message: WhatsApp, push, SMS, email, in-app. This module is a
shared service — other modules publish intents, never call providers directly.

## 2. Architecture

Notifications are a Django app. Sending is never synchronous inside a request:
callers write a `notification_intents` row, and the `send_due_notifications` cron
command (every 15 min, plus `drain_tasks` every minute for `high`/`critical`
priority) resolves preferences, renders and dispatches. There is no queue server.


```
module -> notification.dispatch(intent) -> preference resolution -> channel selection
       -> template render (id-ID/en-US) -> provider adapter -> delivery receipt -> audit
```

```
notification_templates(id, key, channel, locale, subject?, body, variables[], version, active)
notification_intents(id, foundation_id, school_id, recipient_user_id, category, key,
                     payload jsonb, priority, scheduled_for, status, dedupe_key)
notification_deliveries(id, intent_id, channel, provider, provider_message_id,
                        status, error_code?, sent_at, delivered_at, read_at, cost)
notification_preferences(id, user_id, category, channels[], quiet_hours_start, quiet_hours_end, enabled)
```

## 3. Categories and defaults

| Category | Default channels | Priority | Quiet hours respected |
|---|---|---|---|
| `ARRIVAL` / `DEPARTURE` | push, whatsapp | high | no (time-critical, but inside school hours anyway) |
| `EMERGENCY` (clinic, safeguarding, bus alert) | whatsapp, push, sms | critical | **no** |
| `PAYMENT_DUE` / `PAYMENT_RECEIVED` | whatsapp, push | normal | yes |
| `GRADE_PUBLISHED` / `REPORT_CARD` | push | normal | yes |
| `HOMEWORK` | push | low | yes |
| `BEHAVIOUR_MAJOR` | whatsapp, push | high | no |
| `BEHAVIOUR_MINOR` | digest | low | yes |
| `CANTEEN` | digest | low | yes |
| `ANNOUNCEMENT` | push, in-app | normal | yes |

## 4. Requirements

| ID | Requirement |
|---|---|
| `NTF-001` | Channel selection MUST follow: user preference → category default → fallback ladder (whatsapp → push → sms → email). |
| `NTF-002` | Quiet hours (default 21:00–06:00 local) MUST defer non-critical messages to the next allowed window; `EMERGENCY` MUST always send immediately. |
| `NTF-003` | `dedupe_key` MUST prevent duplicate sends for the same logical event (e.g. one arrival notice per student per day per gate debounce). |
| `NTF-004` | Scheduled notifications MUST re-evaluate their condition **at send time** — a paid invoice cancels its pending reminder (`FIN-027`). |
| `NTF-005` | WhatsApp MUST use approved message templates; template keys, variable order and approval status MUST be tracked in `notification_templates`. |
| `NTF-006` | Failure of a WhatsApp template send (undeliverable, opt-out, template rejected) MUST fall through to the next channel within 60 seconds. |
| `NTF-007` | Digests MUST be aggregated per recipient per category and sent at a school-configured hour (default 17:00). |
| `NTF-008` | All templates MUST exist in `id-ID`; `en-US` is optional and falls back to `id-ID`. |
| `NTF-009` | Message bodies MUST NOT contain sensitive detail beyond what the channel warrants: no grades, amounts owed, or health detail in SMS — SMS says "open the app". |
| `NTF-010` | Delivery receipts MUST be stored; a school-admin view MUST show per-message status for support ("did the parent get it?"). |
| `NTF-011` | Per-channel cost MUST be recorded as `DECIMAL(18,2)` + `currency` (provider billing currency) for margin analysis. |
| `NTF-012` | Rate limits: max 20 notifications per recipient per day (excluding `EMERGENCY`); excess is collapsed into a digest. |
| `NTF-013` | Opt-out MUST be honoured per category; `EMERGENCY` and statutory financial notices MUST NOT be opt-outable. |
| `NTF-014` | Broadcast announcements MUST support targeting by school, grade, class, or role, with a recipient count preview before send and a mandatory confirmation. |
| `NTF-014b` | `EMERGENCY` and `high` priority intents MUST be picked up by `drain_tasks` within one minute; `normal`/`low` may wait for the 15-minute tick. |
| `NTF-015` | A failed provider MUST trip a circuit breaker and route to the fallback channel rather than queue indefinitely. |

## 5. API

```
POST /notifications/broadcast     {target, category, title, body, attachments[], schedule_at?}
GET  /notifications/broadcast/:id  -> {recipients, delivered, read, failed}
GET  /me/notifications?cursor
POST /me/notifications/:id/read
GET/PUT /me/notification-preferences
GET  /admin/notifications/deliveries?student_id&from&to&category
POST /webhooks/whatsapp/status    (provider receipts)
```

## 6. Acceptance criteria

1. A gate scan produces exactly one arrival notification per guardian, dispatched in <5s.
2. An invoice paid at 09:00 has its 09:30 reminder cancelled at send-time evaluation.
3. With WhatsApp provider down, a payment reminder arrives via push within 60s.
4. A parent who opted out of `HOMEWORK` still receives `EMERGENCY` messages.
5. Broadcasting to "Grade 7, School A" shows the exact recipient count before sending and matches the delivery count afterwards.
