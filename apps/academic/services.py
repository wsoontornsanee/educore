import html
import logging
import random
from collections import Counter
from datetime import timedelta
import csv
import io
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.utils import timezone
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)

from apps.core import storage
from apps.core.services import audit, write_generated_file
from apps.identity.models import Foundation, RoleAssignment, Staff, Student
from apps.academic.models import (
    ALLOWED_SUBMISSION_CONTENT_TYPES,
    AUTO_GRADABLE_QUESTION_TYPES,
    Assessment,
    AssessmentScore,
    Broadcast,
    BroadcastPolicy,
    ClassEnrollment,
    ClassSubject,
    DEFAULT_DESCRIPTOR_BANDS,
    Exam,
    ExamAnswer,
    ExamAttempt,
    ExamAttemptStatus,
    ExamQuestion,
    ExamQuestionType,
    Homework,
    HomeworkSubmission,
    HomeworkSubmissionStatus,
    LessonPlan,
    MAX_SUBMISSION_FILES,
    MAX_SUBMISSION_FILE_SIZE,
    PeriodGridSlot,
    PermissionSlip,
    PermissionSlipAcknowledgement,
    ReportCard,
    ReportCardPolicy,
    ReportCardStatus,
    SubstitutionStatus,
    TimetableSlot,
    TimetableSubstitution,
    WEIGHTED_ASSESSMENT_TYPES,
)


class ScoreOutOfRangeError(ValueError):
    pass


class ScoreConflictError(ValueError):
    """TCH-007: raised instead of silently overwriting a score another write already changed.

    Carries the CURRENT record's state so a caller can render a merge prompt without a
    second round trip.
    """
    def __init__(
        self,
        message,
        current_score=None,
        current_version=None,
        current_feedback='',
        current_descriptor='',
        current_graded_by=None,
        current_graded_at=None,
    ):
        super().__init__(message)
        self.current_score = current_score
        self.current_version = current_version
        self.current_feedback = current_feedback
        self.current_descriptor = current_descriptor
        self.current_graded_by = current_graded_by
        self.current_graded_at = current_graded_at


class ReasonRequiredError(ValueError):
    pass


class ScoreCsvError(ValueError):
    """Raised on a malformed bulk-score CSV — fails loud on the whole file
    rather than silently skipping bad rows."""
    pass


class WeightConfigError(ValueError):
    pass


class TimetableConflictError(ValueError):
    pass


class SlotNotScheduledError(ValueError):
    pass


class NotAuthorizedForSlotError(PermissionError):
    pass


class SubstitutionDeclineReasonRequiredError(ValueError):
    pass


class PeriodGridMismatchError(ValueError):
    pass


class InvalidSubmissionFilesError(ValueError):
    pass


class HomeworkSubmissionStateError(ValueError):
    """ACD-028: raised when a submission can't transition from its current status."""
    pass


class ReminderRateLimitedError(ValueError):
    pass


class ReportCardStateError(ValueError):
    pass


class BroadcastNotAllowedError(ValueError):
    pass


class BroadcastRateLimitedError(ValueError):
    pass


class ExamWindowError(ValueError):
    pass


class AttemptAlreadySubmittedError(ValueError):
    pass


def compute_descriptor(score: Decimal, max_score: Decimal) -> str:
    """ACD-003: map a numeric score onto the default descriptor bands."""
    if score is None:
        return ''
    normalized = (score / max_score * 100) if max_score else Decimal('0')
    for threshold, label in DEFAULT_DESCRIPTOR_BANDS:
        if normalized >= threshold:
            return str(label)
    return str(DEFAULT_DESCRIPTOR_BANDS[-1][1])


# ACD-015: draft-suggestion phrase bank, keyed by descriptor label. Deterministic
# template-based generation, not an LLM call — the spec itself frames this as an
# optional convenience ("MAY offer a draft suggestion"), so a new AI dependency/
# architecture isn't warranted for it. The teacher always edits/approves before
# anything is saved (see set_report_card_content) — this function never writes.
_NARRATIVE_TEMPLATES = {
    'Sangat Baik': "Sangat baik dalam pencapaian kompetensi {subject}.",
    'Baik': "Baik dalam pencapaian kompetensi {subject}, terus tingkatkan.",
    'Cukup': "Cukup dalam pencapaian kompetensi {subject}; perlu penguatan lebih lanjut.",
    'Perlu Bimbingan': "Memerlukan bimbingan intensif dalam pencapaian kompetensi {subject}.",
}


def suggest_objective_narrative(subject: str, grade, descriptor: str = None) -> str:
    """ACD-015: draft a per-subject objective narrative suggestion from the
    student's grade/descriptor. A draft, never a write — the caller must pass
    it through set_report_card_content for the teacher to edit and approve."""
    if grade is None:
        return f"Belum ada nilai untuk {subject} pada periode ini."
    if descriptor is None:
        descriptor = compute_descriptor(Decimal(str(grade)), Decimal('100'))
    template = _NARRATIVE_TEMPLATES.get(descriptor, _NARRATIVE_TEMPLATES['Cukup'])
    return template.format(subject=subject)


def set_assessment_score(
    assessment: Assessment,
    student,
    score=None,
    feedback='',
    descriptor=None,
    reason=None,
    actor=None,
    expected_version=None,
) -> AssessmentScore:
    """Create or update a student's score on an assessment (ACD-004, ACD-005).

    TCH-007: pass `expected_version` (the version the caller last read) to detect a
    concurrent write instead of silently overwriting it — raises ScoreConflictError if
    the record has since moved on. Omitting it (default) keeps today's last-write-wins
    behavior. A brand-new score (no existing row) has nothing to conflict with, so
    expected_version is a no-op on create.
    """
    if score is not None:
        score = Decimal(str(score))
        if score < 0 or score > assessment.max_score:
            raise ScoreOutOfRangeError(
                f"SCORE_OUT_OF_RANGE: score must be between 0 and {assessment.max_score}."
            )

    existing = AssessmentScore.objects.filter(assessment=assessment, student=student).first()

    if existing is not None and expected_version is not None and existing.version != expected_version:
        graded_by_name = None
        if existing.graded_by:
            graded_by_name = getattr(existing.graded_by, 'full_name', '') or str(existing.graded_by)
        graded_at_str = None
        if existing.graded_at:
            graded_at_str = existing.graded_at.isoformat()
        elif hasattr(existing, 'updated_at') and existing.updated_at:
            graded_at_str = existing.updated_at.isoformat()

        raise ScoreConflictError(
            f"SCORE_CONFLICT: expected version {expected_version}, but the score is now at version {existing.version}.",
            current_score=existing.score,
            current_version=existing.version,
            current_feedback=existing.feedback,
            current_descriptor=existing.descriptor,
            current_graded_by=graded_by_name,
            current_graded_at=graded_at_str,
        )

    old_value = str(existing.score) if existing else None
    new_value = str(score) if score is not None else None
    value_changed = existing is not None and old_value != new_value

    if assessment.published and existing is not None and value_changed and not reason:
        raise ReasonRequiredError("REASON_REQUIRED: changing a published assessment's score requires a reason.")

    resolved_descriptor = descriptor if descriptor is not None else compute_descriptor(score, assessment.max_score)

    if existing:
        record = existing
        record.score = score
        record.descriptor = resolved_descriptor
        record.feedback = feedback
        record.version += 1
        if score is not None:
            record.graded_by = actor
            record.graded_at = timezone.now()
        record.save()
    else:
        record = AssessmentScore.objects.create(
            foundation_id=assessment.foundation_id,
            assessment=assessment,
            student=student,
            score=score,
            descriptor=resolved_descriptor,
            feedback=feedback,
            graded_by=actor,
            graded_at=timezone.now() if score is not None else None,
        )

    audit_diff = {'before': old_value, 'after': new_value}
    if reason:
        audit_diff['reason'] = reason
    audit(
        action='academic.assessment_score.updated' if existing else 'academic.assessment_score.created',
        entity_type='AssessmentScore',
        entity_id=record.id,
        foundation_id=assessment.foundation_id,
        diff=audit_diff,
    )

    return record


def parse_score_csv(csv_content: str) -> list[dict]:
    """ACD-007: parse a bulk-score CSV. Required columns: 'nis', 'score'
    (blank score clears it, same as passing score=None). Optional: 'feedback'.

    Keyed by NIS rather than the internal student_id, since that's what a
    teacher's own spreadsheet actually has. Raises ScoreCsvError on a missing
    header, missing NIS, or a non-numeric score — fails loud on the whole file
    rather than silently skipping malformed rows.
    """
    reader = csv.DictReader(io.StringIO(csv_content))
    if not reader.fieldnames or 'nis' not in reader.fieldnames or 'score' not in reader.fieldnames:
        raise ScoreCsvError("CSV_MISSING_COLUMNS: header row must include 'nis' and 'score' columns.")

    rows = []
    for row_number, row in enumerate(reader, start=2):  # header is row 1
        nis = (row.get('nis') or '').strip()
        if not nis:
            raise ScoreCsvError(f"CSV_MISSING_NIS: row {row_number} has no NIS.")

        raw_score = (row.get('score') or '').strip()
        if raw_score == '':
            score = None
        else:
            try:
                score = Decimal(raw_score)
            except InvalidOperation:
                raise ScoreCsvError(f"CSV_INVALID_SCORE: row {row_number} has a non-numeric score '{raw_score}'.")

        rows.append({
            'row_number': row_number,
            'nis': nis,
            'score': score,
            'feedback': (row.get('feedback') or '').strip(),
        })
    return rows


def preview_bulk_score_import(assessment: Assessment, csv_content: str) -> list[dict]:
    """ACD-007 dry-run: reports what WOULD change per CSV row without writing
    anything. Each result carries 'action': CREATE/UPDATE/NO_CHANGE/ERROR.
    """
    results = []
    for row in parse_score_csv(csv_content):
        student = Student.objects.filter(foundation_id=assessment.foundation_id, nis=row['nis']).first()
        if not student:
            results.append({**row, 'action': 'ERROR', 'student_id': None,
                             'current_score': None, 'new_score': None,
                             'error': f"STUDENT_NOT_FOUND: no student with NIS '{row['nis']}'."})
            continue

        if row['score'] is not None and (row['score'] < 0 or row['score'] > assessment.max_score):
            results.append({**row, 'action': 'ERROR', 'student_id': student.id,
                             'current_score': None, 'new_score': None,
                             'error': f"SCORE_OUT_OF_RANGE: must be between 0 and {assessment.max_score}."})
            continue

        existing = AssessmentScore.objects.filter(assessment=assessment, student=student).first()
        current_score = existing.score if existing else None
        if existing is None:
            action = 'CREATE'
        elif current_score == row['score']:
            action = 'NO_CHANGE'
        else:
            action = 'UPDATE'

        results.append({
            **row, 'action': action, 'student_id': student.id,
            'current_score': str(current_score) if current_score is not None else None,
            'new_score': str(row['score']) if row['score'] is not None else None,
            'error': None,
        })
    return results


def apply_bulk_score_import(assessment: Assessment, csv_content: str, actor=None, reason: str = None) -> dict:
    """ACD-007: commits a CSV bulk import via set_assessment_score per row, so
    conflict/range/reason-required semantics stay identical to the existing
    JSON bulk endpoint (PUT /assessments/:id/scores/). A row's failure doesn't
    stop the rest of the file. Returns {'imported': int, 'errors': [...]}."""
    imported = 0
    errors = []
    for row in parse_score_csv(csv_content):
        student = Student.objects.filter(foundation_id=assessment.foundation_id, nis=row['nis']).first()
        if not student:
            errors.append({'row_number': row['row_number'], 'nis': row['nis'],
                            'error': f"STUDENT_NOT_FOUND: no student with NIS '{row['nis']}'."})
            continue
        try:
            set_assessment_score(
                assessment=assessment, student=student, score=row['score'],
                feedback=row['feedback'], reason=reason, actor=actor,
            )
            imported += 1
        except (ScoreOutOfRangeError, ReasonRequiredError, ScoreConflictError) as exc:
            errors.append({'row_number': row['row_number'], 'nis': row['nis'], 'error': str(exc)})

    audit(
        action='academic.assessment_score.csv_import',
        entity_type='Assessment',
        entity_id=assessment.id,
        foundation_id=assessment.foundation_id,
        diff={'imported': imported, 'error_count': len(errors)},
    )
    return {'imported': imported, 'errors': errors}


