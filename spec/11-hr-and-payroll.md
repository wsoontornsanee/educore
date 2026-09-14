# 11 — HR and Payroll (PPh 21 TER / BPJS)

## 1. Scope

Staff records, contracts, leave, attendance for staff, salary structures, payroll
runs with Indonesian statutory calculations, and payslip distribution.

**Warning to the implementer:** statutory rates change. Every rate, threshold and
bracket in this module MUST be stored as effective-dated configuration data,
never hardcoded in application logic.

## 2. Entities

```
staff_contracts(id, staff_id, type[PERMANENT|CONTRACT|HOURLY], start_date, end_date?,
                position, department, base_salary, ptkp_status, npwp?, bank_account)
salary_components(id, school_id, code, name, type[EARNING|DEDUCTION],
                  calc[FIXED|PERCENT_OF_BASE|PER_HOUR|FORMULA], taxable, bpjs_base, active)
staff_component_assignments(id, staff_id, component_id, amount_or_rate, effective_from, effective_to)
staff_attendance(id, staff_id, date, check_in, check_out, status, source)
leave_types(id, school_id, code, name, annual_quota_days, paid, requires_approval)
leave_requests(id, staff_id, leave_type_id, date_from, date_to, days, reason, status, approved_by)
payroll_runs(id, school_id, period, status, gross, deduction, net, approved_by, paid_at)
payslips(id, run_id, staff_id, lines jsonb, gross, pph21, bpjs_kes, bpjs_tk, net, pdf_key)
tax_config(id, effective_from, ter_tables jsonb, ptkp_values jsonb, brackets jsonb, notes)
bpjs_config(id, effective_from, kesehatan jsonb, ketenagakerjaan jsonb)
```

### 2.1 Currency

Salary, components, payslips and payroll runs carry `currency CHAR(3)`, defaulting
to the school's `base_currency`. One payroll run is single-currency; a school
paying some staff in another currency runs a separate run for them. Statutory
PPh 21 and BPJS calculations apply to IDR runs only — a non-IDR run MUST skip them
and flag `STATUTORY_NOT_APPLICABLE` on the payslip. All amounts `DECIMAL(18,2)`.

## 3. Payroll requirements

| ID | Requirement |
|---|---|
| `HR-001` | Payroll run status machine: `DRAFT → CALCULATED → PENDING_APPROVAL → APPROVED → PAID`. Only `APPROVED` runs may be paid; only `foundation_admin` may approve (see 03 §3). |
| `HR-002` | A `CALCULATED` run MUST be fully recalculable and diffable against the previous period, showing every staff member whose net pay changed and by how much. |
| `HR-002b` | Payroll calculation MUST run as the `calculate_payroll` management command (or its synchronous service call for small runs) — never on a worker queue. |
| `HR-003` | Gross = base salary + earning components (fixed, percent-of-base, per-hour × hours, formula), prorated for mid-period joiners/leavers by calendar days. |
| `HR-004` | **PPh 21 MUST use the monthly TER (Tarif Efektif Rata-rata) method for periods Jan–Nov and the annual progressive recalculation in December**, per the current regulation. Rates come from `tax_config`, selected by `effective_from ≤ period`. |
| `HR-005` | TER category MUST be derived from the employee's PTKP status (TER A/B/C mapping stored in config, not code). |
| `HR-006` | December recalculation MUST reconcile the year's withheld tax against the annual liability and produce an adjustment line (over- or under-withheld). |
| `HR-007` | BPJS Kesehatan and BPJS Ketenagakerjaan (JHT, JP, JKK, JKM) MUST be split into employer and employee portions, each with its own base cap, from `bpjs_config`. |
| `HR-008` | Employer-borne contributions MUST appear in the school's cost figure and ledger but MUST NOT reduce the employee's net pay. |
| `HR-009` | Every payslip line MUST be explainable: hovering/tapping a figure shows the formula, inputs and config version used. |
| `HR-010` | Payslips MUST be generated as password-protected PDFs (default password policy configurable) and distributed by email or in-app, never in a shared folder. |
| `HR-011` | A payroll run MUST post to the ledger: `Dr Salary Expense, Dr Employer Contributions / Cr Payroll Payable, Cr Tax Payable, Cr BPJS Payable`. |
| `HR-012` | Bank disbursement file export MUST be supported per major bank format (CSV/TXT templates, configurable). |
| `HR-013` | Bukti Potong (withholding certificate) data MUST be exportable annually per employee. |
| `HR-014` | Retroactive salary changes MUST be handled as explicit back-pay components in the current period, never by editing a closed run. |
| `HR-015` | A closed payroll period MUST be immutable; corrections post to the next open period. |

## 4. Leave and staff attendance

| ID | Requirement |
|---|---|
| `HR-016` | Leave balance MUST be computed as quota − approved − pending, and shown at request time. |
| `HR-017` | Overlapping leave requests for the same staff member MUST be rejected. |
| `HR-018` | Unpaid leave MUST automatically deduct in the payroll run at a configured daily rate. |
| `HR-019` | Staff may check in with the same gate hardware as students (card tap); staff attendance MUST feed hourly/deduction calculations where the contract is `HOURLY`. |
| `HR-020` | Leave approval MUST respect an approval chain: department head → school admin, configurable per school. |

## 5. API

```
GET/POST /staff-contracts | PATCH /staff-contracts/:id
GET/POST /salary-components | POST /staff/:id/components
GET/POST /leave-types | POST /leave-requests | POST /leave-requests/:id/decide
GET  /staff/:id/leave-balance
POST /payroll/runs                  {school_id, period} -> DRAFT
POST /payroll/runs/:id/calculate    -> CALCULATED + diff vs prior period
GET  /payroll/runs/:id/diff
POST /payroll/runs/:id/submit | /approve | /mark-paid
GET  /payroll/runs/:id/payslips | GET /payslips/:id/pdf
GET  /payroll/runs/:id/bank-file?format=bca|mandiri|bri
GET  /payroll/tax-config?effective_on
```

## 6. Acceptance criteria

1. Recalculating a run twice with unchanged inputs produces byte-identical figures.
2. A staff member joining on the 16th of a 30-day month receives exactly 15/30 of base salary.
3. December run for a full-year employee produces an adjustment line equal to (annual liability − sum of Jan–Nov withheld).
4. Changing a TER rate in `tax_config` with a future `effective_from` does not alter any already-calculated run.
5. An approved run cannot be edited; attempting to do so returns `PAYROLL_PERIOD_LOCKED`.
6. Payslip PDF opens only with the configured password.
