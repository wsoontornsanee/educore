# 18 — Partner & Vendor Integration API

## 1. Scope

A public, partner-facing REST API surface for 3rd-party vendors and
integrators — not scoped to any single domain. Payroll acknowledgement is
the first known consumer (see `[Open Item] Payroll Run Approvals FND-007` —
no in-house payroll module exists; `apps/foundation/approvals.py`'s
`APPROVAL_TYPE_PAYROLL` branch is a deliberate no-op pending this surface),
but the API itself covers four domains from day one: payroll, finance,
roster, and attendance. This spec documents the surface a future
implementation task builds against; it does not itself change any backend
code.

| Req ID | Requirement |
|---|---|
| `PVA-001` | The API MUST expose only four domains at launch: `payroll`, `finance`, `roster`, `attendance`. No other internal domain (academic, wallet, campus) is exposed. |
| `PVA-002` | Every credential is scoped to exactly one foundation. A partner integration never spans foundations on one key. |

## 2. Authentication

Key-id/secret pair issued per foundation. Every request carries the key-id
in a header and an HMAC-SHA256 signature computed over
`timestamp + method + path + body`; the secret itself is never transmitted.

```
X-EduCore-Key-Id:    ak_live_7Qp2R9
X-EduCore-Signature: t=1789632650,v1=9f...
Idempotency-Key:     4f0a1b2c-...
```

| Req ID | Requirement |
|---|---|
| `PVA-010` | A request whose `t=` timestamp is more than 300 seconds from server time MUST be rejected (`401 SIGNATURE_INVALID`) — bounds the replay window. |
| `PVA-011` | Scope (which of the four domains, and which schools within the foundation) is set by the foundation at key-issue time, per domain and per school. A partner cannot self-extend scope. |
| `PVA-012` | Key rotation supports at most 2 simultaneously active keys per foundation, for a maximum of 30 days. The old key becomes **read-only** on day 23 of the overlap (a 7-day write freeze before full expiry), then stops working entirely at day 30. |
| `PVA-013` | An IP allow-list is optional per key, except it is REQUIRED for any key holding the `payroll.write` scope. |

## 3. Versioning and deprecation

| Req ID | Requirement |
|---|---|
| `PVA-020` | The API version lives in the URL path (`/api/v1/...`), never in a header. |
| `PVA-021` | A breaking change always ships as a new path version (`/api/v2/...`). Adding an optional field to a response is NOT a breaking change and ships in place. |
| `PVA-022` | A superseded version stays live for 12 months after its successor ships. From the day the successor ships, every response on the old version carries a `Sunset` header. |

## 4. Conventions

| Rule | Shape |
|---|---|
| Pagination | `?cursor=&limit=` request → `{results: [...], next_cursor}` response |
| Money | `{"amount": "1500000.00", "currency": "IDR"}` — amount is always a 2dp string, currency at the same object level, matching `core.fields.MoneyField`'s `DECIMAL(18,2)` convention. An aggregate endpoint never sums across currencies: it returns an array of one total per currency, never a single number. |
| Time | ISO 8601, UTC (e.g. `2026-09-17T02:14:00Z`) |
| Idempotency | Every mutating endpoint accepts an `Idempotency-Key` header. A replay with the same key returns the original response unchanged, never a duplicate effect. Keys are retained 7 days. |

## 5. Errors

All errors are RFC 9457 `application/problem+json`. Integrators branch on
`code` (a stable SCREAMING_SNAKE_CASE string), never on `detail` (free text).

```json
{
  "type": "/errors/scope-denied",
  "title": "Scope not granted",
  "status": 403,
  "detail": "Key lacks payroll.write",
  "instance": "/api/v1/partner/_",
  "code": "SCOPE_DENIED"
}
```

| Status | `code` |
|---|---|
| 401 | `SIGNATURE_INVALID` |
| 403 | `SCOPE_DENIED` |
| 409 | `IDEMPOTENCY_MISMATCH` |
| 409 | `PAYROLL_RUN_LOCKED` |
| 422 | `CURRENCY_MISMATCH` |
| 429 | `RATE_LIMITED` |

## 6. Webhooks and polling

Webhook is the primary channel. A partner registers exactly one HTTPS
endpoint per environment, receives a `POST` signed the same way as outgoing
requests, and must reply `2xx` within 5 seconds. A failed delivery is
retried 5 times with exponential backoff over up to 6 hours; after that the
event remains available via polling (`GET /partner/events?since=`) so
nothing is ever silently lost.

| Event | Fires when |
|---|---|
| `payroll.run.approved` | Foundation approves a payroll run — line-item detail ready to be pulled |
| `finance.payment.settled` | A VA/QRIS payment settles and is allocated |
| `roster.student.enrolled` | A new active student appears within the key's scope |
| `integration.key.rotated` | A key was rotated — fires 30 days before the superseded key dies |

## 7. Endpoints

```
GET  /partner/foundations                          -- roster.read
GET  /partner/schools                               -- roster.read
GET  /partner/staff?school_id                       -- roster.read
GET  /partner/payroll/runs?period                   -- payroll.read
GET  /partner/payroll/runs/:id/lines                -- payroll.read
POST /partner/payroll/runs/:id/acknowledge          -- payroll.write
GET  /partner/invoices?status&school_id             -- finance.read
GET  /partner/attendance/daily?date                 -- attendance.read
GET  /partner/events?since=<cursor>                 -- any scope (webhook fallback)
POST /partner/webhooks                              -- any scope (register receiving endpoint)
```

| Req ID | Requirement |
|---|---|
| `PVA-030` | `GET /partner/staff` returns only `NIK`/`NISN` fields when the key additionally holds `roster.pii` — approved separately by the foundation, never implied by plain `roster.read`. |
| `PVA-031` | `GET /partner/attendance/daily` returns the six existing `AttendanceStatus` values (`HADIR`, `TERLAMBAT`, `SAKIT`, `IZIN`, `ALPA`, `DISPEN`); gate photos are never exposed through this API. |
| `PVA-032` | `POST /partner/payroll/runs/:id/acknowledge` requires `payroll.write` and is rejected `409 PAYROLL_RUN_LOCKED` once a run has already moved past the acknowledgement window (exact state model is an implementation-task decision — no payroll domain model exists yet to define it against). |

## 8. Rate limits

| Limit | Value |
|---|---|
| Requests per key | 600 / minute |
| Rows per cursor page | 200 max |
| Fastest allowed polling interval | 30 seconds |

Exceeding any limit returns `429 RATE_LIMITED` with a `Retry-After` header.
A large export MUST use an async job endpoint, never an unbounded
paginate-to-the-end loop (mirrors the existing `POST /foundation/exports`
pattern in spec/03 §5).

## 9. Acceptance criteria

1. A signed request with a timestamp 301 seconds old is rejected with `401 SIGNATURE_INVALID` before any scope or business logic runs.
2. A key holding only `finance.read` gets `403 SCOPE_DENIED` calling `POST /partner/payroll/runs/:id/acknowledge`.
3. Two calls to the same mutating endpoint with the same `Idempotency-Key` return byte-identical responses and produce exactly one side effect.
4. A webhook endpoint that 5xxs on every attempt is retried 5 times over 6 hours, then the event is still retrievable via `GET /partner/events?since=`.
5. A key rotated on day 0 can still make read calls on day 25 but is rejected on any write call from day 23 onward.