def publish_assessment(assessment: Assessment) -> Assessment:
    """Publish an assessment, validating weighted-category weight budget (ACD-001)."""
    if assessment.type in WEIGHTED_ASSESSMENT_TYPES:
        sibling_weight = Assessment.objects.filter(
            class_subject=assessment.class_subject,
            type__in=WEIGHTED_ASSESSMENT_TYPES,
            published=True,
            deleted_at__isnull=True,
        ).exclude(pk=assessment.pk).values_list('weight', flat=True)
        total = sum(sibling_weight, Decimal('0.00')) + assessment.weight
        if total > Decimal('100.00'):
            raise WeightConfigError(
                f"WEIGHT_SUM_EXCEEDS_100: published weighted assessments for this class subject would total {total}%."
            )

    assessment.published = True
    assessment.save(update_fields=['published', 'updated_at'])
    audit(
        action='academic.assessment.published',
        entity_type='Assessment',
        entity_id=assessment.id,
        foundation_id=assessment.foundation_id,
        diff={'title': assessment.title},
    )
    return assessment


def compute_term_grade(student, class_subject) -> dict:
    """ACD-008/ACD-009: weighted final grade from published weighted-category assessments."""
    weighted_assessments = list(
        Assessment.objects.filter(
            class_subject=class_subject,
            type__in=WEIGHTED_ASSESSMENT_TYPES,
            published=True,
            deleted_at__isnull=True,
        )
    )

    weight_sum = sum((a.weight for a in weighted_assessments), Decimal('0.00'))
    if not weighted_assessments or weight_sum != Decimal('100.00'):
        raise WeightConfigError(
            f"WEIGHT_CONFIG_INCOMPLETE: published weighted assessment weights sum to {weight_sum}%, expected 100%."
        )

    scores_by_assessment = {
        s.assessment_id: s.score
        for s in AssessmentScore.objects.filter(
            assessment__in=weighted_assessments, student=student
        )
    }

    missing = [a.title for a in weighted_assessments if scores_by_assessment.get(a.id) is None]
    if missing:
        return {
            'status': 'INCOMPLETE',
            'grade': None,
            'missing_assessments': missing,
            'formula': None,
        }

    total = Decimal('0.00')
    terms = []
    for a in weighted_assessments:
        normalized = (scores_by_assessment[a.id] / a.max_score * 100)
        contribution = normalized * a.weight / Decimal('100.00')
        total += contribution
        terms.append(f"({a.title}: {scores_by_assessment[a.id]}/{a.max_score} x {a.weight}%)")

    grade = int(total.quantize(Decimal('1'), rounding=ROUND_HALF_UP))

    return {
        'status': 'COMPLETE',
        'grade': grade,
        'missing_assessments': [],
        'formula': " + ".join(terms),
    }


def check_timetable_conflicts(class_subject, day_of_week, period_no, room='', exclude_pk=None) -> None:
    """ACD-017: block teacher, room, and class double-booking for the same day/period."""
    base_qs = TimetableSlot.objects.filter(
        foundation_id=class_subject.foundation_id,
        day_of_week=day_of_week,
        period_no=period_no,
        deleted_at__isnull=True,
    )
    if exclude_pk:
        base_qs = base_qs.exclude(pk=exclude_pk)

    if base_qs.filter(class_subject__class_group=class_subject.class_group).exists():
        raise TimetableConflictError("CLASS_DOUBLE_BOOKED: this class group already has a slot in this period.")

    if base_qs.filter(class_subject__teacher=class_subject.teacher).exists():
        raise TimetableConflictError("TEACHER_DOUBLE_BOOKED: this teacher already has a slot in this period.")

    if room and base_qs.filter(room=room).exists():
        raise TimetableConflictError("ROOM_DOUBLE_BOOKED: this room already has a slot in this period.")


def get_period_grid(school, day_of_week) -> list:
    """ACD-018: a school's configured periods for a weekday, ordered. Empty if unconfigured."""
    return list(
        PeriodGridSlot.objects.filter(
            foundation_id=school.foundation_id, school=school, day_of_week=day_of_week,
        ).order_by('period_no')
    )


def set_period_grid(school, day_of_week, periods: list) -> list:
    """ACD-018: atomically replace the full period grid for a school+weekday.

    Replaced as one unit rather than period-by-period: renumbering (e.g. moving a
    break from period 3 to period 4) would otherwise transiently collide with the
    unique_together constraint mid-edit.
    """
    PeriodGridSlot.objects.filter(
        foundation_id=school.foundation_id, school=school, day_of_week=day_of_week,
    ).delete()

    created = []
    for p in periods:
        created.append(PeriodGridSlot.objects.create(
            foundation_id=school.foundation_id,
            school=school,
            day_of_week=day_of_week,
            period_no=p['period_no'],
            start_time=p['start_time'],
            end_time=p['end_time'],
            is_break=p.get('is_break', False),
            label=p.get('label', ''),
        ))

    audit(
        action='academic.period_grid.set',
        entity_type='School',
        entity_id=school.id,
        foundation_id=school.foundation_id,
        school_id=school.id,
        diff={'day_of_week': day_of_week, 'period_count': len(created)},
    )
    return created


def create_timetable_slot(class_subject, day_of_week, period_no, start_time, end_time, room='') -> TimetableSlot:
    check_timetable_conflicts(class_subject, day_of_week, period_no, room)

    school = class_subject.class_group.school
    grid = get_period_grid(school, day_of_week)
    if grid:
        grid_period = next((p for p in grid if p.period_no == period_no), None)
        if grid_period is None:
            raise PeriodGridMismatchError(f"PERIOD_NOT_IN_GRID: period {period_no} is not configured for {school.name} on this weekday.")
        if grid_period.is_break:
            raise PeriodGridMismatchError(f"PERIOD_IS_BREAK: period {period_no} is a break slot for {school.name} on this weekday.")
        if grid_period.start_time != start_time or grid_period.end_time != end_time:
            raise PeriodGridMismatchError(
                f"PERIOD_TIME_MISMATCH: period {period_no} is configured as {grid_period.start_time}-{grid_period.end_time}, not {start_time}-{end_time}."
            )

    slot = TimetableSlot.objects.create(
        foundation_id=class_subject.foundation_id,
        class_subject=class_subject,
        day_of_week=day_of_week,
        period_no=period_no,
        start_time=start_time,
        end_time=end_time,
        room=room,
    )
    audit(
        action='academic.timetable_slot.created',
        entity_type='TimetableSlot',
        entity_id=slot.id,
        foundation_id=slot.foundation_id,
        diff={'class_subject': str(class_subject), 'day_of_week': day_of_week, 'period_no': period_no},
    )
    return slot


def validate_submission_files(files) -> None:
    """ACD-027: at most 5 files, each <=20MB, and an accepted content type."""
    if len(files) > MAX_SUBMISSION_FILES:
        raise InvalidSubmissionFilesError(f"TOO_MANY_FILES: at most {MAX_SUBMISSION_FILES} files are allowed.")
    for f in files:
        size = f.get('size', 0)
        content_type = f.get('content_type', '')
        if size > MAX_SUBMISSION_FILE_SIZE:
            raise InvalidSubmissionFilesError(f"FILE_TOO_LARGE: '{f.get('filename', '?')}' exceeds 20MB.")
        if content_type not in ALLOWED_SUBMISSION_CONTENT_TYPES:
            raise InvalidSubmissionFilesError(f"UNSUPPORTED_FILE_TYPE: '{content_type}' is not accepted.")


def store_homework_submission_file(homework: Homework, uploaded_file, uploaded_by=None) -> dict:
    """ACD-027: validate and persist one uploaded homework attachment, returning the
    {key, filename, size, content_type} dict submit_homework's `files` list expects.

    Reuses validate_submission_files (wrapped as a one-item list) rather than
    duplicating its size/content-type rules. Written directly to GCS via
    core.write_generated_file, catalogued as a core.StoredFile row (ARC-026,
    ARC-030) — no raw filesystem write.
    """
    file_meta = {
        'filename': uploaded_file.name,
        'size': uploaded_file.size,
        'content_type': uploaded_file.content_type,
    }
    validate_submission_files([file_meta])

    data = b''.join(uploaded_file.chunks())
    stored_file = write_generated_file(
        purpose='homework_submission', filename=uploaded_file.name,
        data=data, content_type=uploaded_file.content_type,
        foundation_id=homework.foundation_id, uploaded_by=uploaded_by,
    )

    return {
        'key': stored_file.key,
        'filename': uploaded_file.name,
        'size': uploaded_file.size,
        'content_type': uploaded_file.content_type,
    }


def assign_homework(class_subject, title, instructions, assigned_at, due_at) -> Homework:
    """ACD-029: create a homework assignment and notify enrolled students' guardians.

    Students have no login of their own anywhere in this codebase (no `user` FK on
    `Student`) — every student-facing notice here is actually addressed to the
    student's guardians, same resolution as `send_broadcast`.
    """
    homework = Homework.objects.create(
        foundation_id=class_subject.foundation_id,
        class_subject=class_subject,
        title=title,
        instructions=instructions,
        assigned_at=assigned_at,
        due_at=due_at,
    )
    audit(
        action='academic.homework.assigned',
        entity_type='Homework',
        entity_id=homework.id,
        foundation_id=homework.foundation_id,
        diff={'title': title, 'class_subject': str(class_subject), 'due_at': str(due_at)},
    )

    try:
        from apps.identity.models import GuardianLink
        from apps.notifications.models import NotificationCategory
        from apps.notifications.services import dispatch_intent

        student_ids = ClassEnrollment.objects.filter(
            class_group=class_subject.class_group, is_active=True, deleted_at__isnull=True,
        ).values_list('student_id', flat=True)
        guardian_links = GuardianLink.objects.filter(
            foundation_id=homework.foundation_id, student_id__in=student_ids, deleted_at__isnull=True,
        ).select_related('guardian__person', 'guardian__user', 'student__person')

        seen_guardian_ids = set()
        for link in guardian_links:
            guardian = link.guardian
            if guardian.id in seen_guardian_ids or not guardian.user:
                continue
            seen_guardian_ids.add(guardian.id)

            dispatch_intent(
                foundation_id=homework.foundation_id,
                category=NotificationCategory.HOMEWORK,
                template_key='academic.homework.assigned',
                payload={
                    'title': homework.title,
                    'subject': class_subject.subject.name,
                    'class_group': class_subject.class_group.name,
                    'due_at': str(due_at),
                },
                school_id=class_subject.class_group.school_id,
                recipient_user=guardian.user,
                recipient_phone=getattr(guardian.user, 'phone_e164', ''),
                recipient_email=getattr(guardian.user, 'email', ''),
                recipient_name=guardian.person.full_name if guardian.person else '',
                dedupe_key=f"homework_assigned:{homework.id}:{guardian.id}",
            )
    except Exception as exc:
        logger.warning(f"Error notifying guardians of homework assignment #{homework.id}: {exc}")

    return homework


def submit_homework(homework: Homework, student, text='', files=None) -> HomeworkSubmission:
    """ACD-027/ACD-028: create or update a student's homework submission.

    A resubmission is only allowed while the existing submission is
    SUBMITTED/LATE/RETURNED (i.e. not yet graded) — once GRADED, resubmitting
    would otherwise silently overwrite the teacher's score with no error
    (the previous behavior). A teacher must explicitly return_homework_submission
    it first, which is the actual ACD-028 revise-and-resubmit path.
    """
    files = files or []
    validate_submission_files(files)

    existing = HomeworkSubmission.objects.filter(homework=homework, student=student).first()
    if existing and existing.status == HomeworkSubmissionStatus.GRADED:
        raise HomeworkSubmissionStateError(
            "SUBMISSION_ALREADY_GRADED: cannot resubmit a graded submission — "
            "ask the teacher to return it for revision first."
        )

    now = timezone.now()
    status = HomeworkSubmissionStatus.LATE if now > homework.due_at else HomeworkSubmissionStatus.SUBMITTED

    submission, created = HomeworkSubmission.objects.update_or_create(
        foundation_id=homework.foundation_id,
        homework=homework,
        student=student,
        defaults={
            'submitted_at': now,
            'files': files,
            'text': text,
            'status': status,
        },
    )
    audit(
        action='academic.homework_submission.submitted',
        entity_type='HomeworkSubmission',
        entity_id=submission.id,
        foundation_id=homework.foundation_id,
        diff={'status': status, 'file_count': len(files)},
    )
    return submission


