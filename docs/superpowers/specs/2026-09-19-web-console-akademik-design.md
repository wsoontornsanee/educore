# Web Console: Akademik module (design)

Notion: "Web Console: Build Akademik module (Siswa & kelas, Jadwal, Antrean penilaian, Rapor)".
Follows PR #194 (nav + landing shell) and PR #197 (unbuilt items hidden from nav).

## Goal

Replace the four `console:coming_soon` items in the Akademik nav group with real, read-only console pages:

| Nav item | Permission | Page |
|---|---|---|
| Siswa & kelas | `student_records.read` | class list + class roster |
| Jadwal | `student_records.read` | weekly timetable grid |
| Antrean penilaian | `grades.read` | ungraded homework submissions |
| Rapor | `grades.read` | report card list + preview |

All four already have backend models, services and JSON APIs. Only the session-auth web pages are missing.

## Delivery

Four independent PRs, in this order: roster, schedule, grading queue, rapor. Each PR adds one page and flips exactly one `url_name` in `apps/identity/nav.py`. The nav item (and the Akademik group) appears only once its page ships, because `get_nav_for_user` hides `coming_soon` items.

## Decisions

- **Read-only.** Write actions (grade/return homework, generate/approve/publish report cards) stay on the JSON API. Tracked in Notion as a follow-on.
- **Foundation-wide visibility.** Same as the permission-slip console and the JSON APIs: anyone holding the read permission sees every class of their foundation. Per-teacher scoping is a follow-on because it must change the API and console together.
- **Staff profile required.** `ROLE_PARENT` holds both `student_records.read` and `grades.read`. A guardian must never reach the school-side console, so every page requires a linked `Staff` row (same reasoning as `StaffConsoleMixin`). The four nav items get `requires_staff_profile=True` so the nav never shows a link that 404s.
- **Minimal PII.** Pages show student name and NIS only. Never NISN, NIK, phone, or guardian data.
- **Substitutions** were not shown on Jadwal v1; added afterwards, see "Follow-on: timetable substitutions".

## Shared pattern

- Views use `apps.identity.console_access.StaffConsoleMixin` (introduced by the Operasional console): `HasRequiredPermission` plus `_resolve_staff()` (a missing Staff profile 404s).
- New module `apps/academic/console_views.py` holds the four page views. `views.py` is already ~2100 lines.
- Routes live in `apps/academic/web_urls.py`, mounted under `/web/academic/`.
- Templates live in `frontend/templates/pages/`, extend `base_console.html`, and use the existing spec/17 tokens and the permission-slip console's visual conventions (0px radii, mono labels, `var(--color-*)`).
- Every page handles empty state and unknown-id 404. All queries filter `foundation_id` explicitly and `deleted_at__isnull=True`.
- New strings use `{% translate %}` / `gettext`, with EN entries added to the `.po` by hand and `compilemessages` run. Never `makemessages` (fuzzy-clobber risk, see i18n workflow note).
- Tests per page: permission allow/deny, no-Staff-profile 404 (guardian), unauthenticated rejected, cross-tenant 404, empty state, nav item appears.

## PR 1: Siswa & kelas

- `GET /web/academic/classes/` (`academic-class-list-page`): class groups for one academic year. Defaults to class groups of every active academic year (one per school); `?academic_year=<id>` shows that year instead, with a select of the foundation's years. Columns: name, grade level, school, wali kelas, active-enrolment count / capacity. Enrolment counts come from one annotated query, not per-row queries.
- `GET /web/academic/classes/<id>/` (`academic-class-detail-page`): header (name, year, school, wali kelas) and roster of active enrolments (name, NIS, status), ordered by name. 404 for unknown or cross-tenant id.
- Nav: `roster` gets `url_name="academic-class-list-page"` and `requires_staff_profile=True`.

## PR 2: Jadwal

- `GET /web/academic/timetable/` (`academic-timetable-page`): weekly grid (Mon-Sat columns, Sunday only if a slot exists; one row per period number) from `TimetableSlot`. `?lens=class:<id>` or `?lens=teacher:<staff id>` picks the view via one grouped select; default is the first class of an active academic year. Class view cells show subject, teacher, room; teacher view cells show class, subject, room. Unknown or cross-tenant lens ids 404; a malformed lens falls back to the default.
- Rows come from the slots themselves (period number, earliest start / latest end), not `PeriodGridSlot`, so break periods are not shown. No term filter: like `StudentTimetableView`, all non-deleted slots are shown (`TimetableSlot` has no term column).
- Nav: `schedule` flipped, `requires_staff_profile=True`.

