# 12 — Hardware, Edge Agent and IoT

## 1. Scope

The physical layer: turnstile gates, RFID/NFC readers, face-recognition cameras,
canteen POS terminals, self-service kiosks, and the edge agent that keeps a
campus running when the internet does not.

Hardware is both the moat and the wedge: once the turnstile is ours, the software renews itself.

## 2. Device classes

| Class | Hardware | Function |
|---|---|---|
| `GATE_READER` | RFID/NFC reader + turnstile controller | Student/staff check-in and check-out |
| `FACE_TERMINAL` | Camera terminal with on-device matching | Face check-in, card fallback |
| `POS_TERMINAL` | Android terminal + NFC + printer | Canteen and campus retail |
| `KIOSK` | Android tablet + printer + NFC | Balance top-up, library slips, visitor badges |
| `HANDHELD` | Rugged Android + NFC | Bus board/alight, roaming attendance |
| `GATEWAY` | Small-form Linux box per campus | Edge agent, local cache, LAN broker |

## 3. Edge agent (runs on `GATEWAY`, on-premise)

The agent is a small Python service shipped from `/edge` in the same repository.
Its local store is a **MySQL 8 instance running on the campus gateway itself** —
the same engine as the cloud, so schema definitions, migrations and query code are
shared rather than maintained twice. One engine across the whole system; no second
database technology anywhere.


| ID | Requirement |
|---|---|
| `HW-001` | The agent MUST maintain a local **MySQL 8** cache of the campus roster, credential UIDs, spend rules, product catalogue and last-known balances. |
| `HW-001b` | The local schema MUST be generated from the same Django models as the cloud (a reduced `edge` settings profile running the same migrations), so a field added centrally cannot silently diverge on the gateway. |
| `HW-001c` | Gateway MySQL MUST be tuned for small-host operation: `innodb_buffer_pool_size` sized to the device (default 512MB), `innodb_flush_log_at_trx_commit=1` for durability on power loss, binary logging **off**, and a single local user bound to `127.0.0.1` — never exposed on the campus LAN. |
| `HW-001d` | The gateway MUST have a UPS and MUST shut MySQL down cleanly on power loss; the agent MUST run `innodb` crash-recovery verification on boot and report the result in its heartbeat. |
| `HW-002` | The agent MUST authorise gate scans locally in ≤300ms without any cloud round-trip. |
| `HW-003` | The agent MUST buffer all events durably on disk and replay them in order on reconnect, preserving original `occurred_at`. |
| `HW-004` | Buffer capacity MUST be at least 72 hours of campus events; on overflow the agent alerts and drops the oldest **non-financial** events first — financial events are never dropped. Sizing assumption: ≥8GB free disk on the gateway for MySQL data + binlog-free redo. |
| `HW-004b` | Uploaded events MUST be pruned from the gateway only after the cloud acknowledges them, and never before a local retention floor of 7 days — the local database doubles as the campus's forensic record. |
| `HW-005` | Upload MUST be batched (default 50 events / 5 seconds), gzip-compressed, and idempotent on a client-generated event UUID. The server writes batches to a staging table; the `ingest_device_events` cron command (every minute) applies them to domain tables. |
| `HW-005b` | Money sent by a terminal MUST be a decimal string at 2dp with an explicit currency; a numeric-typed money field is rejected with `INVALID_MONEY_FORMAT`. |
| `HW-006` | The agent MUST pull incremental sync deltas via a cursor and apply them atomically. |
| `HW-007` | The agent MUST self-report health every 60s: version, uptime, queue depth, last sync, device status list, disk free, **local MySQL status (up/down, data size, last crash-recovery result)**. |
| `HW-008` | Over-the-air update MUST be staged: download → verify signature → apply on next idle window → auto-rollback on failed health check. |
| `HW-009` | Device-to-cloud auth MUST use per-device mTLS certificates issued at provisioning; a revoked device MUST be rejected at TLS handshake. |
| `HW-010` | The agent MUST NOT store raw biometric images; only encrypted templates, and only for students with recorded consent. |

