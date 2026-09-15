from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.utils import timezone

from apps.core.services import audit
from apps.academic.models import (
    ALLOWED_SUBMISSION_CONTENT_TYPES,
    Assessment,
    AssessmentScore,
    ClassEnrollment,
    DEFAULT_DESCRIPTOR_BANDS,
    Homework,
    HomeworkSubmission,
    HomeworkSubmissionStatus,
    MAX_SUBMISSION_FILES,
    MAX_SUBMISSION_FILE_SIZE,
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


class InvalidSubmissionFilesError(ValueError):
    pass


class ReminderRateLimitedError(ValueError):
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


def create_timetable_slot(class_subject, day_of_week, period_no, start_time, end_time, room='') -> TimetableSlot:
    check_timetable_conflicts(class_subject, day_of_week, period_no, room)
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
