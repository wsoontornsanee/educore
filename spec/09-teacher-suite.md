# 09 — Teacher Suite

## 1. Scope

Web (classroom desktop) plus a mobile companion for the teacher. Success metric
for this module: **cut administrative time per teacher per week measurably** —
the deck's claim is up to 40% of teacher hours lost to manual admin.

## 2. Surfaces

| Surface | Primary tasks |
|---|---|
| Teacher web | Gradebook grid, assessment setup, exam authoring, report-card narratives, lesson plans |
| Teacher mobile | Period attendance, behaviour points, quick announcements, timetable + substitutions |

## 3. Requirements

| ID | Requirement |
|---|---|
| `TCH-001` | Home MUST show today's agenda from the timetable, including substitutions assigned to this teacher, with one-tap entry to each period's attendance. |
| `TCH-002` | Period attendance MUST default every student to `HADIR` and require marking only exceptions; submission MUST take ≤15 seconds for a 32-student class. |
| `TCH-003` | Period attendance MUST pre-fill from gate data: a student with no `IN` scan is pre-marked `ALPA` with a visible "from gate" badge the teacher can override. |
| `TCH-004` | Attendance submission MUST work offline on mobile and sync on reconnect (queued, idempotent). |
| `TCH-005` | Gradebook MUST be a virtualised grid handling 40 students × 30 assessments without lag, with keyboard-first entry (`ACD-006`). |
| `TCH-006` | Unsaved-state indicator MUST be explicit per cell: saving / saved / error-with-retry. |
| `TCH-007` | Concurrent edit by a second teacher MUST be detected (`updated_at` mismatch) and surfaced as a merge prompt, never a silent overwrite. |
| `TCH-008` | Behaviour points MUST be recordable in ≤3 taps from mobile: pick student → pick reason from a school-configured list → optional note. |
| `TCH-009` | Behaviour reasons MUST be a configurable catalogue with point values (positive and negative); free-text-only records are not permitted. |
| `TCH-010` | Lesson plans (RPP/modul ajar) MUST be creatable per class-subject per week, attachable to timetable slots, and duplicable from a previous week. |
| `TCH-011` | Teacher MUST be able to message a class's guardians as a group announcement, subject to school policy flag `teacher_can_broadcast` (default ON) and rate limit (5/day/class). |
| `TCH-012` | Teacher MUST NOT see financial data for any student under any circumstance. |
| `TCH-013` | Report-card narrative editor MUST show the student's objective attainment and attendance summary alongside the text field, with a character guide (recommended 300–600 chars). |
| `TCH-014` | Homework grading queue MUST let a teacher move through submissions with keyboard next/prev, score and feedback, without returning to a list. |
| `TCH-015` | Substitution assignment MUST notify the substitute immediately and show the original teacher's lesson plan for that slot. |
| `TCH-016` | All teacher writes MUST be attributed and audited (`recorded_by`). |

## 4. Efficiency instrumentation

Track and report per teacher per week: attendance submissions, median time to
submit, grade entries, homework graded, broadcasts sent. Surface as a
school-admin view — used in renewal conversations, never for public ranking.

## 5. API

(Uses 04 and 05 endpoints, plus:)
```
GET  /teacher/agenda?date
GET  /teacher/classes
POST /behaviour-records        {student_id, reason_id, note?, occurred_at}
GET  /behaviour-reasons | POST /behaviour-reasons
GET/POST /lesson-plans | POST /lesson-plans/:id/duplicate {target_week}
POST /teacher/broadcasts       {class_group_id, title, body, attachments[]}
GET  /teacher/metrics?from&to
```

## 6. Acceptance criteria

1. A homeroom teacher submits attendance for 32 students in under 15 seconds with 2 exceptions marked.
2. Attendance submitted in a basement classroom with no signal appears server-side within 10s of regaining connectivity, once.
3. Two teachers editing the same score cell produce a merge prompt, not a lost update.
4. A teacher account querying `/invoices` receives 403 `INSUFFICIENT_PERMISSION`.
5. Duplicating last week's lesson plan produces an editable copy attached to this week's slots.
