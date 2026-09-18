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
- **Substitutions not shown** on Jadwal v1 (follow-on).

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

- `GET /web/academic/grading-queue/`: `SUBMITTED` and `LATE` homework submissions, oldest first. Columns: student, homework, class - subject, submitted at, late badge. `?class_subject=<id>` filter.
- The queue query currently lives inline in `HomeworkViewSet.grading_queue`. It moves to a service function reused by the JSON action and the page.
- Nav: `grading` flipped, `requires_staff_profile=True`.

## PR 4: Rapor

- `GET /web/academic/report-cards/`: current report cards with term and class filters, status badges (Konsep / Menunggu Tinjauan / Disetujui / Diterbitkan), and per-status counts.
- `GET /web/academic/report-cards/<id>/`: renders `render_report_card_html`, plus a PDF link if the existing download path is directly linkable.
- Nav: `reports` flipped, `requires_staff_profile=True`.

## Out of scope (Notion Todos)

- Write actions on grading queue and Rapor.
- Per-teacher class scoping.
- Timetable substitution overlay.