### 3.1 Gateway provisioning image

The gateway ships as a prepared image containing: OS, MySQL 8, the Python agent,
the `edge` Django settings profile, and a systemd unit ordering the agent after
`mysqld`. Field install is: power on → scan QR → the agent runs migrations against
local MySQL → initial full sync → device appears online in the console. No manual
database setup at the school.

## 4. Device lifecycle

| ID | Requirement |
|---|---|
| `HW-011` | Provisioning MUST be a single flow: scan device QR → assign to school + location + direction → issue certificate → device appears online in the console. |
| `HW-012` | Device console MUST show per-device: status (online/offline/degraded), last heartbeat, firmware version, today's event count, queue depth. |
| `HW-013` | A device offline for more than `offline_alert_minutes` (default 15) during school hours MUST alert the school admin and the EduCore support channel. |
| `HW-014` | Remote actions MUST include: reboot, force sync, clear queue (with confirmation), unlock gate (audited), and retire device. |
| `HW-015` | Retiring a device MUST revoke its certificate immediately and preserve its historical events. |

## 5. Credentials (cards)

| ID | Requirement |
|---|---|
| `HW-016` | Cards MUST be MIFARE DESFire EV2/EV3 or equivalent with mutual authentication. **Plain UID-only cards MUST NOT be used for payment authorisation**, because UIDs are trivially cloned. |
| `HW-017` | Card issuance MUST bind card UID ↔ student, print/encode, and record issuance in `credentials`. |
| `HW-018` | Lost-card flow: revoke in one action → all devices reject within 60s online / at next sync offline → issue replacement, optionally with a replacement fee posted to the student's invoice. |
| `HW-019` | A student MUST be able to hold at most one active card; issuing a new one auto-revokes the previous. |
| `HW-020` | QR-based fallback credentials MUST be time-limited (≤15 minutes) and single-use. |

## 6. Face recognition constraints

| ID | Requirement |
|---|---|
| `HW-021` | Matching MUST run on-device; templates never leave the campus in raw form. |
| `HW-022` | Liveness detection MUST be enabled; a printed-photo presentation MUST fail. |
| `HW-023` | Below-threshold matches MUST fall back to card, never auto-accept (`ATT-010`). |
| `HW-024` | Consent, retention period and deletion-on-exit MUST be enforced programmatically (see 14 §4). |

## 7. API (device-facing)

```
POST /device/enroll            {provisioning_token, hardware_id} -> {device_id, cert}
POST /device/heartbeat         {device_id, metrics}
GET  /device/sync?cursor       -> {roster_delta, credentials_delta, rules_delta, catalog_delta, next_cursor}
POST /device/events            {batch:[{uuid, type, payload, occurred_at}]}  (idempotent)
GET  /device/commands          -> pending remote actions
POST /device/commands/:id/ack  {result}
```

## 8. Deployment standard (per campus)

Documented for the field team, and encoded as an install checklist in the console:
main gate readers (in/out), one gateway on wired LAN with UPS, POS per canteen
till, one kiosk near the office, spare card stock ≥5% of roster. Site survey MUST
record network topology, power, and mounting heights before install sign-off.

## 9. Acceptance criteria

1. Pulling the gateway's WAN cable for 3 hours during a school day loses zero gate or POS events.
2. A revoked card is rejected at an online gate within 60 seconds.
3. A printed photo of a student fails liveness at a `FACE_TERMINAL`.
4. A device with a revoked certificate cannot complete a TLS handshake.
5. Replaying an event batch three times produces one set of records.
6. Cutting power to a gateway mid-transaction and restoring it leaves local MySQL consistent, the agent running, and no buffered events lost.
7. Adding a field to a synced Django model and deploying produces the same column on the gateway after its next update, with no hand-written edge migration.
