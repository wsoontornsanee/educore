import logging
import random
from collections import Counter
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

from apps.core.services import audit
from apps.identity.models import Student
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
    ReportCard,
    ReportCardPolicy,
    ReportCardStatus,
    TimetableSlot,
    TimetableSubstitution,
    WEIGHTED_ASSESSMENT_TYPES,
)


class ScoreOutOfRangeError(ValueError):
    pass


class ReasonRequiredError(ValueError):
    pass


class WeightConfigError(ValueError):
    pass


class TimetableConflictError(ValueError):
    pass


class SlotNotScheduledError(ValueError):
    pass


class PeriodGridMismatchError(ValueError):
    pass


class InvalidSubmissionFilesError(ValueError):
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


def set_assessment_score(
    assessment: Assessment,
    student,
    score=None,
    feedback='',
    descriptor=None,
    reason=None,
    actor=None,
) -> AssessmentScore:
    """Create or update a student's score on an assessment (ACD-004, ACD-005)."""
    if score is not None:
        score = Decimal(str(score))
        if score < 0 or score > assessment.max_score:
            raise ScoreOutOfRangeError(
                f"SCORE_OUT_OF_RANGE: score must be between 0 and {assessment.max_score}."
            )

    existing = AssessmentScore.objects.filter(assessment=assessment, student=student).first()

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


def submit_homework(homework: Homework, student, text='', files=None) -> HomeworkSubmission:
    """ACD-027/ACD-028: create or update a student's homework submission."""
    files = files or []
    validate_submission_files(files)

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


def get_homework_completion(homework: Homework) -> dict:
    """ACD-030: class completion counts for a homework assignment."""
    total = ClassEnrollment.objects.filter(
        class_group=homework.class_subject.class_group,
        is_active=True,
        deleted_at__isnull=True,
    ).count()
    submitted_student_ids = set(
        HomeworkSubmission.objects.filter(homework=homework, deleted_at__isnull=True).values_list('student_id', flat=True)
    )
    return {
        'total': total,
        'submitted': len(submitted_student_ids),
        'not_started': max(total - len(submitted_student_ids), 0),
    }


def remind_unsubmitted(homework: Homework) -> dict:
    """ACD-030: rate-limited (once per 12h) reminder to unsubmitted students. Delivery is stubbed."""
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
    return substitution


def get_effective_teacher_for_slot(slot, date):
    """ACD-020: the teacher authorized to teach/submit attendance for this slot on this date —
    the date's substitute if one is assigned, otherwise the slot's regular teacher.
    """
    substitution = TimetableSubstitution.objects.filter(
        foundation_id=slot.foundation_id, slot=slot, date=date,
    ).first()
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
        )
    }
    submitted_slot_ids = set(
        PeriodAttendance.objects.filter(
            foundation_id=school.foundation_id, slot_id__in=slot_ids, date=date,
        ).values_list('slot_id', flat=True).distinct()
    )

    expected = []
    for slot in slots:
        attendance_submitted = slot.id in submitted_slot_ids
        if missing_only and attendance_submitted:
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


def render_report_card_html(report_card: ReportCard) -> str:
    """Minimal functional layout — the branded template is a pending open design decision (memory/01_PROJECT.md §5.6)."""
    student = report_card.student
    rows = "".join(
        f"<tr><td>{g['subject']}</td><td>{g.get('grade') if g.get('grade') is not None else '-'}</td>"
        f"<td>{g.get('status')}</td></tr>"
        for g in report_card.grades_snapshot
    )
    attendance_rows = "".join(
        f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in report_card.attendance_summary.items()
    )
    return f"""<html><body>
<h1>Rapor - {student.person.full_name}</h1>
<p>NISN: {student.nisn or '-'} | Kelas: {report_card.class_group.name} | {report_card.term.name}</p>
<h2>Nilai</h2>
<table border="1"><tr><th>Mapel</th><th>Nilai</th><th>Status</th></tr>{rows}</table>
<h2>Kehadiran</h2>
<table border="1"><tr><th>Status</th><th>Jumlah</th></tr>{attendance_rows}</table>
<h2>Catatan Wali Kelas</h2>
<p>{report_card.narrative}</p>
<p>Versi: {report_card.version}</p>
</body></html>"""


def render_report_card_pdf(report_card: ReportCard) -> str:
    """Render and persist the report card document. Falls back to HTML if weasyprint's
    native libraries are unavailable in this environment (see memory/01_PROJECT.md)."""
    html_content = render_report_card_html(report_card)
    output_dir = Path(settings.MEDIA_ROOT) / 'report_cards'
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        from weasyprint import HTML
        key = f"report_cards/{report_card.id}_v{report_card.version}.pdf"
        HTML(string=html_content).write_pdf(str(Path(settings.MEDIA_ROOT) / key))
    except Exception:
        logger.warning("weasyprint unavailable, falling back to HTML rapor output", exc_info=True)
        key = f"report_cards/{report_card.id}_v{report_card.version}.html"
        (Path(settings.MEDIA_ROOT) / key).write_text(html_content)

    return key


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
    policy, _created = ReportCardPolicy.objects.get_or_create(
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
