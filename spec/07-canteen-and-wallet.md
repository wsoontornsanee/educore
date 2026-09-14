# 07 — Canteen, Campus Wallet and POS

## 1. Scope

Stored-value student wallet, canteen/uniform/stationery POS, parental spending
controls, nutrition visibility, merchant settlement, and the 1–2% campus take-rate.

## 2. Entities

```
wallets(id, student_id, balance, status[ACTIVE|FROZEN|CLOSED], daily_limit, updated_at)
wallet_transactions(id, wallet_id, type[TOPUP|PURCHASE|REFUND|ADJUSTMENT|TRANSFER_OUT],
                    amount, balance_after, reference, pos_transaction_id?,
                    device_id?, occurred_at, status, idempotency_key)
merchants(id, school_id, name, type[CANTEEN|UNIFORM|STATIONERY|OTHER], settlement_account, commission_bps)
products(id, merchant_id, sku, name, price, category, nutrition jsonb, allergens[], active)
pos_terminals(id, merchant_id, device_id, name, status)
pos_transactions(id, merchant_id, terminal_id, student_id, items jsonb, subtotal,
                 total, commission, occurred_at, status, offline_created)
spend_rules(id, student_id, daily_limit, blocked_categories[], blocked_products[], allowed_window)
merchant_settlements(id, merchant_id, period, gross, commission, net, status, paid_at)
```

### 2.1 Currency

`wallets`, `wallet_transactions`, `products`, `pos_transactions` and
`merchant_settlements` carry `currency CHAR(3)`, defaulting to the school's
`base_currency`. A wallet holds exactly one currency for its lifetime; a purchase
in another currency raises `CURRENCY_MISMATCH`. All amounts `DECIMAL(18,2)`.

## 3. Wallet

| ID | Requirement |
|---|---|
| `WAL-001` | Wallet balance MUST be derived-and-cached: `balance` is maintained transactionally alongside every `wallet_transactions` insert, in the same DB transaction. |
| `WAL-002` | The `reconcile_wallet_balances` cron command (nightly, 19:00) MUST recompute every balance from the transaction log and alert on any mismatch. Mismatches are incidents, not warnings. |
| `WAL-003` | Every wallet mutation MUST carry an `idempotency_key`; replays return the original result. |
| `WAL-004` | Balance MUST NEVER go negative unless `allow_overdraft` is explicitly enabled for the school with a cap. |
| `WAL-005` | Top-up methods: VA, QRIS, cash at school office, and auto-top-up (threshold + amount) where the guardian has opted in. |
| `WAL-006` | Auto-top-up MUST require explicit opt-in, show the next charge, and be cancellable from the app in one screen. |
| `WAL-007` | Wallet MUST be frozen automatically when the student status leaves `ACTIVE`. |
| `WAL-008` | Guardians MUST see a full, itemised transaction history with merchant, time, items and running balance. |

## 4. Spending controls

| ID | Requirement |
|---|---|
| `WAL-009` | Guardian MUST be able to set a daily spend limit; the POS MUST enforce it at authorisation time. |
| `WAL-010` | Guardian MUST be able to block categories (e.g. `SUGARY_DRINKS`) and specific products; blocked items are rejected at POS with a clear on-screen reason. |
| `WAL-011` | Optional time windows (e.g. purchases allowed only 09:30–10:00 and 12:00–12:45). |
| `WAL-012` | Limit changes take effect at the terminal's next 60-second sync poll (`ARC-016`) on online terminals and at next sync on offline terminals; the terminal MUST display its rule freshness. |
| `WAL-013` | A rejected transaction MUST be logged with the reason and visible to the guardian. |

## 5. POS — offline first

| ID | Requirement |
|---|---|
| `WAL-014` | The POS terminal MUST operate fully offline for at least 8 hours, using a locally cached roster, product catalogue, spend rules and last-known balances. |
| `WAL-015` | Offline transactions MUST be queued with a client-generated UUID and `offline_created=true`, and synced on reconnect. |
| `WAL-016` | Offline spend MUST be capped by `offline_floor_limit` per student per day (default Rp 50,000) to bound risk. |
| `WAL-017` | Sync conflict (offline spend exceeded the real balance) MUST be resolved by accepting the transaction and creating a negative balance flagged `RECONCILE_REQUIRED`, notifying the guardian — **never** by silently voiding a purchase a child already consumed. |
| `WAL-018` | Checkout MUST complete in ≤3 seconds from card tap to receipt, offline or online. |
| `WAL-019` | The terminal UI MUST show student photo + name + balance on tap, before items are added, so the operator can catch a wrong card. |
| `WAL-020` | Terminal MUST print or display a receipt and MUST push a purchase notification to guardians if enabled (default: daily digest, not per-item). |

## 6. Merchants and settlement

| ID | Requirement |
|---|---|
| `WAL-021` | Commission MUST be computed per transaction at `merchant.commission_bps` and stored on the transaction — never recomputed from a current rate at settlement time. |
| `WAL-022` | Settlement runs per merchant per period (default weekly): gross, commission, net; produces a statement PDF. |
| `WAL-023` | A merchant MUST be able to see their own sales only — enforced by scope, same rules as 02 §4. |
| `WAL-024` | Product catalogue MUST support nutrition fields (calories, sugar_g, allergens) and a school-level "healthy" tag used in parent reporting. |

## 7. Refunds and closure

| ID | Requirement |
|---|---|
| `WAL-025` | Operator-initiated void within `void_window_minutes` (default 15) reverses the purchase and restores balance. |
| `WAL-026` | On student exit (`GRADUATED`/`TRANSFERRED_OUT`), residual balance MUST enter a refund queue with guardian bank details; balances below `Rp 10,000` MAY be donated to the school only with explicit guardian consent. |
| `WAL-027` | Unclaimed balances MUST remain the guardian's liability on the books indefinitely — never auto-forfeited. |

## 8. API

```
GET  /wallets/:student_id | GET /wallets/:student_id/transactions?from&to
POST /wallets/:student_id/topup            {amount, method} -> payment intent
POST /wallets/:student_id/auto-topup       {enabled, threshold, amount}
PUT  /wallets/:student_id/rules            {daily_limit, blocked_categories[], allowed_window}
POST /pos/sessions                          (terminal auth) -> {roster, catalog, rules, cursor}
GET  /pos/sync?cursor                       -> incremental roster/rule/balance deltas
POST /pos/transactions                      (batch, idempotent) {transactions:[...]}
POST /pos/transactions/:id/void             {reason}
GET  /merchants/:id/sales?from&to
POST /merchants/:id/settlements/run         {period}
GET  /students/:id/nutrition-summary?from&to
```

## 9. Acceptance criteria

1. A terminal disconnected for 6 hours records 300 purchases and syncs all 300 with no duplicates and correct original timestamps.
2. Replaying the same POS batch twice changes no balance.
3. A student with a Rp 20,000 daily limit is rejected on the transaction that would reach Rp 20,001, with the reason shown on the terminal.
4. Nightly integrity job on 40,000 wallets completes in under 10 minutes and reports zero drift.
5. A voided purchase 10 minutes after sale restores the exact balance and appears as a `REFUND` line to the guardian.