## PR 3: Antrean penilaian

- `GET /web/academic/grading-queue/` (`academic-grading-queue-page`): `SUBMITTED` and `LATE` homework submissions, oldest first. Columns: student (name, NIS), homework, subject - class, submitted at, status (Terkumpul / Terlambat). Summary cards show total waiting and late counts. `?class_subject=<id>` filters (malformed ignored; an unknown or cross-tenant id simply yields an empty queue, since it is a filter and not a resource). At most 200 rows render, with a "showing N oldest of M" note.
- The queue query moves from the two inline JSON actions into `services.get_homework_grading_queue(foundation_id, homework_id, class_subject_id, include_graded)`, now used by both actions and the page. It filters `foundation_id` explicitly.
- Nav: `grading` flipped, `requires_staff_profile=True`.

## PR 4: Rapor

- `GET /web/academic/report-cards/` (`academic-report-card-list-page`): current (`is_current`) report cards with per-status count cards (Konsep / Menunggu Tinjauan / Disetujui / Diterbitkan). `?term=<id>` defaults to the most recent term of an active academic year; an explicit empty `term=` means every term. `?class_group=<id>` filters. Rows: student (name, NIS), class, term, version, status; 200-row cap with a "showing N of M" note.
- `GET /web/academic/report-cards/<id>/` (`academic-report-card-detail-page`): the frozen snapshot in console chrome (per-subject grade, descriptor via `compute_descriptor`, objective narrative; attendance summary; homeroom narrative, promotion decision, extracurricular notes). Unknown or cross-tenant id 404s.
- `GET /web/academic/report-cards/<id>/print/` (`academic-report-card-print-page`): returns `render_report_card_html` (the branded layout the PDF is built from) for opening in a new tab. No PDF download link: the existing `download` action returns a signed-URL JSON document, not a linkable file.
- Nav: `reports` flipped, `requires_staff_profile=True`.

## Follow-on: timetable substitutions (Jadwal)

- `?week=<date>` (any date resolves to that week's Monday; invalid or missing means the current week) with previous/next/this-week links and the date under each day header.
- `TimetableSubstitution` rows for the shown week are overlaid, mirroring `get_effective_teacher_for_slot`: PENDING and ACCEPTED count (PENDING is flagged "menunggu konfirmasi"), DECLINED and soft-deleted are ignored. A substitution counts only on the weekday its slot recurs on (`date__iso_week_day = slot.day_of_week`).
- Class view: the substitute replaces the teacher line, with "Pengganti <regular teacher>". Teacher view: a slot the teacher was substituted out of is struck through with "Digantikan <substitute>"; a slot they cover shows "Menggantikan <regular teacher>" even though it is not one of their own class subjects. One extra query regardless of slot count.

## Follow-on: write actions (Antrean penilaian, Rapor)

Built in `apps/academic/console_actions.py`. Every action is a POST-only view that reuses the audited service the JSON API calls, then redirects back with a flash message (post/redirect/get, like the Operasional and Keuangan consoles). Each is gated fail-closed by its own permission key plus the linked Staff profile (`StaffConsoleMixin`), and by school scope: the target must belong to a school in which the user holds that key (`permitted_schools`), otherwise 404. Read pages stay foundation-wide; only writes are school-scoped. Buttons render only where the user could actually act.

- **Grade** (`grades.write`, `grading-queue/<id>/grade/`): score required, 0-100, at most two decimals (console-only validation; the JSON API and service are unchanged, the spec sets no homework score scale), optional feedback. **Return** (`.../return/`): feedback required (the service enforces it). Both only act on SUBMITTED/LATE rows; a row someone else already graded or returned is refused, never overwritten. Redirect keeps a numeric `?class_subject=` filter.
- **Generate** (`grades.write`, `report-cards/generate/`): class (in a writable school) + term; the term must belong to the class's academic year. `generate_report_cards` skips cards no longer in DRAFT; the flash reports created / updated / skipped.
- **Approve** and **Publish** (`school_config.write`, same keys as the API) and **Revise** (`grades.write`), on the detail page: DRAFT/PENDING_REVIEW shows Setujui, APPROVED shows Terbitkan, a current PUBLISHED card shows Buat revisi (redirects to the new DRAFT version). An invalid transition flashes a generic Indonesian error instead of the service's raw `INVALID_TRANSITION` code.
- Still out of scope: editing a card's narrative / promotion decision / extracurricular notes.

## Out of scope (Notion Todos)

- Per-teacher class scoping.
