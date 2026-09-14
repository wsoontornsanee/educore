# 04 — Academic

## 1. Scope

Curriculum structure, timetabling, gradebook aligned to Kurikulum Merdeka,
assessments, online exams with auto-grading, homework, and report cards (rapor).

## 2. Entities

```
subjects(id, school_id, code, name, level, is_religious, credit_hours)
class_groups(id, school_id, academic_year_id, grade_level, name, homeroom_teacher_id, capacity)
class_subjects(id, class_group_id, subject_id, teacher_id, term_id)
learning_objectives(id, subject_id, grade_level, code, description)   -- TP
assessments(id, class_subject_id, type, title, objective_ids[], max_score, weight, due_at, published)
assessment_scores(id, assessment_id, student_id, score, descriptor, feedback, graded_by, graded_at)
timetable_slots(id, class_group_id, day_of_week, period_no, start_time, end_time, class_subject_id, room)
timetable_substitutions(id, slot_id, date, original_teacher_id, substitute_teacher_id, reason)
homework(id, class_subject_id, title, instructions, attachments[], assigned_at, due_at)
homework_submissions(id, homework_id, student_id, submitted_at, files[], text, status, score, feedback)
exams(id, class_subject_id, title, mode[ONLINE|PAPER], window_start, window_end, duration_min, shuffle, settings jsonb)
exam_questions(id, exam_id, seq, type[MCQ|MULTI|TRUE_FALSE|SHORT|ESSAY|MATCHING], body, media[], points, answer_key jsonb)
exam_attempts(id, exam_id, student_id, started_at, submitted_at, auto_score, manual_score, final_score, status)
report_cards(id, student_id, term_id, status, published_at, pdf_key, narrative)
```

## 3. Grading requirements (Kurikulum Merdeka)

| ID | Requirement |
|---|---|
| `ACD-001` | Assessment types MUST include `FORMATIVE`, `SUMMATIVE`, `PROJECT`, `PRACTICAL`, `EXAM`. Only `SUMMATIVE`/`EXAM`/`PROJECT` carry report-card weight by default; weights are configurable per subject per term and MUST sum to 100%. |
| `ACD-002` | Every assessment MUST be linkable to one or more `learning_objectives` (TP), and the gradebook MUST offer an objective-level attainment view per student. |
| `ACD-003` | Scores MUST support both numeric (0–100) and descriptor bands. Band thresholds are school-configurable; default: `Perlu Bimbingan <70`, `Cukup 70–79`, `Baik 80–89`, `Sangat Baik ≥90`. |
| `ACD-004` | A score entered outside `0..max_score` MUST be rejected with `SCORE_OUT_OF_RANGE`. |
| `ACD-005` | Grade changes after an assessment is `published` MUST require a reason and write an audit event; the prior value MUST remain retrievable. |
| `ACD-006` | Gradebook MUST support fast keyboard entry: a grid where Enter moves down, Tab moves right, and edits autosave with optimistic UI + conflict detection on `updated_at`. |
| `ACD-007` | Bulk score import per assessment via CSV, with per-row validation and a dry-run diff. |
| `ACD-008` | Final term grade = weighted average of category averages, rounded half-up to integer; the exact formula MUST be shown to the teacher on hover. |
| `ACD-009` | A student with no score in a weighted category MUST be flagged `INCOMPLETE` rather than silently averaged. |

## 4. Report cards (rapor)

| ID | Requirement |
|---|---|
| `ACD-010` | Report card generation MUST be a term-scoped batch job per class, producing a per-student PDF using the school's branded template. |
| `ACD-011` | Report card MUST contain: identity block (name, NISN, class, school, NPSN), per-subject final grade + descriptor + objective narrative, attendance summary (sakit/izin/alpa counts), extracurricular notes, homeroom teacher narrative, principal signature block. |
| `ACD-012` | Status machine: `DRAFT → PENDING_REVIEW → APPROVED → PUBLISHED`. Only `school_admin`/principal may `APPROVE`. |
| `ACD-013` | Parents MUST only see `PUBLISHED` report cards. |
| `ACD-014` | A school MAY gate report-card publication on tuition arrears — configurable flag `block_rapor_on_arrears`; when on, a blocked student's parent sees a payment prompt instead. **Default OFF**, and the flag MUST be recorded in audit when toggled. |
| `ACD-015` | Narrative generation MAY offer a draft suggestion from the student's objective attainment; the teacher MUST edit/approve — never auto-published. |
| `ACD-016` | Generated PDFs MUST be immutable once `PUBLISHED`; corrections create a new version with a visible revision number. |