def grade_homework_submission(submission: HomeworkSubmission, score, feedback='', actor=None) -> HomeworkSubmission:
    submission.score = score
    submission.feedback = feedback
    submission.status = HomeworkSubmissionStatus.GRADED
    submission.graded_by = actor
    submission.graded_at = timezone.now()
    submission.save()
    audit(
        action='academic.homework_submission.graded',
        entity_type='HomeworkSubmission',
        entity_id=submission.id,
        foundation_id=submission.foundation_id,
        diff={'score': str(score) if score is not None else None},
    )
    return submission


def return_homework_submission(submission: HomeworkSubmission, feedback: str, actor=None) -> HomeworkSubmission:
    """ACD-028: return a submission to the student for revision.

    Allowed from SUBMITTED/LATE/GRADED — a teacher can return a submission
    they haven't graded yet (asking for a redo before even scoring it), or
    one they already graded (asking for a revision after scoring). Clears
    any existing score, since a returned submission has no valid grade until
    it's resubmitted and graded again. feedback is required — it's the
    revision instruction the student sees, not optional commentary.
    """
    if submission.status == HomeworkSubmissionStatus.RETURNED:
        raise HomeworkSubmissionStateError("ALREADY_RETURNED: this submission is already RETURNED.")
    if not feedback:
        raise ReasonRequiredError("FEEDBACK_REQUIRED: returning a submission requires feedback explaining why.")

    previous_status = submission.status
    submission.status = HomeworkSubmissionStatus.RETURNED
    submission.feedback = feedback
    submission.score = None
    submission.graded_by = actor
    submission.graded_at = timezone.now()
    submission.save()
    audit(
        action='academic.homework_submission.returned',
        entity_type='HomeworkSubmission',
        entity_id=submission.id,
        foundation_id=submission.foundation_id,
        diff={'previous_status': previous_status},
    )
    return submission


def get_homework_grading_queue(foundation_id, homework_id=None, class_subject_id=None, include_graded=False):
    """TCH-014: homework submissions awaiting grading, oldest first.

    Shared by the JSON grading-queue actions and the web console page so the
    two never drift. `include_graded=False` keeps only SUBMITTED and LATE.
    Returns an unsliced queryset with everything the row renderers need."""
    qs = HomeworkSubmission.objects.filter(foundation_id=foundation_id, deleted_at__isnull=True)
    if homework_id:
        qs = qs.filter(homework_id=homework_id)
    if class_subject_id:
        qs = qs.filter(homework__class_subject_id=class_subject_id)
    if not include_graded:
        qs = qs.filter(status__in=[HomeworkSubmissionStatus.SUBMITTED, HomeworkSubmissionStatus.LATE])
    return qs.select_related(
        'homework', 'homework__class_subject', 'homework__class_subject__subject',
        'homework__class_subject__class_group', 'student', 'student__person', 'graded_by',
    ).order_by('submitted_at', 'id')


def get_homework_completion(homework: Homework) -> dict:
    """ACD-030: 4-segment class completion counts and percentages (graded, submitted, late, missing)."""
    enrolled_student_ids = set(
        ClassEnrollment.objects.filter(
            class_group=homework.class_subject.class_group,
            is_active=True,
            deleted_at__isnull=True,
        ).values_list('student_id', flat=True)
    )
    total = len(enrolled_student_ids)

    submissions = HomeworkSubmission.objects.filter(
        homework=homework,
        student_id__in=enrolled_student_ids,
        deleted_at__isnull=True,
    )

    graded = 0
    submitted = 0
    late = 0
    accounted_student_ids = set()

    for sub in submissions:
        accounted_student_ids.add(sub.student_id)
        if sub.status == HomeworkSubmissionStatus.GRADED or sub.score is not None:
            graded += 1
        elif sub.status == HomeworkSubmissionStatus.LATE or (sub.submitted_at and sub.submitted_at > homework.due_at):
            late += 1
        else:
            submitted += 1

    missing = max(total - len(accounted_student_ids), 0)

    if total > 0:
        graded_pct = round((graded / total) * 100, 1)
        submitted_pct = round((submitted / total) * 100, 1)
        late_pct = round((late / total) * 100, 1)
        missing_pct = round((missing / total) * 100, 1)
    else:
        graded_pct = submitted_pct = late_pct = missing_pct = 0.0

    return {
        'total': total,
        'graded': graded,
        'submitted': submitted,
        'late': late,
        'missing': missing,
        'not_started': missing,  # backward compatibility
        'percentages': {
            'graded': graded_pct,
            'submitted': submitted_pct,
            'late': late_pct,
            'missing': missing_pct,
        },
    }


def get_homework_remind_status(homework: Homework) -> dict:
    """Retrieve recipient delivery receipts for the most recent homework reminder run."""
    from apps.notifications.models import NotificationIntent

    intents = NotificationIntent.objects.filter(
        foundation_id=homework.foundation_id,
        dedupe_key__startswith=f"homework_reminder:{homework.id}:",
    ).order_by('-created_at')

    if not intents.exists():
        return {
            'homework_id': homework.id,
            'last_reminded_at': homework.last_reminded_at,
            'recipient_count': 0,
            'recipients': [],
        }

    latest_created_at = intents.first().created_at
    recent_intents = intents.filter(
        created_at__gte=latest_created_at - timedelta(minutes=2)
    ).prefetch_related('deliveries')

    recipients = []
    for intent in recent_intents:
        deliveries_data = [
            {
                'channel': d.channel,
                'provider': d.provider,
                'status': d.status,
                'sent_at': d.sent_at,
                'delivered_at': d.delivered_at,
                'error_code': d.error_code,
                'error_message': d.error_message,
            }
            for d in intent.deliveries.all()
        ]
        recipients.append({
            'intent_id': intent.id,
            'recipient_name': intent.recipient_name,
            'recipient_phone': intent.recipient_phone,
            'recipient_email': intent.recipient_email,
            'status': intent.status,
            'sent_at': intent.sent_at,
            'deliveries': deliveries_data,
        })

    return {
        'homework_id': homework.id,
        'last_reminded_at': homework.last_reminded_at,
        'recipient_count': len(recipients),
        'recipients': recipients,
    }


def remind_unsubmitted(homework: Homework) -> dict:
    """ACD-030: rate-limited (once per 12h) reminder to unsubmitted students' guardians."""
    now = timezone.now()
    if homework.last_reminded_at and (now - homework.last_reminded_at) < timedelta(hours=12):
        raise ReminderRateLimitedError("REMINDER_RATE_LIMITED: reminders can only be sent once every 12 hours.")

    enrolled_ids = set(
        ClassEnrollment.objects.filter(
            class_group=homework.class_subject.class_group, is_active=True, deleted_at__isnull=True,
        ).values_list('student_id', flat=True)
    )
    submitted_ids = set(
        HomeworkSubmission.objects.filter(homework=homework, deleted_at__isnull=True).values_list('student_id', flat=True)
    )
    unsubmitted_ids = list(enrolled_ids - submitted_ids)

    homework.last_reminded_at = now
    homework.save(update_fields=['last_reminded_at', 'updated_at'])

    audit(
        action='academic.homework.reminder_sent',
        entity_type='Homework',
        entity_id=homework.id,
        foundation_id=homework.foundation_id,
        diff={'unsubmitted_count': len(unsubmitted_ids)},
    )

    try:
        from apps.identity.models import GuardianLink
        from apps.notifications.models import NotificationCategory
        from apps.notifications.services import dispatch_intent

        guardian_links = GuardianLink.objects.filter(
            foundation_id=homework.foundation_id, student_id__in=unsubmitted_ids, deleted_at__isnull=True,
        ).select_related('guardian__person', 'guardian__user')

        seen_guardian_ids = set()
        for link in guardian_links:
            guardian = link.guardian
            if guardian.id in seen_guardian_ids or not guardian.user:
                continue
            seen_guardian_ids.add(guardian.id)

            dispatch_intent(
                foundation_id=homework.foundation_id,
                category=NotificationCategory.HOMEWORK,
                template_key='academic.homework.reminder',
                payload={
                    'title': homework.title,
                    'subject': homework.class_subject.subject.name,
                    'due_at': str(homework.due_at),
                },
                school_id=homework.class_subject.class_group.school_id,
                recipient_user=guardian.user,
                recipient_phone=getattr(guardian.user, 'phone_e164', '') or '',
                recipient_email=getattr(guardian.user, 'email', '') or '',
                recipient_name=(guardian.person.full_name if guardian.person else '') or '',
                # Keyed by reminder date, not a fixed key: the NEXT legitimate reminder
                # (12h+ later per the rate limit above) must not be deduped by this one.
                dedupe_key=f"homework_reminder:{homework.id}:{guardian.id}:{now.date()}",
            )
    except Exception as exc:
        logger.warning(f"Error notifying guardians of homework reminder #{homework.id}: {exc}")

    return {'reminded_student_ids': unsubmitted_ids, 'count': len(unsubmitted_ids)}


def start_attempt(exam: Exam, student) -> ExamAttempt:
    """ACD-021: start (or resume) a student's attempt. One attempt per student per exam."""
    now = timezone.now()
    existing = ExamAttempt.objects.filter(exam=exam, student=student).first()
    if existing:
        return existing

    if now < exam.window_start or now > exam.window_end:
        raise ExamWindowError("EXAM_WINDOW_CLOSED: the exam is not currently open.")

    question_ids = list(exam.questions.filter(deleted_at__isnull=True).order_by('seq').values_list('id', flat=True))
    if exam.shuffle:
        rng = random.Random(f"{exam.id}:{student.id}")
        rng.shuffle(question_ids)

    return ExamAttempt.objects.create(
        foundation_id=exam.foundation_id,
        exam=exam,
        student=student,
        started_at=now,
        status=ExamAttemptStatus.IN_PROGRESS,
        question_order=question_ids,
    )


def compute_remaining_seconds(attempt: ExamAttempt) -> int:
    """ACD-022: remaining time computed server-side from started_at; client clock is advisory only."""
    if attempt.status != ExamAttemptStatus.IN_PROGRESS:
        return 0
    deadline = attempt.started_at + timedelta(minutes=attempt.exam.duration_min)
    deadline = min(deadline, attempt.exam.window_end)
    remaining = (deadline - timezone.now()).total_seconds()
    return max(int(remaining), 0)


def _normalize_short_answer(text) -> str:
    return str(text or '').strip().lower()


