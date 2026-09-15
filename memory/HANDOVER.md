# Handover — 2026-09-15

Session paused (context limit). This is the entry point for whoever picks this up next.

## Read first, in this order
1. `CLAUDE.md` / `AGENTS.md` / `USER.md` — the 5-phase SOP (Plan → Dev → Test → PR → wait for user merge), red lines, engineering protocols. Non-negotiable, follow exactly.
2. `memory/00_CORE.md` — permanent architectural constraints (if present).
3. `memory/01_PROJECT.md` — full task history (TASK-001 through TASK-057), the numbered retrospectives section (51 entries) explaining every non-obvious design decision made this session. Read the retrospectives before touching an area they cover — they exist specifically so you don't re-derive or contradict a decision that was already reasoned through.
4. This file, for what's immediately actionable next.

## State as of this handover
- `main` is green: 468/468 tests passing, `makemigrations --check` clean.
- Nothing is mid-flight: no open feature branches, no Notion page stuck "In progress," no unresolved PR.
- The last 18 tasks this session (TASK-040 through TASK-057) all followed the same loop: pull main → verify baseline → pick one task → log a Notion Plan page (search first, never duplicate) → branch → implement with tests → full suite + `makemigrations --check` → update `memory/01_PROJECT.md` (task table row + a numbered retrospective entry explaining the *why*) → commit/push → PR → Notion Plan page to Done → **wait for the user's explicit "merged" before continuing**. Keep following this exactly.

## Standing instructions from the user (apply to every future task)
- Before creating or updating a Notion task, search first — never create duplicates.
- Log every deferred non-goal as its own `[Open Item]` Notion page immediately, not just in PR prose.
- Never merge or deploy unilaterally — always wait for the user to say a PR is merged before starting the next task.
- When a citation (spec section, requirement ID) is inherited from earlier work rather than freshly read, verify it against the actual `spec/*.md` file before reusing it (see retrospective #43 — a fabricated `REC-0xx`/"spec/17" citation chain went unchecked for 5 tasks before being caught).

## Known guardrails — don't cross these without asking the user first
- **A separate task stream owns finance/HR/dashboard work** under its own `TASK-025` to `TASK-036` numbering and pre-assigned branch names (`feat/step-7.x-*`, `feat/step-8.x-*`, `feat/step-12.1-payroll-and-tax`, `feat/step-9.1-foundation-dashboard`, `feat/step-13.1-dapodik-emis-export`, `feat/step-14.1-sso-integrations`, etc.) — visible in the Notion tracker, all still `Todo`. Don't pick these up; they're someone else's in-progress numbering, already collided with once this session (see retrospective early in the session).
  - `rpt_ar_aging` (the 6th spec/15 reporting table) is deliberately left unbuilt for this exact reason — it shares its name with that stream's planned "AR Aging Reports & Bad Debt Write-Off Workflow."
- **UI/mobile-client work has no client to build against** in this repo (backend-only monolith, no frontend/mobile code checked in here). Every `[Open Item]` that says "UI" or "mobile" in its title is blocked for that reason, not abandoned — see the many "split into a backend half (built) + a UI half (deferred)" retrospectives (TASK-042/044/045/047/049 etc.) for the established pattern of how to handle this: build the backend contract, split the UI half into its own narrower Open Item.
- **Some Open Decisions are real research questions you can resolve** (see TASK-055's Xendit/Midtrans research via `WebSearch`, and TASK-057's discovery that the "cron host topology" decision was already answered in spec and just unimplemented) — check the actual spec text and, if needed, real-world facts before assuming a Notion "Open Decision" is unresolvable. Others are genuine business/legal/product calls (curriculum scope, report card template sign-off, canteen escrow legal entity, biometric vs RFID, arrears withholding policy) that need the user, not research.
- **Don't self-initiate a new architectural subsystem or a new domain** (e.g. a new Django app, a new milestone's worth of work like Behaviour Points) without asking first — see the `AskUserQuestion` calls before TASK-050 (reporting subsystem) and before picking a task once the narrow-slice backlog ran dry. Routine "next narrow slice" work doesn't need this; a new app/domain does.

## What's actually left in the Notion backlog right now
Query `collection://755347a6-6594-8379-97bb-877f515199e1` (data source "Astra Educore") for `Status = 'Todo'`. As of this handover, `backlog/open-items` and `backlog/open-decisions` contain:
- Genuinely open, backend-buildable: nothing obvious remains — the narrow-slice backend backlog was exhausted around TASK-054/057. Re-check with a fresh query; new items may have been added since.
- UI/mobile-blocked (leave alone unless a client shows up): Gradebook Grid UI, Teacher Web Gradebook Grid UX, Teacher Mobile Agenda Surface, Exam Client-Side Lockdown, Gradebook Merge-Prompt UI, Homework Teacher Completion-Bar UI, Guardian & Parent Mobile App Authorization Filtering.
- Other stream's territory (leave alone): `rpt_ar_aging`, and everything under the `feat/step-7.x` through `feat/step-14.x` branch names.
- Needs a real product/business decision from the user, not code: Report Card Template & Signed Sample, Canteen Escrow Legal Entity, Biometric vs RFID, Academic Curriculum Scope, Arrears/Rapor-Withholding Policy, Multi-Currency Billing Priority, Payment Gateway Convenience Fee Allocation.
- Bigger, deliberately-not-self-initiated scope: Behaviour Points System (new P2/Campus Life domain), Academic Narrative AI-Draft Suggestion Engine, Exam 300-Concurrent-Student Load Testing.
- Wallet Auto-Top-up / Saved Payment Instrument (WAL-006): structurally blocked — no tokenized/saved-payment-instrument system exists anywhere in this codebase (confirmed, not assumed).

**Recommended next move**: re-run the Todo query fresh (the list above may be stale by the time this is read), and if nothing new and unblocked has appeared, ask the user directly (`AskUserQuestion`) rather than guessing — that's exactly what happened last time the backlog ran dry, and it worked well (the user picked "resolve an Open Decision," which led to two solid, well-scoped tasks).

## Useful specifics for the next agent
- Test command: `EDUCORE_USE_SQLITE=1 python3 manage.py test` (MySQL is the real target per spec, SQLite is the local/CI fallback env var).
- `python3` not `python` on this machine — `python` isn't on PATH.
- Notion data source: `collection://755347a6-6594-8379-97bb-877f515199e1` ("Astra Educore"). Row properties: Name, Status (Todo/In progress/Done), Branch, Logs, Updated At.
- weasyprint PDF generation warnings in test output are expected/harmless (native libs unavailable in this dev environment; the code has an HTML fallback — see retrospective on report cards).