## 5. Timetable

| ID | Requirement |
|---|---|
| `ACD-017` | Timetable builder MUST detect and block conflicts: teacher double-booked, room double-booked, class double-booked. |
| `ACD-018` | MUST support a per-school period grid (e.g. 10 periods, 40 min) plus break slots, and Friday-shortened schedules. |
| `ACD-019` | Substitutions MUST be assignable for a single date, notify the substitute, and appear on the teacher's mobile agenda. |
| `ACD-020` | The timetable is the source of truth for which class-period attendance is expected in (see 05). |

## 6. Online exams

| ID | Requirement |
|---|---|
| `ACD-021` | Attempt state MUST persist server-side every 20 seconds and on every answer change; a network drop MUST resume at the same question with remaining time intact. |
| `ACD-022` | Remaining time MUST be computed server-side from `started_at`; client clock is advisory only. |
| `ACD-023` | Auto-grading MUST cover MCQ, MULTI (partial credit configurable), TRUE_FALSE, MATCHING, and SHORT (exact/normalised match against an answer list). ESSAY routes to manual grading. |
| `ACD-024` | Optional integrity settings: shuffle questions, shuffle options, one-question-at-a-time, block back-navigation, full-screen lock with focus-loss counter. Focus losses are recorded and shown to the teacher, never auto-punished. |
| `ACD-025` | Late submission handling: `AUTO_SUBMIT` at window close; partial answers are retained. |
| `ACD-026` | An exam MUST be runnable for 300 concurrent students on one campus without score loss. |

## 7. Homework

| ID | Requirement |
|---|---|
| `ACD-027` | Submissions accept files (pdf/jpg/png/docx, ≤20MB each, ≤5 files) and/or text. |
| `ACD-028` | Submission status: `NOT_STARTED | SUBMITTED | LATE | GRADED | RETURNED`. Late is computed against `due_at` in school timezone. |
| `ACD-029` | Assigning homework MUST trigger a parent/student notification (see 13), respecting quiet hours. |
| `ACD-030` | Teacher MUST see a class completion bar and a one-tap "remind unsubmitted" action (rate-limited to once per 12h per homework). |

## 8. API

```
GET/POST /subjects | /class-groups | /class-subjects
GET  /gradebook?class_subject_id&term_id           -> matrix {students, assessments, scores}
POST /assessments | PATCH /assessments/:id | POST /assessments/:id/publish
PUT  /assessments/:id/scores                       {scores:[{student_id,score,feedback}]}
POST /assessments/:id/scores/import                (multipart, ?dry_run)
GET  /students/:id/attainment?term_id
POST /report-cards/generate                        {class_group_id, term_id} -> {job_id}
POST /report-cards/:id/approve | /publish
GET  /report-cards?student_id&term_id
GET/POST /timetable/slots | POST /timetable/substitutions
POST /exams | POST /exams/:id/publish
POST /exams/:id/attempts                           -> {attempt_id, questions}
PATCH /exam-attempts/:id/answers                   {question_id, answer}
POST /exam-attempts/:id/submit
GET  /exams/:id/grading-queue                      -> essay answers to grade
POST /homework | GET /homework/:id/submissions | POST /homework/:id/remind
POST /homework/:id/submissions                     (student, multipart)
```

## 9. Acceptance criteria

1. Entering 32 scores in the gradebook grid with only the keyboard produces 32 persisted scores and zero page reloads.
2. Changing a published score without a reason returns `REASON_REQUIRED`.
3. A 40-question MCQ exam for 300 students auto-scores within 60 seconds of window close.
4. A student who loses connection at question 12 resumes at question 12 with correct remaining time.
5. Publishing a class's report cards makes exactly those students' parents see them, and nobody else's.