def auto_grade_answer(question: ExamQuestion, answer: dict):
    """ACD-023: auto-grade MCQ/MULTI/TRUE_FALSE/SHORT/MATCHING. Returns None for ESSAY (manual)."""
    if question.type not in AUTO_GRADABLE_QUESTION_TYPES:
        return None

    key = question.answer_key or {}

    if question.type == ExamQuestionType.MCQ:
        return question.points if answer.get('selected') == key.get('correct') else Decimal('0.00')

    if question.type == ExamQuestionType.TRUE_FALSE:
        return question.points if answer.get('selected') == key.get('correct') else Decimal('0.00')

    if question.type == ExamQuestionType.SHORT:
        accepted = {_normalize_short_answer(a) for a in key.get('accepted', [])}
        return question.points if _normalize_short_answer(answer.get('text')) in accepted else Decimal('0.00')

    if question.type == ExamQuestionType.MATCHING:
        correct_pairs = key.get('pairs', {})
        given_pairs = answer.get('pairs', {})
        return question.points if given_pairs == correct_pairs else Decimal('0.00')

    if question.type == ExamQuestionType.MULTI:
        correct = set(key.get('correct', []))
        selected = set(answer.get('selected', []))
        if not key.get('partial_credit'):
            return question.points if selected == correct else Decimal('0.00')
        if not correct:
            return Decimal('0.00')
        correct_hits = len(selected & correct)
        wrong_hits = len(selected - correct)
        fraction = max(correct_hits - wrong_hits, 0) / len(correct)
        return (question.points * Decimal(str(fraction))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    return None


def save_answer(attempt: ExamAttempt, question: ExamQuestion, answer: dict) -> ExamAnswer:
    """ACD-021: persist an answer on every change. Auto-grades non-ESSAY types immediately."""
    if attempt.status != ExamAttemptStatus.IN_PROGRESS:
        raise AttemptAlreadySubmittedError("ATTEMPT_ALREADY_SUBMITTED: cannot change answers after submission.")

    points_awarded = auto_grade_answer(question, answer)

    record, _created = ExamAnswer.objects.update_or_create(
        foundation_id=attempt.foundation_id,
        attempt=attempt,
        question=question,
        defaults={'answer': answer, 'points_awarded': points_awarded},
    )
    return record


def submit_attempt(attempt: ExamAttempt, auto=False) -> ExamAttempt:
    """ACD-025: finalize an attempt. Late submissions AUTO_SUBMIT, retaining partial answers."""
    if attempt.status != ExamAttemptStatus.IN_PROGRESS:
        return attempt

    graded_answers = attempt.answers.filter(points_awarded__isnull=False, deleted_at__isnull=True)
    auto_score = sum((a.points_awarded for a in graded_answers), Decimal('0.00'))

    essay_question_ids = set(
        attempt.exam.questions.filter(type=ExamQuestionType.ESSAY, deleted_at__isnull=True).values_list('id', flat=True)
    )
    ungraded_essays = essay_question_ids - set(
        attempt.answers.filter(question_id__in=essay_question_ids, points_awarded__isnull=False).values_list('question_id', flat=True)
    )

    attempt.auto_score = auto_score
    attempt.submitted_at = timezone.now()
    attempt.status = ExamAttemptStatus.AUTO_SUBMITTED if auto else ExamAttemptStatus.SUBMITTED
    attempt.final_score = None if ungraded_essays else (auto_score + attempt.manual_score)
    attempt.save()

    audit(
        action='academic.exam_attempt.submitted',
        entity_type='ExamAttempt',
        entity_id=attempt.id,
        foundation_id=attempt.foundation_id,
        diff={'auto_score': str(auto_score), 'auto': auto},
    )
    return attempt


def auto_submit_if_expired(attempt: ExamAttempt) -> ExamAttempt:
    """Checked on read/write paths: past the exam window while still IN_PROGRESS -> AUTO_SUBMIT."""
    if attempt.status == ExamAttemptStatus.IN_PROGRESS and compute_remaining_seconds(attempt) <= 0:
        return submit_attempt(attempt, auto=True)
    return attempt


def auto_submit_expired_attempts(foundation_id: int = None) -> dict:
    """ACD-025/ACD-026: batch counterpart to auto_submit_if_expired.

    auto_submit_if_expired only fires when a view happens to touch that exact
    ExamAttempt again after its window closes — a student who never reopens
    the exam URL once time runs out would otherwise sit IN_PROGRESS
    indefinitely, with no mechanism to ever auto-score them. This is what
    actually makes spec/04 §9's acceptance criterion ("a 40-question MCQ exam
    for 300 students auto-scores within 60 seconds of window close")
    achievable: run this every minute (see deploy/crontab) and every attempt
    is force-submitted within one cron tick of its window closing, regardless
    of whether any student ever revisits it.
    """
    attempts = ExamAttempt.all_tenants.filter(
        status=ExamAttemptStatus.IN_PROGRESS,
        exam__window_end__lt=timezone.now(),
        deleted_at__isnull=True,
    )
    if foundation_id:
        attempts = attempts.filter(foundation_id=foundation_id)

    submitted = 0
    for attempt in attempts:
        submit_attempt(attempt, auto=True)
        submitted += 1
    return {'submitted': submitted}


def grade_essay_answer(exam_answer: ExamAnswer, points, actor=None) -> ExamAnswer:
    """Manual grading for ESSAY answers; recomputes the attempt's final score once complete."""
    exam_answer.points_awarded = points
    exam_answer.graded_by = actor
    exam_answer.save()

    attempt = exam_answer.attempt
    essay_question_ids = set(
        attempt.exam.questions.filter(type=ExamQuestionType.ESSAY, deleted_at__isnull=True).values_list('id', flat=True)
    )
    ungraded_essays = essay_question_ids - set(
        attempt.answers.filter(question_id__in=essay_question_ids, points_awarded__isnull=False).values_list('question_id', flat=True)
    )
    if not ungraded_essays and attempt.status != ExamAttemptStatus.IN_PROGRESS:
        manual_score = sum(
            (a.points_awarded for a in attempt.answers.filter(question_id__in=essay_question_ids, points_awarded__isnull=False)),
            Decimal('0.00'),
        )
        attempt.manual_score = manual_score
        attempt.final_score = attempt.auto_score + manual_score
        attempt.save(update_fields=['manual_score', 'final_score', 'updated_at'])

    audit(
        action='academic.exam_answer.graded',
        entity_type='ExamAnswer',
        entity_id=exam_answer.id,
        foundation_id=exam_answer.foundation_id,
        diff={'points_awarded': str(points)},
    )
    return exam_answer


def record_focus_loss(attempt: ExamAttempt) -> ExamAttempt:
    """ACD-024: record a focus-loss event; never auto-punished."""
    attempt.focus_loss_count += 1
    attempt.save(update_fields=['focus_loss_count', 'updated_at'])
    return attempt


def assign_substitution(slot, date, substitute_teacher, reason='') -> TimetableSubstitution:
    """ACD-019: assign a single-date substitute for a timetable slot."""
    original_teacher = slot.class_subject.teacher
    substitution, created = TimetableSubstitution.objects.update_or_create(
        foundation_id=slot.foundation_id,
        slot=slot,
        date=date,
        defaults={
            'original_teacher': original_teacher,
            'substitute_teacher': substitute_teacher,
            'reason': reason,
            'status': SubstitutionStatus.PENDING,
            'decline_reason': '',
            'responded_at': None,
        },
    )
    audit(
        action='academic.timetable_substitution.assigned',
        entity_type='TimetableSubstitution',
        entity_id=substitution.id,
        foundation_id=slot.foundation_id,
        diff={
            'slot': str(slot),
            'date': str(date),
            'original_teacher': str(original_teacher),
            'substitute_teacher': str(substitute_teacher),
        },
    )

    # ACD-019: notify the substitute. A notification failure must never block the
    # substitution assignment itself — same guard as every other cross-app side effect.
    try:
        from apps.notifications.models import NotificationCategory
        from apps.notifications.services import dispatch_intent

        dispatch_intent(
            foundation_id=slot.foundation_id,
            category=NotificationCategory.SUBSTITUTE_ASSIGNED,
            template_key='academic.substitution.assigned',
            payload={
                'type': NotificationCategory.SUBSTITUTE_ASSIGNED,
                'substitution_id': substitution.id,
                'slot_id': slot.id,
                'class_group': slot.class_group.name,
                'subject': slot.class_subject.subject.name,
                'date': str(date),
                'period_no': str(slot.period_no),
                'original_teacher': original_teacher.person.full_name,
            },
            school_id=slot.class_group.school_id,
            recipient_user=substitute_teacher.user,
            recipient_phone=getattr(substitute_teacher.user, 'phone_e164', ''),
            recipient_email=getattr(substitute_teacher.user, 'email', ''),
            recipient_name=substitute_teacher.person.full_name,
            dedupe_key=f"substitution_assigned:{substitution.id}",
        )
    except Exception as exc:
        logger.warning(f"Error notifying substitute teacher for TimetableSubstitution #{substitution.id}: {exc}")

    return substitution


def accept_substitution(substitution: TimetableSubstitution, actor=None) -> TimetableSubstitution:
    """ACD-019/TCH-015: Accept a pending timetable substitution."""
    substitution.status = SubstitutionStatus.ACCEPTED
    substitution.responded_at = timezone.now()
    substitution.save(update_fields=['status', 'responded_at', 'updated_at'])

    audit(
        action='academic.timetable_substitution.accepted',
        entity_type='TimetableSubstitution',
        entity_id=substitution.id,
        foundation_id=substitution.foundation_id,
        diff={
            'slot_id': substitution.slot_id,
            'date': str(substitution.date),
            'substitute_teacher_id': substitution.substitute_teacher_id,
            'status': substitution.status,
        },
    )
    return substitution


def decline_substitution(substitution: TimetableSubstitution, reason: str, actor=None) -> TimetableSubstitution:
    """ACD-019/TCH-015: Decline a pending timetable substitution with mandatory reason,
    notifying the original teacher / admin.
    """
    cleaned_reason = (reason or '').strip()
    if not cleaned_reason:
        raise SubstitutionDeclineReasonRequiredError(_("Alasan penolakan wajib diisi."))

    substitution.status = SubstitutionStatus.DECLINED
    substitution.decline_reason = cleaned_reason
    substitution.responded_at = timezone.now()
    substitution.save(update_fields=['status', 'decline_reason', 'responded_at', 'updated_at'])

    audit(
        action='academic.timetable_substitution.declined',
        entity_type='TimetableSubstitution',
        entity_id=substitution.id,
        foundation_id=substitution.foundation_id,
        diff={
            'slot_id': substitution.slot_id,
            'date': str(substitution.date),
            'substitute_teacher_id': substitution.substitute_teacher_id,
            'decline_reason': cleaned_reason,
            'status': substitution.status,
        },
    )

    try:
        from apps.notifications.models import NotificationCategory
        from apps.notifications.services import dispatch_intent

        original_teacher = substitution.original_teacher
        substitute_teacher = substitution.substitute_teacher
        slot = substitution.slot
        dispatch_intent(
            foundation_id=substitution.foundation_id,
            category=NotificationCategory.SUBSTITUTE_DECLINED,
            template_key='academic.substitution.declined',
            payload={
                'class_group': slot.class_subject.class_group.name,
                'subject': slot.class_subject.subject.name,
                'date': str(substitution.date),
                'period_no': str(slot.period_no),
                'substitute_teacher': substitute_teacher.person.full_name,
                'reason': cleaned_reason,
            },
            school_id=slot.class_subject.class_group.school_id,
            recipient_user=original_teacher.user,
            recipient_phone=getattr(original_teacher.user, 'phone_e164', ''),
            recipient_email=getattr(original_teacher.user, 'email', ''),
            recipient_name=original_teacher.person.full_name,
            dedupe_key=f"substitution_declined:{substitution.id}",
        )
    except Exception as exc:
        logger.warning(f"Error notifying teacher for declined TimetableSubstitution #{substitution.id}: {exc}")

    return substitution


def get_effective_teacher_for_slot(slot, date):
    """ACD-020: the teacher authorized to teach/submit attendance for this slot on this date —
    the date's substitute if one is assigned and not declined, otherwise the slot's regular teacher.
    """
    substitution = TimetableSubstitution.objects.filter(
        foundation_id=slot.foundation_id, slot=slot, date=date, deleted_at__isnull=True,
    ).exclude(status=SubstitutionStatus.DECLINED).first()
    if substitution:
        return substitution.substitute_teacher
    return slot.class_subject.teacher


def get_expected_periods_for_school(school, date, missing_only=False) -> list:
    """ACD-020 (school-level rollup): every TimetableSlot for this school scheduled on
    `date`'s weekday, across all class groups — not just one teacher's agenda — with
    the effective (substitution-aware) teacher and whether attendance was submitted.

    A live query rather than a cron/persisted model: a school has at most a few hundred
    slots/day, so joining TimetableSlot x PeriodAttendance on request is cheap, and
    nothing here needs to happen proactively at a fixed time (that would justify a cron).
    """
    from apps.attendance.models import PeriodAttendance

    weekday = date.isoweekday()
    slots = TimetableSlot.objects.filter(
        foundation_id=school.foundation_id,
        class_subject__class_group__school=school,
        day_of_week=weekday,
        deleted_at__isnull=True,
    ).select_related('class_subject__class_group', 'class_subject__subject', 'class_subject__teacher')

    slot_ids = [s.id for s in slots]
    substitutions_by_slot_id = {
        s.slot_id: s for s in TimetableSubstitution.objects.filter(
            foundation_id=school.foundation_id, slot_id__in=slot_ids, date=date, deleted_at__isnull=True,
        ).exclude(status=SubstitutionStatus.DECLINED)
    }
    submitted_slot_ids = set(
        PeriodAttendance.objects.filter(
            foundation_id=school.foundation_id, slot_id__in=slot_ids, date=date,
        ).values_list('slot_id', flat=True).distinct()
    )

    # Check for active AcademicCalendarEvents affecting attendance on this date
    from datetime import datetime, time
    from apps.academic.models import AcademicCalendarEvent
    tz = timezone.get_current_timezone()
    day_start = timezone.make_aware(datetime.combine(date, time.min), tz)
    day_end = timezone.make_aware(datetime.combine(date, time.max), tz)

    cal_events = list(AcademicCalendarEvent.objects.filter(
        foundation_id=school.foundation_id,
        school=school,
        affects_attendance=True,
        start_at__lte=day_end,
        end_at__gte=day_start,
        deleted_at__isnull=True,
    ).prefetch_related('class_groups'))

    def _find_exemption(slot_obj):
        for ev in cal_events:
            ev_cgroups = set(ev.class_groups.values_list('id', flat=True))
            if ev_cgroups and slot_obj.class_subject.class_group_id not in ev_cgroups:
                continue
            if ev.is_all_day:
                return ev
            s_start = timezone.make_aware(datetime.combine(date, slot_obj.start_time), tz)
            s_end = timezone.make_aware(datetime.combine(date, slot_obj.end_time), tz)
            if s_start < ev.end_at and s_end > ev.start_at:
                return ev
        return None

    expected = []
    for slot in slots:
        attendance_submitted = slot.id in submitted_slot_ids
        ev = _find_exemption(slot)
        is_exempt = ev is not None
        if missing_only and (attendance_submitted or is_exempt):
            continue
        sub = substitutions_by_slot_id.get(slot.id)
        teacher = sub.substitute_teacher if sub else slot.class_subject.teacher
        expected.append({
            'slot_id': slot.id,
            'period_no': slot.period_no,
            'class_group': slot.class_subject.class_group.name,
            'subject': slot.class_subject.subject.name,
            'teacher_id': teacher.id,
            'teacher_name': teacher.person.full_name,
            'is_substitution': sub is not None,
            'attendance_submitted': attendance_submitted,
            'is_exempt': is_exempt,
            'exemption_reason': ev.title if ev else None,
            'calendar_event_id': ev.id if ev else None,
        })

    expected.sort(key=lambda e: (e['class_group'], e['period_no']))
    return expected


def generate_report_cards(class_group, term, triggered_by=None) -> dict:
    """ACD-010: term-scoped batch generation per class. Idempotent while a card is still DRAFT."""
    from apps.attendance.models import AttendanceDay

    class_subjects = list(ClassSubject.objects.filter(class_group=class_group, term=term, deleted_at__isnull=True))
    student_ids = ClassEnrollment.objects.filter(
        class_group=class_group, is_active=True, deleted_at__isnull=True,
    ).values_list('student_id', flat=True)

    created, updated, skipped = 0, 0, 0

    for student in Student.objects.filter(id__in=student_ids, foundation_id=class_group.foundation_id):
        grades_snapshot = []
        for cs in class_subjects:
            try:
                result = compute_term_grade(student, cs)
            except WeightConfigError:
                result = {'status': 'INCOMPLETE', 'grade': None, 'missing_assessments': [], 'formula': None}
            grades_snapshot.append({'subject': cs.subject.name, 'subject_code': cs.subject.code, **result})

        attendance_qs = AttendanceDay.objects.filter(
            foundation_id=class_group.foundation_id,
            student=student,
            date__gte=term.start_date,
            date__lte=term.end_date,
            deleted_at__isnull=True,
        )
        attendance_summary = dict(Counter(attendance_qs.values_list('status', flat=True)))

        existing = ReportCard.objects.filter(
            student=student, term=term, is_current=True, deleted_at__isnull=True,
        ).first()

        if existing and existing.status != ReportCardStatus.DRAFT:
            skipped += 1
            continue

        if existing:
            existing.grades_snapshot = grades_snapshot
            existing.attendance_summary = attendance_summary
            existing.save(update_fields=['grades_snapshot', 'attendance_summary', 'updated_at'])
            updated += 1
        else:
            ReportCard.objects.create(
                foundation_id=class_group.foundation_id,
                student=student,
                term=term,
                class_group=class_group,
                status=ReportCardStatus.DRAFT,
                grades_snapshot=grades_snapshot,
                attendance_summary=attendance_summary,
            )
            created += 1

    audit(
        action='academic.report_card.generated',
        entity_type='ClassGroup',
        entity_id=class_group.id,
        foundation_id=class_group.foundation_id,
        diff={'term': term.name, 'created': created, 'updated': updated, 'skipped': skipped},
    )
    return {'created': created, 'updated': updated, 'skipped': skipped}


def approve_report_card(report_card: ReportCard, actor=None) -> ReportCard:
    """ACD-012: DRAFT/PENDING_REVIEW -> APPROVED. Restricted to school_admin/principal at the view layer."""
    if report_card.status not in (ReportCardStatus.DRAFT, ReportCardStatus.PENDING_REVIEW):
        raise ReportCardStateError(f"INVALID_TRANSITION: cannot approve a report card in status {report_card.status}.")

    report_card.status = ReportCardStatus.APPROVED
    report_card.approved_by = actor
    report_card.approved_at = timezone.now()
    report_card.save()
    audit(
        action='academic.report_card.approved',
        entity_type='ReportCard',
        entity_id=report_card.id,
        foundation_id=report_card.foundation_id,
    )
    return report_card


def set_report_card_content(
    report_card: ReportCard,
    narrative: str = None,
    extracurricular_notes: list = None,
    promotion_decision: str = None,
    subject_narratives: dict = None,
    actor=None,
) -> ReportCard:
    """ACD-011: edit a report card's narrative, extracurricular notes, per-subject
    objective narrative, and promotion decision before publication.

    All arguments are optional — only provided fields are changed. Only allowed
    while DRAFT/PENDING_REVIEW (ACD-016: a card is immutable once PUBLISHED).
    subject_narratives maps subject_code -> narrative text, merged into the
    matching grades_snapshot item's 'objective_narrative' key.
    """
    if report_card.status not in (ReportCardStatus.DRAFT, ReportCardStatus.PENDING_REVIEW):
        raise ReportCardStateError(
            f"INVALID_TRANSITION: cannot edit content of a report card in status {report_card.status}."
        )

    update_fields = ['updated_at']
    if narrative is not None:
        report_card.narrative = narrative
        update_fields.append('narrative')
    if extracurricular_notes is not None:
        report_card.extracurricular_notes = extracurricular_notes
        update_fields.append('extracurricular_notes')
    if promotion_decision is not None:
        report_card.promotion_decision = promotion_decision
        update_fields.append('promotion_decision')
    if subject_narratives:
        snapshot = report_card.grades_snapshot
        for item in snapshot:
            if item.get('subject_code') in subject_narratives:
                item['objective_narrative'] = subject_narratives[item['subject_code']]
        report_card.grades_snapshot = snapshot
        update_fields.append('grades_snapshot')

    report_card.save(update_fields=update_fields)
    audit(
        action='academic.report_card.content_updated',
        entity_type='ReportCard',
        entity_id=report_card.id,
        foundation_id=report_card.foundation_id,
        diff={
            'narrative_changed': narrative is not None,
            'extracurricular_changed': extracurricular_notes is not None,
            'promotion_decision_changed': promotion_decision is not None,
            'subject_narratives_changed': list(subject_narratives) if subject_narratives else [],
        },
    )
    return report_card


CURRICULUM_PHASE_BY_MAX_GRADE = (
    (2, 'A'), (4, 'B'), (6, 'C'), (9, 'D'), (10, 'E'), (12, 'F'),
)


def get_curriculum_phase(grade_level: int) -> str:
    """Kurikulum Merdeka's standard phase-letter mapping (spec/04 §1): Fase A (grade 1-2)
    through Fase F (grade 11-12). Ministry-defined, not a school-configurable value."""
    for max_grade, phase in CURRICULUM_PHASE_BY_MAX_GRADE:
        if grade_level <= max_grade:
            return phase
    return CURRICULUM_PHASE_BY_MAX_GRADE[-1][1]


def _get_principal_staff(school) -> Staff | None:
    """Resolves the school's principal as whichever Staff currently holds the
    school_admin role at that school's scope — no separate 'principal' field
    to keep in sync; the role assignment IS the source of truth."""
    assignment = RoleAssignment.all_tenants.filter(
        foundation_id=school.foundation_id,
        role=RoleAssignment.ROLE_SCHOOL_ADMIN,
        scope_type=RoleAssignment.SCOPE_SCHOOL,
        scope_id=school.id,
        deleted_at__isnull=True,
    ).first()
    if not assignment:
        return None
    return Staff.all_tenants.filter(
        foundation_id=school.foundation_id, user=assignment.user, school=school, deleted_at__isnull=True,
    ).first()


_DESCRIPTOR_COLOR = {
    'Sangat Baik': '#0E7A4F',
    'Baik': '#3A302C',
    'Cukup': '#B56A00',
    'Perlu Bimbingan': '#B3261E',
}

_ATTENDANCE_LABELS = (
    ('SAKIT', 'Sakit', '#1B5FA8'),
    ('IZIN', 'Izin', '#6B615C'),
    ('ALPA', 'Tanpa keterangan', '#B3261E'),
)


def render_report_card_html(report_card: ReportCard) -> str:
    """Branded rapor layout (ACD-010, ACD-011) — see memory/01_PROJECT.md retrospective
    on TASK-064 for the design source and the deliberate omission of the P5 profile
    section (no ACD-011 requirement, no real data source).

    A real <table> (not a flex/grid row layout) renders section A so weasyprint
    repeats <thead> across pages for classes with more subjects than fit on one
    sheet; section C onward is forced onto a fresh page via page-break-before,
    matching the design's fixed second page regardless of subject count.
    """
    esc = html.escape
    student = report_card.student
    class_group = report_card.class_group
    school = class_group.school
    term = report_card.term
    foundation = Foundation.objects.get(id=school.foundation_id)

    semester_label = 'Semester Ganjil' if term.term_no == 1 else 'Semester Genap'
    phase = get_curriculum_phase(class_group.grade_level)
    homeroom = class_group.homeroom_teacher
    principal = _get_principal_staff(school)

    numeric_grades = [g['grade'] for g in report_card.grades_snapshot if g.get('grade') is not None]
    average = (sum(Decimal(str(g)) for g in numeric_grades) / len(numeric_grades)) if numeric_grades else None
    average_label = f"{average:.1f}".replace('.', ',') if average is not None else '-'
    average_descriptor = compute_descriptor(average, Decimal('100')) if average is not None else '-'

    subject_rows = []
    for i, g in enumerate(report_card.grades_snapshot, start=1):
        grade = g.get('grade')
        grade_label = f"{grade:.0f}" if grade is not None else '-'
        descriptor = compute_descriptor(Decimal(str(grade)), Decimal('100')) if grade is not None else '-'
        color = _DESCRIPTOR_COLOR.get(descriptor, '#3A302C')
        narrative = esc(g.get('objective_narrative') or '-')
        subject_rows.append(f"""
      <tr>
        <td style="padding:5px 5px;border-top:1px solid #E5DDD9;font-family:'IBM Plex Mono',monospace;font-size:9pt;text-align:center;color:#6B615C">{i}</td>
        <td style="padding:5px 8px;border-top:1px solid #E5DDD9;font-size:9.5pt;font-weight:500">{esc(g.get('subject', ''))}</td>
        <td style="padding:5px 5px;border-top:1px solid #E5DDD9;font-family:'IBM Plex Mono',monospace;font-size:10pt;font-weight:600;text-align:center">{grade_label}</td>
        <td style="padding:5px 6px;border-top:1px solid #E5DDD9;font-size:9pt;color:{color};font-weight:600">{descriptor}</td>
        <td style="padding:5px 8px;border-top:1px solid #E5DDD9;font-size:9pt;line-height:1.4;color:#3A302C">{narrative}</td>
      </tr>""")

    extracurricular_rows = "".join(
        f"""<div style="padding:7px 10px;border-top:1px solid #E5DDD9;font-size:9pt">{esc(e.get('name', ''))}</div>
        <div style="padding:5px 8px;border-top:1px solid #E5DDD9;font-size:9pt;font-weight:600">{esc(e.get('grade', ''))}</div>"""
        for e in report_card.extracurricular_notes
    ) or '<div style="padding:7px 10px;border-top:1px solid #E5DDD9;font-size:9pt;color:#6B615C;grid-column:1 / -1">Tidak ada catatan ekstrakurikuler.</div>'

    attendance_summary = report_card.attendance_summary
    total_days = sum(attendance_summary.values())
    hadir_days = total_days - sum(attendance_summary.get(k, 0) for k, _, _ in _ATTENDANCE_LABELS)
    attendance_rows = "".join(f"""
        <div style="padding:9px 12px;display:flex;justify-content:space-between;align-items:baseline;border-bottom:1px solid #F0EAE7">
          <div style="display:flex;align-items:center;gap:7px"><span style="width:7px;height:7px;background:{color};border-radius:50%;flex:none"></span><span style="font-size:9.5pt">{label}</span></div>
          <span style="font-family:'IBM Plex Mono',monospace;font-size:10pt;font-weight:600">{attendance_summary.get(key, 0)} hari</span>
        </div>""" for key, label, color in _ATTENDANCE_LABELS)

    homeroom_name = esc(homeroom.person.full_name) if homeroom else '(...............................)'
    homeroom_nip = f'<span style="font-family:\'IBM Plex Mono\',monospace;font-size:8pt;color:#6B615C">NIP {esc(homeroom.nip)}</span>' if homeroom and homeroom.nip else ''
    principal_name = esc(principal.person.full_name) if principal else '(...............................)'
    principal_nip = f'<span style="font-family:\'IBM Plex Mono\',monospace;font-size:8pt;color:#6B615C">NIP {esc(principal.nip)}</span>' if principal and principal.nip else ''
    pub_dt = report_card.published_at or timezone.now()
    issue_date = f"{pub_dt.day} {pub_dt.strftime('%B %Y')}"
    issue_date_short = pub_dt.strftime('%Y-%m-%d')
    decision = esc(report_card.promotion_decision) if report_card.promotion_decision else 'BELUM DITENTUKAN'
    narrative_text = esc(report_card.narrative) if report_card.narrative else '-'

    return f"""<html>
<head>
<meta charset="utf-8">
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@600;700;800&family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet" />
<style>
@page {{ size: A4; margin: 0; }}
body {{ margin: 0; font-family: 'IBM Plex Sans', sans-serif; color: #16110F; }}
* {{ box-sizing: border-box; }}
table {{ border-collapse: collapse; width: 100%; }}
.page {{ width: 210mm; min-height: 297mm; padding: 14mm 13mm; box-sizing: border-box; }}
.page + .page {{ page-break-before: always; }}
</style>
</head>
<body>

<div class="page">
  <div style="display:flex;gap:16px;align-items:flex-start;border-bottom:3px solid #C8102E;padding-bottom:12px">
    <div style="width:56px;height:56px;border:1px solid #E5DDD9;display:flex;align-items:center;justify-content:center;flex:none;background:#F7F4F2">
      <span style="font-family:'IBM Plex Mono',monospace;font-size:8pt;color:#6B615C;text-align:center;line-height:1.2">LOGO<br />YAYASAN</span>
    </div>
    <div style="flex:1;display:flex;flex-direction:column;gap:2px;padding-top:2px">
      <span style="font-family:'IBM Plex Mono',monospace;font-size:9pt;letter-spacing:.14em;text-transform:uppercase;color:#6B615C">{esc(foundation.brand_name)}</span>
      <h1 style="margin:0;font-family:'Plus Jakarta Sans',sans-serif;font-weight:800;font-size:16pt;line-height:1.15;letter-spacing:-.01em">{esc(school.name)}</h1>
      <span style="font-size:9pt;line-height:1.35;color:#3A302C">NPSN {esc(school.npsn)}</span>
    </div>
    <div style="display:flex;flex-direction:column;gap:2px;align-items:flex-end;flex:none;padding-top:2px">
      <span style="font-family:'IBM Plex Mono',monospace;font-size:9pt;letter-spacing:.12em;color:#6B615C">RAPOR PESERTA DIDIK</span>
      <span style="font-size:10pt;font-weight:600">{semester_label}</span>
      <span style="font-family:'IBM Plex Mono',monospace;font-size:9pt">{esc(term.academic_year.name)}</span>
      <span style="font-family:'IBM Plex Mono',monospace;font-size:8pt;color:#6B615C">{esc(school.curriculum.replace('_', ' ').title())}</span>
    </div>
  </div>

  <div style="display:grid;grid-template-columns:1fr 1fr;gap:0 28px;padding:12px 0;border-bottom:1px solid #E5DDD9">
    <div style="display:flex;flex-direction:column;gap:5px">
      <div style="display:grid;grid-template-columns:82px 1fr;gap:8px"><span style="font-size:9pt;color:#6B615C">Nama</span><span style="font-size:10pt;font-weight:600">{esc(student.person.full_name)}</span></div>
      <div style="display:grid;grid-template-columns:82px 1fr;gap:8px"><span style="font-size:9pt;color:#6B615C">NISN</span><span style="font-family:'IBM Plex Mono',monospace;font-size:9.5pt">{esc(student.nisn or '-')}</span></div>
      <div style="display:grid;grid-template-columns:82px 1fr;gap:8px"><span style="font-size:9pt;color:#6B615C">NIS</span><span style="font-family:'IBM Plex Mono',monospace;font-size:9.5pt">{esc(student.nis or '-')}</span></div>
    </div>
    <div style="display:flex;flex-direction:column;gap:5px">
      <div style="display:grid;grid-template-columns:82px 1fr;gap:8px"><span style="font-size:9pt;color:#6B615C">Kelas</span><span style="font-size:10pt;font-weight:600">{esc(class_group.name)}</span></div>
      <div style="display:grid;grid-template-columns:82px 1fr;gap:8px"><span style="font-size:9pt;color:#6B615C">Fase</span><span style="font-size:9.5pt">{phase}</span></div>
      <div style="display:grid;grid-template-columns:82px 1fr;gap:8px"><span style="font-size:9pt;color:#6B615C">Wali Kelas</span><span style="font-size:9.5pt">{homeroom_name}</span></div>
    </div>
  </div>

  <div style="padding-top:14px;display:flex;flex-direction:column;gap:8px">
    <div style="display:flex;justify-content:space-between;align-items:baseline">
      <h2 style="margin:0;font-family:'Plus Jakarta Sans',sans-serif;font-weight:700;font-size:12pt;letter-spacing:-.01em">A. Capaian Hasil Belajar</h2>
      <span style="font-size:9pt;color:#6B615C">Nilai akhir = rerata tertimbang per kategori asesmen</span>
    </div>

    <table style="border:1px solid #16110F">
      <thead>
        <tr>
          <th style="padding:5px 5px;background:#16110F;color:#fff;font-family:'IBM Plex Mono',monospace;font-size:8pt;letter-spacing:.08em;text-align:center;width:20px">NO</th>
          <th style="padding:5px 8px;background:#16110F;color:#fff;font-family:'IBM Plex Mono',monospace;font-size:8pt;letter-spacing:.08em;text-align:left">MATA PELAJARAN</th>
          <th style="padding:5px 5px;background:#16110F;color:#fff;font-family:'IBM Plex Mono',monospace;font-size:8pt;letter-spacing:.08em;text-align:center;width:42px">NILAI</th>
          <th style="padding:5px 6px;background:#16110F;color:#fff;font-family:'IBM Plex Mono',monospace;font-size:8pt;letter-spacing:.08em;text-align:left;width:74px">PREDIKAT</th>
          <th style="padding:5px 8px;background:#16110F;color:#fff;font-family:'IBM Plex Mono',monospace;font-size:8pt;letter-spacing:.08em;text-align:left">CAPAIAN KOMPETENSI</th>
        </tr>
      </thead>
      <tbody>{''.join(subject_rows)}
        <tr>
          <td style="padding:8px 5px;border-top:2px solid #16110F;background:#F7F4F2"></td>
          <td style="padding:8px;border-top:2px solid #16110F;background:#F7F4F2;font-size:9.5pt;font-weight:700">Rata-rata</td>
          <td style="padding:8px 5px;border-top:2px solid #16110F;background:#F7F4F2;font-family:'IBM Plex Mono',monospace;font-size:11pt;font-weight:700;text-align:center">{average_label}</td>
          <td style="padding:8px 6px;border-top:2px solid #16110F;background:#F7F4F2;font-size:9pt;font-weight:600">{average_descriptor}</td>
          <td style="padding:8px;border-top:2px solid #16110F;background:#F7F4F2"></td>
        </tr>
      </tbody>
    </table>
  </div>

  <div style="margin-top:24px;padding-top:14px;display:flex;justify-content:space-between;align-items:flex-end;border-top:1px solid #E5DDD9">
    <span style="font-family:'IBM Plex Mono',monospace;font-size:7.5pt;color:#6B615C">Dokumen diterbitkan sistem EduCore · Rev. {report_card.version} · {issue_date_short} · NISN {esc(student.nisn or '-')}</span>
  </div>
</div>

<div class="page">
  <div style="display:flex;justify-content:space-between;align-items:baseline;border-bottom:1px solid #E5DDD9;padding-bottom:8px">
    <span style="font-family:'IBM Plex Mono',monospace;font-size:9pt;letter-spacing:.12em;color:#6B615C">RAPOR · {esc(student.person.full_name.upper())} · {esc(class_group.name)} · {semester_label.upper()} {esc(term.academic_year.name)}</span>
    <span style="font-family:'IBM Plex Mono',monospace;font-size:9pt;color:#6B615C">NISN {esc(student.nisn or '-')}</span>
  </div>

  <div style="display:flex;gap:16px;flex-wrap:wrap;padding-top:10px">
    <span style="font-family:'IBM Plex Mono',monospace;font-size:8pt;color:#6B615C">PREDIKAT</span>
    <span style="font-size:9pt;color:#0E7A4F"><strong>Sangat Baik</strong> ≥ 90</span>
    <span style="font-size:9pt;color:#3A302C"><strong>Baik</strong> 80–89</span>
    <span style="font-size:9pt;color:#B56A00"><strong>Cukup</strong> 70–79</span>
    <span style="font-size:9pt;color:#B3261E"><strong>Perlu Bimbingan</strong> &lt; 70</span>
  </div>

  <div style="display:grid;grid-template-columns:1.25fr 1fr;gap:18px;padding-top:12px">
    <div style="display:flex;flex-direction:column;gap:8px">
      <h2 style="margin:0;font-family:'Plus Jakarta Sans',sans-serif;font-weight:700;font-size:12pt;letter-spacing:-.01em">B. Ekstrakurikuler</h2>
      <div style="border:1px solid #E5DDD9;display:grid;grid-template-columns:1fr 60px">
        <div style="padding:6px 10px;background:#16110F;color:#fff;font-family:'IBM Plex Mono',monospace;font-size:8pt;letter-spacing:.08em">KEGIATAN</div>
        <div style="padding:5px 8px;background:#16110F;color:#fff;font-family:'IBM Plex Mono',monospace;font-size:8pt;letter-spacing:.08em">NILAI</div>
        {extracurricular_rows}
      </div>
    </div>

    <div style="display:flex;flex-direction:column;gap:8px">
      <h2 style="margin:0;font-family:'Plus Jakarta Sans',sans-serif;font-weight:700;font-size:12pt;letter-spacing:-.01em">C. Kehadiran</h2>
      <div style="border:1px solid #E5DDD9;display:flex;flex-direction:column">
        {attendance_rows}
        <div style="padding:9px 12px;display:flex;justify-content:space-between;align-items:baseline;background:#F7F4F2">
          <span style="font-size:9.5pt;font-weight:700">Hadir</span>
          <span style="font-family:'IBM Plex Mono',monospace;font-size:10.5pt;font-weight:700;color:#0E7A4F">{hadir_days} dari {total_days}</span>
        </div>
      </div>
    </div>
  </div>

  <div style="padding-top:14px">
    <h2 style="margin:0 0 8px;font-family:'Plus Jakarta Sans',sans-serif;font-weight:700;font-size:12pt;letter-spacing:-.01em">D. Catatan Wali Kelas</h2>
    <div style="border:1px solid #E5DDD9;padding:11px 12px;min-height:80px">
      <p style="margin:0;font-size:9.5pt;line-height:1.6;color:#16110F">{narrative_text}</p>
    </div>
  </div>

  <div style="padding-top:16px">
    <h2 style="margin:0 0 8px;font-family:'Plus Jakarta Sans',sans-serif;font-weight:700;font-size:12pt;letter-spacing:-.01em">E. Keputusan</h2>
    <div style="border:1px solid #16110F;padding:12px 14px;display:flex;justify-content:space-between;align-items:center;background:#F7F4F2">
      <span style="font-size:10pt">Berdasarkan pencapaian kompetensi, peserta didik dinyatakan:</span>
      <span style="font-family:'Plus Jakarta Sans',sans-serif;font-weight:800;font-size:13pt;letter-spacing:-.01em">{decision}</span>
    </div>
  </div>

  <div style="padding-top:24px;display:grid;grid-template-columns:repeat(3,1fr);gap:16px">
    <div style="display:flex;flex-direction:column;gap:2px;align-items:center">
      <span style="font-size:9pt;color:#3A302C">Orang Tua / Wali</span>
      <div style="height:52px"></div>
      <div style="width:100%;border-top:1px solid #16110F;padding-top:4px;text-align:center"><span style="font-size:9pt;color:#6B615C">(...............................)</span></div>
    </div>
    <div style="display:flex;flex-direction:column;gap:2px;align-items:center">
      <span style="font-size:9pt;color:#3A302C">Wali Kelas</span>
      <div style="height:52px"></div>
      <div style="width:100%;border-top:1px solid #16110F;padding-top:4px;text-align:center;display:flex;flex-direction:column;gap:1px"><span style="font-size:9pt;font-weight:600">{homeroom_name}</span>{homeroom_nip}</div>
    </div>
    <div style="display:flex;flex-direction:column;gap:2px;align-items:center">
      <span style="font-size:9pt;color:#3A302C">{issue_date}<br />Kepala Sekolah</span>
      <div style="height:38px"></div>
      <div style="width:100%;border-top:1px solid #16110F;padding-top:4px;text-align:center;display:flex;flex-direction:column;gap:1px"><span style="font-size:9pt;font-weight:600">{principal_name}</span>{principal_nip}</div>
    </div>
  </div>

  <div style="padding-top:12px;display:flex;justify-content:space-between;align-items:flex-end;border-top:1px solid #E5DDD9;margin-top:12px">
    <span style="font-family:'IBM Plex Mono',monospace;font-size:7.5pt;color:#6B615C">Dokumen diterbitkan sistem EduCore · Rev. {report_card.version} · {issue_date_short} · Tanpa tanda tangan basah dokumen ini tetap sah sebagai terbitan sistem</span>
  </div>
</div>

</body>
</html>"""


def render_report_card_pdf(report_card: ReportCard) -> str:
    """Render and persist the report card document. Falls back to HTML if weasyprint's
    native libraries are unavailable in this environment (see memory/01_PROJECT.md).
    Written to GCS via core.write_generated_file, catalogued as a core.StoredFile
    row (ARC-026, ARC-030) — no raw filesystem write.
    """
    html_content = render_report_card_html(report_card)

    try:
        from weasyprint import HTML
        filename = f"{report_card.id}_v{report_card.version}.pdf"
        data = HTML(string=html_content).write_pdf()
        content_type = 'application/pdf'
    except Exception:
        logger.warning("weasyprint unavailable, falling back to HTML rapor output", exc_info=True)
        filename = f"{report_card.id}_v{report_card.version}.html"
        data = html_content.encode('utf-8')
        content_type = 'text/html'

    key = storage.build_deterministic_object_key('report_card_pdf', filename)
    stored_file = write_generated_file(
        purpose='report_card_pdf', filename=filename, data=data, key=key,
        content_type=content_type, foundation_id=report_card.foundation_id,
    )
    return stored_file.key


def publish_report_card(report_card: ReportCard, actor=None) -> ReportCard:
    """ACD-012/ACD-016: APPROVED -> PUBLISHED, rendering an immutable document."""
    if report_card.status != ReportCardStatus.APPROVED:
        raise ReportCardStateError(f"INVALID_TRANSITION: cannot publish a report card in status {report_card.status}.")

    report_card.pdf_key = render_report_card_pdf(report_card)
    report_card.status = ReportCardStatus.PUBLISHED
    report_card.published_at = timezone.now()
    report_card.save()
    audit(
        action='academic.report_card.published',
        entity_type='ReportCard',
        entity_id=report_card.id,
        foundation_id=report_card.foundation_id,
        diff={'pdf_key': report_card.pdf_key},
    )
    return report_card


def revise_report_card(report_card: ReportCard, actor=None) -> ReportCard:
    """ACD-016: corrections to a PUBLISHED card create a new version; the old one stays immutable."""
    if report_card.status != ReportCardStatus.PUBLISHED:
        raise ReportCardStateError("INVALID_TRANSITION: only a published report card can be revised.")

    report_card.is_current = False
    report_card.save(update_fields=['is_current', 'updated_at'])

    new_card = ReportCard.objects.create(
        foundation_id=report_card.foundation_id,
        student=report_card.student,
        term=report_card.term,
        class_group=report_card.class_group,
        status=ReportCardStatus.DRAFT,
        grades_snapshot=report_card.grades_snapshot,
        attendance_summary=report_card.attendance_summary,
        narrative=report_card.narrative,
        extracurricular_notes=report_card.extracurricular_notes,
        promotion_decision=report_card.promotion_decision,
        version=report_card.version + 1,
        is_current=True,
    )
    audit(
        action='academic.report_card.revised',
        entity_type='ReportCard',
        entity_id=new_card.id,
        foundation_id=report_card.foundation_id,
        diff={'previous_version': report_card.version, 'new_version': new_card.version},
    )
    return new_card


def get_or_create_report_card_policy(school) -> ReportCardPolicy:
    policy, _created = ReportCardPolicy.all_tenants.get_or_create(
        foundation_id=school.foundation_id, school=school,
    )
    return policy


def set_arrears_gate(school, enabled: bool, actor=None) -> ReportCardPolicy:
    """ACD-014: toggling block_rapor_on_arrears MUST be recorded in audit."""
    policy = get_or_create_report_card_policy(school)
    previous = policy.block_rapor_on_arrears
    policy.block_rapor_on_arrears = enabled
    policy.save()
    audit(
        action='academic.report_card_policy.arrears_gate_toggled',
        entity_type='ReportCardPolicy',
        entity_id=policy.id,
        foundation_id=school.foundation_id,
        diff={'before': previous, 'after': enabled},
    )
    return policy


def is_student_blocked_by_arrears(student, school) -> bool:
    """ACD-014: check whether the student's overdue invoices should withhold their rapor."""
    from apps.finance.models import Invoice, InvoiceStatus

    policy = get_or_create_report_card_policy(school)
    if not policy.block_rapor_on_arrears:
        return False

    overdue = Invoice.objects.filter(
        foundation_id=student.foundation_id,
        student=student,
        status__in=[InvoiceStatus.ISSUED, InvoiceStatus.PARTIALLY_PAID],
    )
    return any(invoice.is_overdue for invoice in overdue)


def get_visible_report_card(report_card: ReportCard) -> dict:
    """ACD-013/ACD-014: parents only ever see a PUBLISHED card, gated by arrears policy."""
    if report_card.status != ReportCardStatus.PUBLISHED:
        return {'visible': False, 'reason': 'NOT_PUBLISHED'}

    if is_student_blocked_by_arrears(report_card.student, report_card.class_group.school):
        return {'visible': False, 'reason': 'ARREARS'}

    return {'visible': True, 'reason': None}


def duplicate_lesson_plan(lesson_plan: LessonPlan, target_week_start_date, actor=None) -> LessonPlan:
    """TCH-010: duplicate a lesson plan into a new week, as an independent editable copy."""
    new_plan = LessonPlan.objects.create(
        foundation_id=lesson_plan.foundation_id,
        class_subject=lesson_plan.class_subject,
        week_start_date=target_week_start_date,
        title=lesson_plan.title,
        content=lesson_plan.content,
        attachments=lesson_plan.attachments,
        created_by=actor,
    )
    new_plan.slots.set(lesson_plan.slots.all())

    audit(
        action='academic.lesson_plan.duplicated',
        entity_type='LessonPlan',
        entity_id=new_plan.id,
        foundation_id=lesson_plan.foundation_id,
        diff={'source_plan_id': lesson_plan.id, 'target_week': str(target_week_start_date)},
    )
    return new_plan


DAILY_BROADCAST_LIMIT_PER_CLASS = 5


def get_or_create_broadcast_policy(school) -> BroadcastPolicy:
    policy, _created = BroadcastPolicy.objects.get_or_create(
        foundation_id=school.foundation_id, school=school,
    )
    return policy


def set_broadcast_policy(school, enabled: bool, actor=None) -> BroadcastPolicy:
    policy = get_or_create_broadcast_policy(school)
    previous = policy.teacher_can_broadcast
    policy.teacher_can_broadcast = enabled
    policy.save()
    audit(
        action='academic.broadcast_policy.toggled',
        entity_type='BroadcastPolicy',
        entity_id=policy.id,
        foundation_id=school.foundation_id,
        diff={'before': previous, 'after': enabled},
    )
    return policy


def send_broadcast(teacher, class_group, title, body, actor=None) -> Broadcast:
    """TCH-011: teacher -> class guardians group announcement, gated by policy and a daily rate limit."""
    from apps.identity.models import GuardianLink
    from apps.notifications.models import NotificationCategory
    from apps.notifications.services import dispatch_intent

    school = class_group.school
    policy = get_or_create_broadcast_policy(school)
    if not policy.teacher_can_broadcast:
        raise BroadcastNotAllowedError("BROADCAST_NOT_ALLOWED: this school has disabled teacher broadcasts.")

    today = timezone.localdate()
    sent_today = Broadcast.objects.filter(
        class_group=class_group, sent_at__date=today, deleted_at__isnull=True,
    ).count()
    if sent_today >= DAILY_BROADCAST_LIMIT_PER_CLASS:
        raise BroadcastRateLimitedError(
            f"BROADCAST_RATE_LIMITED: at most {DAILY_BROADCAST_LIMIT_PER_CLASS} broadcasts per class per day."
        )

    student_ids = ClassEnrollment.objects.filter(
        class_group=class_group, is_active=True, deleted_at__isnull=True,
    ).values_list('student_id', flat=True)

    guardian_links = GuardianLink.objects.filter(
        foundation_id=class_group.foundation_id, student_id__in=student_ids, deleted_at__isnull=True,
    ).select_related('guardian__person', 'guardian__user')

    sent_count = 0
    seen_guardian_ids = set()
    for link in guardian_links:
        guardian = link.guardian
        if guardian.id in seen_guardian_ids or not guardian.user:
            continue
        seen_guardian_ids.add(guardian.id)

        dispatch_intent(
            foundation_id=class_group.foundation_id,
            category=NotificationCategory.ANNOUNCEMENT,
            template_key='teacher.broadcast',
            payload={'message': f"{title}: {body}"},
            school_id=school.id,
            recipient_user=guardian.user,
            recipient_phone=getattr(guardian.user, 'phone_e164', ''),
            recipient_email=getattr(guardian.user, 'email', ''),
            recipient_name=guardian.person.full_name if guardian.person else '',
        )
        sent_count += 1

    broadcast = Broadcast.objects.create(
        foundation_id=class_group.foundation_id,
        class_group=class_group,
        sender=teacher,
        title=title,
        body=body,
        sent_at=timezone.now(),
        recipient_count=sent_count,
    )
    audit(
        action='academic.broadcast.sent',
        entity_type='Broadcast',
        entity_id=broadcast.id,
        foundation_id=class_group.foundation_id,
        diff={'class_group': class_group.name, 'recipient_count': sent_count},
    )
    return broadcast



class PermissionSlipClosedError(ValueError):
    """The permission slip's due_at has passed — new acknowledgements are closed."""


class GuardianNotLinkedError(ValueError):
    """The guardian is not linked to the given student (IAM-014)."""


class StudentNotEnrolledError(ValueError):
    """The student is not actively enrolled in the permission slip's class group."""


def create_permission_slip(staff, class_group, title, description='',
                           event_date=None, location='', due_at=None, actor=None):
    """Create a school-issued permission slip for one class group (spec/08 PAR-012),
    then notify that class's guardians that a consent request awaits their signature.
    Notification failures never block the slip's creation."""
    from apps.identity.models import GuardianLink
    from apps.notifications.models import NotificationCategory
    from apps.notifications.services import dispatch_intent

    if not title or not title.strip():
        raise ValueError("TITLE_REQUIRED: slip title must not be empty.")
    if due_at is not None and event_date is not None:
        # due_at may sit at any point before the event; it must not fall after the event day
        from datetime import datetime as _dt, time as _time
        event_end_of_day = _dt.combine(event_date, _time(23, 59, 59), tzinfo=timezone.utc if timezone.is_naive(due_at) else due_at.tzinfo)
        if due_at > event_end_of_day:
            raise ValueError("DUE_AFTER_EVENT: due_at must not fall after the event date.")

    slip = PermissionSlip.objects.create(
        foundation_id=class_group.foundation_id,
        class_group=class_group,
        created_by=staff,
        title=title.strip(),
        description=description or '',
        event_date=event_date,
        location=location or '',
        due_at=due_at,
    )
    audit(
        action='academic.permission_slip.created',
        entity_type='PermissionSlip',
        entity_id=slip.id,
        foundation_id=class_group.foundation_id,
        school_id=class_group.school_id,
        actor_id=getattr(actor, 'id', None),
        diff={'title': slip.title, 'class_group': class_group.name},
    )

    student_ids = ClassEnrollment.objects.filter(
        class_group=class_group, is_active=True, deleted_at__isnull=True,
    ).values_list('student_id', flat=True)

    guardian_links = GuardianLink.objects.filter(
        foundation_id=class_group.foundation_id, student_id__in=student_ids, deleted_at__isnull=True,
    ).select_related('guardian__person', 'guardian__user')

    event_line = f" (Tanggal: {slip.event_date.isoformat()})" if slip.event_date else ""
    seen_guardian_ids = set()
    for link in guardian_links:
        guardian = link.guardian
        if guardian.id in seen_guardian_ids or not guardian.user:
            continue
        seen_guardian_ids.add(guardian.id)
        try:
            dispatch_intent(
                foundation_id=class_group.foundation_id,
                category=NotificationCategory.ANNOUNCEMENT,
                template_key='academic.permission_slip.new',
                payload={
                    'message': f"Permintaan izin baru: {slip.title}{event_line}. Mohon berikan persetujuan digital di aplikasi.",
                    'permission_slip_id': slip.id,
                    'student_name': link.student.person.full_name if link.student and link.student.person else '',
                    'title': slip.title,
                    'class_group_name': class_group.name,
                    'event_date': slip.event_date.isoformat() if slip.event_date else '',
                    'location': slip.location or '',
                },
                school_id=class_group.school_id,
                recipient_user=guardian.user,
                recipient_phone=getattr(guardian.user, 'phone_e164', ''),
                recipient_email=getattr(guardian.user, 'email', ''),
                recipient_name=guardian.person.full_name if guardian.person else '',
            )
        except Exception:
            logger.warning("permission slip notification dispatch failed for slip %s", slip.id, exc_info=True)
    return slip


def acknowledge_permission_slip(slip, guardian, student, response, signature, actor=None):
    """Record a guardian's signed digital acknowledgement (PAR-012).

    Validates guardian-student link (IAM-014), student enrollment in the slip's
    class group, and the slip's due_at gate. A changed answer appends a NEW
    superseding acknowledgement row — the previous active row for the same
    (slip, student, guardian) is soft-deleted inside the same transaction
    (history preserved, never rewritten), so exactly one active response per
    signer exists at any moment (matching the active-only uniqueness constraint).
    """
    from django.db import transaction

    from apps.identity.models import GuardianLink

    if response not in {PermissionSlipAcknowledgement.RESPONSE_APPROVED, PermissionSlipAcknowledgement.RESPONSE_DECLINED}:
        raise ValueError("INVALID_RESPONSE: response must be APPROVED or DECLINED.")
    if not signature or not signature.strip():
        raise ValueError("SIGNATURE_REQUIRED: a typed signature is required (PAR-012).")

    link = GuardianLink.objects.filter(
        foundation_id=slip.foundation_id, guardian=guardian, student=student, deleted_at__isnull=True,
    ).first()
    if not link:
        raise GuardianNotLinkedError("GUARDIAN_NOT_LINKED: this guardian is not linked to the given student (IAM-014).")

    enrolled = ClassEnrollment.objects.filter(
        foundation_id=slip.foundation_id, student=student,
        class_group_id=slip.class_group_id, is_active=True, deleted_at__isnull=True,
    ).exists()
    if not enrolled:
        raise StudentNotEnrolledError("STUDENT_NOT_ENROLLED: the student is not in this slip's class group.")

    if slip.due_at is not None and timezone.now() > slip.due_at:
        raise PermissionSlipClosedError("PERMISSION_SLIP_CLOSED: the acknowledgement window has closed.")

    now = timezone.now()
    with transaction.atomic():
        # Supersede: soft-delete any previous active response by this guardian
        # for this (slip, student) — rows stay in history, only one stays active.
        PermissionSlipAcknowledgement.objects.filter(
            foundation_id=slip.foundation_id, permission_slip=slip,
            student=student, guardian=guardian, deleted_at__isnull=True,
        ).update(deleted_at=now)

        ack = PermissionSlipAcknowledgement.objects.create(
            foundation_id=slip.foundation_id,
            permission_slip=slip,
            student=student,
            guardian=guardian,
            response=response,
            responded_at=now,
            signature=signature.strip(),
        )
    audit(
        action='academic.permission_slip.acknowledged',
        entity_type='PermissionSlipAcknowledgement',
        entity_id=ack.id,
        foundation_id=slip.foundation_id,
        school_id=slip.class_group.school_id,
        actor_id=getattr(actor, 'id', None),
        diff={'response': response, 'slip': slip.title, 'student_nis': student.nis},
    )
    return ack


def get_effective_acknowledgement(slip, student, guardian):
    """The guardian's current effective response for (slip, student): the latest active row."""
    return PermissionSlipAcknowledgement.objects.filter(
        foundation_id=slip.foundation_id, permission_slip=slip,
        student=student, guardian=guardian, deleted_at__isnull=True,
    ).order_by('-responded_at', '-id').first()


def get_slip_consent_tally(slip):
    """Live consent tally for the school (PAR-012): per enrolled student, the
    latest effective response, plus roll-up counts approved/declined/pending."""
    student_ids = list(ClassEnrollment.objects.filter(
        class_group_id=slip.class_group_id, is_active=True, deleted_at__isnull=True,
        foundation_id=slip.foundation_id,
    ).values_list('student_id', flat=True))

    acks = list(PermissionSlipAcknowledgement.objects.filter(
        foundation_id=slip.foundation_id, permission_slip=slip,
        student_id__in=student_ids, deleted_at__isnull=True,
    ).order_by('responded_at', 'id'))
    # latest per (student, guardian): dict keyed by (student_id, guardian_id)
    latest_by_pair = {}
    for ack in acks:
        latest_by_pair[(ack.student_id, ack.guardian_id)] = ack

    student_latest = {}  # student_id -> effective response (most recent guardian ack)
    for (student_id, _guardian_id), ack in latest_by_pair.items():
        current = student_latest.get(student_id)
        if current is None or ack.responded_at >= current[1]:
            student_latest[student_id] = (ack.response, ack.responded_at)

    approved = declined = 0
    pending_students = []
    for sid in student_ids:
        entry = student_latest.get(sid)
        if entry is None:
            pending_students.append(sid)
        elif entry[0] == PermissionSlipAcknowledgement.RESPONSE_APPROVED:
            approved += 1
        else:
            declined += 1

    return {
        'total_enrolled': len(student_ids),
        'approved': approved,
        'declined': declined,
        'pending': len(pending_students),
        'pending_student_ids': pending_students,
    }


def build_proctor_snapshot(exam: Exam) -> dict:
    """Live proctoring view of one exam: the enrolled roster merged with each
    student's attempt state (ACD-024). Auto-submits any expired in-progress
    attempt it encounters, so the numbers never show a stale "in progress".

    Shared by the JSON endpoint the proctor console polls and the web page
    that renders the console's first paint."""
    now = timezone.now()

    # Questions
    questions = exam.questions.filter(deleted_at__isnull=True)
    total_questions = questions.count()

    # Roster: Active enrollments in exam's class group
    enrollments = ClassEnrollment.objects.filter(
        class_group=exam.class_subject.class_group,
        is_active=True,
        deleted_at__isnull=True,
    ).select_related('student', 'student__person')

    enrolled_students = {e.student_id: e.student for e in enrollments}

    # Attempts for this exam
    attempts = ExamAttempt.objects.filter(
        exam=exam,
        deleted_at__isnull=True,
    ).select_related('student', 'student__person').prefetch_related('answers')

    attempts_by_student = {att.student_id: att for att in attempts}

    # Combine enrolled students and any students who attempted
    all_student_ids = set(enrolled_students.keys()) | set(attempts_by_student.keys())
    all_students = []
    for sid in all_student_ids:
        student = enrolled_students.get(sid) or (attempts_by_student[sid].student if sid in attempts_by_student else None)
        if student:
            all_students.append(student)

    # Sort students by full_name, nis
    all_students.sort(key=lambda s: (getattr(getattr(s, 'person', None), 'full_name', '') or '', s.nis))

    student_rows = []
    in_progress_count = 0
    submitted_count = 0
    not_started_count = 0
    flagged_count = 0

    for student in all_students:
        attempt = attempts_by_student.get(student.id)
        if attempt:
            attempt = auto_submit_if_expired(attempt)
            status_val = attempt.status
            active_answers = [a for a in attempt.answers.all() if a.deleted_at is None]
            answered_count = len(active_answers)

            if active_answers:
                latest_ans = max(active_answers, key=lambda a: a.answered_at)
                last_saved_at = latest_ans.answered_at
            else:
                last_saved_at = attempt.started_at

            if status_val == ExamAttemptStatus.IN_PROGRESS:
                rem_sec = compute_remaining_seconds(attempt)
                in_progress_count += 1
            else:
                rem_sec = 0
                submitted_count += 1

            focus_losses = attempt.focus_loss_count
            attempt_id = attempt.id
        else:
            status_val = 'NOT_STARTED'
            answered_count = 0
            last_saved_at = None
            rem_sec = None
            focus_losses = 0
            attempt_id = None
            not_started_count += 1

        progress_pct = int(round((answered_count / total_questions) * 100)) if total_questions > 0 else 0
        needs_attention = (focus_losses >= 3)
        if needs_attention:
            flagged_count += 1

        student_name = student.person.full_name if hasattr(student, 'person') and student.person else student.nis

        student_rows.append({
            'student_id': student.id,
            'student_name': student_name,
            'nis': student.nis,
            'attempt_id': attempt_id,
            'status': status_val,
            'answered_count': answered_count,
            'total_questions': total_questions,
            'progress_pct': progress_pct,
            'focus_loss_count': focus_losses,
            'last_saved_at': last_saved_at,
            'remaining_seconds': rem_sec,
            'needs_attention': needs_attention,
        })

    is_active = (exam.window_start <= now <= exam.window_end)

    return {
        'exam': {
            'id': exam.id,
            'title': exam.title,
            'duration_min': exam.duration_min,
            'window_start': exam.window_start,
            'window_end': exam.window_end,
            'total_questions': total_questions,
            'is_active': is_active,
        },
        'summary': {
            'total_students': len(student_rows),
            'in_progress': in_progress_count,
            'submitted': submitted_count,
            'not_started': not_started_count,
            'flagged_focus_loss': flagged_count,
        },
        'students': student_rows,
    }


def publish_exam(exam, actor=None):
    """Publish an exam so students can see and start it (ACD-024). Shared by
    the JSON `publish` action and the Mode ujian console so both write the
    same audit event. Idempotent: publishing an already-published exam
    changes and audits nothing."""
    if exam.published:
        return exam
    exam.published = True
    exam.save(update_fields=['published', 'updated_at'])
    audit(
        action='academic.exam.published',
        entity_type='Exam',
        entity_id=exam.id,
        actor_id=str(actor.id) if actor is not None and getattr(actor, 'id', None) else None,
        foundation_id=exam.foundation_id,
        diff={'published': {'before': False, 'after': True}},
    )
    return exam
