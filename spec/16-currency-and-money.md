# 16 — Currency and Money

## 1. Rule

**Every monetary value in EduCore is a `DECIMAL(18,2)` paired with an ISO-4217
currency code.** There are no exceptions, no integer-minor-unit shortcuts, and no
float or `double` columns anywhere in the system.

```python
# apps/core/fields.py
class MoneyField(models.DecimalField):
    def __init__(self, **kw):
        kw.setdefault("max_digits", 18)
        kw.setdefault("decimal_places", 2)
        super().__init__(**kw)
```

| ID | Requirement |
|---|---|
| `CUR-001` | Every monetary column MUST use `core.fields.MoneyField` → `DECIMAL(18,2)`. A CI check MUST fail the build on any `FloatField`, `DoubleField` or bare `DecimalField` used for money. |
| `CUR-002` | Every monetary column MUST be accompanied by a currency: either a `currency` `CHAR(3)` column on the same row, or an unambiguous FK to a row that carries one (e.g. an invoice line inherits its invoice's currency). Denormalise the code onto any row that is queried or summed independently. |
| `CUR-003` | For IDR, the stored scale MUST always be `.00`. Any operation that would produce non-zero minor digits in IDR MUST round to 2dp for storage and MUST NOT leave a residual — see §4. |
| `CUR-004` | **Display formatting is the frontend's job.** The API MUST return `{"amount": "1500000.00", "currency": "IDR"}` as a string, never a pre-formatted or locale-rendered value, and never a float in JSON. |
| `CUR-005` | All arithmetic MUST use Python `Decimal` with an explicit context. `float()` on a money value is a build-breaking lint error. |

## 2. Currency configuration

```
currencies(code CHAR(3) PK, name, symbol, decimal_places TINYINT,
           display_decimals TINYINT, rounding_unit DECIMAL(18,2), active)
```

| Code | Stored scale | `display_decimals` | `rounding_unit` | Notes |
|---|---|---|---|---|
| `IDR` | 2 (always `.00`) | 0 | 100.00 | Frontend shows `Rp 1.500.000`; amounts round to the nearest Rp 100 |
| `USD` | 2 | 2 | 0.01 | `$1,500.00` |
| `SGD` | 2 | 2 | 0.01 | SPK/international schools |
| `MYR` | 2 | 2 | 0.01 | |
| `AUD` | 2 | 2 | 0.01 | |

| ID | Requirement |
|---|---|
| `CUR-006` | `currencies` is reference data seeded by migration and editable only by platform staff. |
| `CUR-007` | Each **school** MUST declare a `base_currency`; each **foundation** MUST declare a `reporting_currency`. Defaults: `IDR` for both. |
| `CUR-008` | A school MAY additionally accept `billing_currencies[]` (e.g. an SPK school billing some families in USD). Every invoice stores exactly one currency for its whole life. |
| `CUR-009` | An invoice, payment, wallet, payroll run or ledger journal MUST NOT mix currencies within itself. Mixing raises `CURRENCY_MISMATCH`. |

## 3. Frontend display contract

| ID | Requirement |
|---|---|
| `CUR-010` | The frontend MUST format from `(amount_string, currency_code)` using `Intl.NumberFormat` with `minimumFractionDigits = maximumFractionDigits = currency.display_decimals`. |
| `CUR-011` | For IDR the UI MUST render `Rp 1.500.000` — Indonesian grouping, **no decimals shown**, even though `1500000.00` is stored. |
| `CUR-012` | For non-IDR the UI MUST render 2 decimals with the currency's locale grouping. |
| `CUR-013` | Money input fields MUST accept local formatting, parse to a plain decimal string, and submit `"1500000.00"`. IDR inputs MUST NOT show or accept a decimal separator. |
| `CUR-014` | Printed artefacts (invoices, receipts, payslips, report cards) MUST show the currency code or symbol explicitly — never a bare number. |
| `CUR-015` | Totals shown to a user MUST be computed server-side and sent as strings. The client MUST NOT sum money values. |

## 4. Rounding

| ID | Requirement |
|---|---|
| `CUR-016` | All rounding MUST be `ROUND_HALF_UP` on `Decimal`, applied once, at the point a value is persisted. |
| `CUR-017` | Percentage-derived amounts (discounts, commission, tax, proration) MUST be computed at full precision and rounded **once** to the currency's stored scale. |
| `CUR-018` | When a total is split into parts (installments, sibling discount allocation, multi-line commission), the **largest-remainder method** MUST be used so the parts sum exactly to the total. Any residual cent/rupiah goes to the first line, deterministically. |
| `CUR-019` | For IDR, invoice totals MUST additionally round to `rounding_unit` (Rp 100) at issue time; the rounding adjustment MUST be an explicit invoice line `PEMBULATAN`, never a silent difference. |
| `CUR-020` | Ledger journals MUST balance exactly to 0.00 per journal per currency — enforced by a check job and a service-layer assertion. |

## 5. Exchange rates (reporting only)

| ID | Requirement |
|---|---|
| `CUR-021` | `fx_rates(base_currency, quote_currency, rate DECIMAL(18,8), effective_date, source)` — daily rates, manually entered or imported by a cron command. |
| `CUR-022` | FX MUST be used **only for consolidated reporting** (spec 03, spec 15). Transactional records are never converted or rewritten. |
| `CUR-023` | Any consolidated figure spanning currencies MUST state the reporting currency and the rate date used, on screen and in every export. |
| `CUR-024` | A missing rate for a required date MUST surface as an explicit gap in the report, never as a silent zero or a stale substitution. |
| `CUR-025` | EduCore MUST NOT perform currency conversion in any transaction flow. A parent billed in USD pays in USD. |

## 6. API shape

```json
{
  "id": "…",
  "number": "INV/SDIT01/2026/000123",
  "currency": "IDR",
  "subtotal": "1500000.00",
  "discount": "150000.00",
  "total": "1350000.00",
  "paid": "0.00",
  "balance_due": "1350000.00"
}
```

| ID | Requirement |
|---|---|
| `CUR-026` | DRF MUST serialise all `MoneyField`s as strings (`coerce_to_string=True`). JSON numbers are forbidden for money. |
| `CUR-027` | Every object containing money MUST expose its `currency` at the top level of that object. |
| `CUR-028` | Aggregate endpoints MUST return either a single currency, or an array of per-currency totals — never an implicit sum across currencies. |

## 7. Migration note

Earlier drafts of this spec used `*_idr bigint` columns. Those are superseded:
every such column becomes `DECIMAL(18,2)` with a sibling `currency` column. Where
this spec set writes `amount`, `total`, `balance`, read it as a `MoneyField`.

## 8. Acceptance criteria

1. A CI grep finds zero `FloatField` / `float(` usages in money paths.
2. `Rp 1.500.000` renders from the stored value `1500000.00` with no decimals visible.
3. Splitting `1000000.00` into 3 installments produces `333400.00 + 333300.00 + 333300.00` and sums exactly to the total.
4. An IDR invoice of `1350050.00` issues as `1350000.00` plus a visible `PEMBULATAN` line of `-50.00`.
5. Attempting to pay a USD invoice with an IDR payment returns `CURRENCY_MISMATCH`.
6. A foundation with IDR and USD schools sees a consolidated dashboard labelled with the reporting currency and rate date.
