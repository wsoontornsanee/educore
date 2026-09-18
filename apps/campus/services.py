import logging
from typing import Optional

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.core.services import audit
from apps.identity.models import Guardian, GuardianLink, RoleAssignment, School, Staff, Student, User
from .models import (
    BehaviourCase,
    BehaviourCategory,
    BehaviourPolicy,
    BehaviourReason,
    BehaviourRecord,
    CaseStatus,
    CounsellingConfidentiality,
    CounsellingSession,
    CounsellingSessionType,
    Loan,
    LoanBorrowerType,
    LoanStatus,
)

logger = logging.getLogger(__name__)


def get_or_create_behaviour_policy(school: School) -> BehaviourPolicy:
    """Gets or creates the BehaviourPolicy for a given school."""
    policy, _ = BehaviourPolicy.objects.get_or_create(
        foundation_id=school.foundation_id,
        school=school,
        defaults={
            'escalation_negative_threshold': -25,
            'rapor_includes_behaviour': False,
        },
    )
    return policy


def resolve_current_term_for_school(school: School, at_datetime=None):
    """Resolves the current academic Term for the school based on occurred_at."""
    if at_datetime is None:
        at_datetime = timezone.now()
    check_date = at_datetime.date()

    try:
        from apps.academic.models import Term
        return Term.objects.filter(
            foundation_id=school.foundation_id,
            academic_year__school=school,
            start_date__lte=check_date,
            end_date__gte=check_date,
            deleted_at__isnull=True,
        ).first()
    except Exception as exc:
        logger.debug("Failed to resolve current term: %s", exc)
        return None


def record_behaviour(
    foundation_id: int,
    school: School,
    student: Student,
    reason: BehaviourReason,
    recorded_by: User,
    term=None,
    points: Optional[int] = None,
    note: str = '',
    occurred_at=None,
    check_escalation: bool = True,
) -> BehaviourRecord:
    """Records a new student behaviour entry (spec/10 §4, spec/09 TCH-008, TCH-009).
    
    Validates reason catalogue, snapshots points, dispatches major alerts,
    and checks escalation threshold (LIF-009).
    """
    if not reason.is_active:
        raise ValidationError("Katalog alasan perilaku tidak aktif (inactive).")
    if reason.school_id != school.id:
        raise ValidationError("Alasan perilaku tidak sesuai dengan sekolah siswa.")
    if student.school_id != school.id:
        raise ValidationError("Siswa tidak terdaftar di sekolah yang bersangkutan.")

    if points is None:
        points = reason.points
    if occurred_at is None:
        occurred_at = timezone.now()
    if term is None:
        term = resolve_current_term_for_school(school, occurred_at)

    with transaction.atomic():
        record = BehaviourRecord.objects.create(
            foundation_id=foundation_id,
            school=school,
            student=student,
            term=term,
            reason=reason,
            points=points,
            note=note or '',
            occurred_at=occurred_at,
            recorded_by=recorded_by,
        )

        audit(
            action='campus.behaviour.recorded',
            entity_type='BehaviourRecord',
            entity_id=record.id,
            actor_id=str(recorded_by.id) if recorded_by else None,
            foundation_id=foundation_id,
            school_id=school.id,
            diff={
                'student_id': student.id,
                'reason_code': reason.code,
                'category': reason.category,
                'points': points,
                'term_id': term.id if term else None,
            },
        )

        # LIF-009: Escalation threshold evaluation on negative points
        if check_escalation and points < 0:
            _evaluate_escalation_threshold(
                foundation_id=foundation_id,
                school=school,
                student=student,
                term=term,
            )

    # LIF-011: Guardian notification dispatch
    _dispatch_behaviour_notifications(record)

    return record


def _evaluate_escalation_threshold(
    foundation_id: int,
    school: School,
    student: Student,
    term,
) -> Optional[BehaviourCase]:
    """Auto-opens a BehaviourCase if student's negative points meet or exceed threshold (LIF-009)."""
    policy = get_or_create_behaviour_policy(school)
    threshold = policy.escalation_negative_threshold

    # Filter by student, term, and active (non-superseded) negative records
    records_qs = BehaviourRecord.objects.filter(
        foundation_id=foundation_id,
        school=school,
        student=student,
        is_superseded=False,
        deleted_at__isnull=True,
        points__lt=0,
    )
    if term:
        records_qs = records_qs.filter(term=term)

    term_neg_points = records_qs.aggregate(total=Sum('points'))['total'] or 0

    if term_neg_points <= threshold:
        # Check if an active (OPEN or IN_PROGRESS) case already exists for this term
        existing_case_qs = BehaviourCase.objects.filter(
            foundation_id=foundation_id,
            school=school,
            student=student,
            status__in=[CaseStatus.OPEN, CaseStatus.IN_PROGRESS],
            deleted_at__isnull=True,
        )
        if term:
            existing_case_qs = existing_case_qs.filter(term=term)

        if not existing_case_qs.exists():
            trigger_text = (
                f"Akumulasi poin negatif ({term_neg_points}) melampaui ambang batas "
                f"({threshold}) pada semester ini."
            )
            case = BehaviourCase.objects.create(
                foundation_id=foundation_id,
                school=school,
                student=student,
                term=term,
                opened_at=timezone.now(),
                trigger=trigger_text,
                status=CaseStatus.OPEN,
                assigned_counsellor=policy.default_counsellor,
            )
            audit(
                action='campus.behaviour.case_opened',
                entity_type='BehaviourCase',
                entity_id=case.id,
                foundation_id=foundation_id,
                school_id=school.id,
                diff={
                    'student_id': student.id,
                    'term_id': term.id if term else None,
                    'term_negative_points': term_neg_points,
                    'threshold': threshold,
                    'counsellor_id': policy.default_counsellor_id,
                },
            )
            return case
    return None


def _dispatch_behaviour_notifications(record: BehaviourRecord):
    """Dispatches guardian notifications for behaviour records (LIF-011)."""
    try:
        from apps.notifications.models import NotificationCategory
        from apps.notifications.services import dispatch_intent

        category = (
            NotificationCategory.BEHAVIOUR_MAJOR
            if record.reason.category == BehaviourCategory.MAJOR
            else NotificationCategory.BEHAVIOUR_MINOR
        )

        guardian_links = GuardianLink.objects.filter(
            foundation_id=record.foundation_id,
            student=record.student,
            deleted_at__isnull=True,
        ).select_related('guardian__user', 'guardian__person', 'student__person')

        seen_guardian_ids = set()
        for link in guardian_links:
            guardian = link.guardian
            if not guardian or not guardian.user or guardian.id in seen_guardian_ids:
                continue
            seen_guardian_ids.add(guardian.id)

            student_name = (
                record.student.person.full_name
                if getattr(record.student, 'person', None)
                else f"Siswa ID {record.student.id}"
            )
            payload = {
                'student_name': student_name,
                'category': record.reason.category,
                'reason_code': record.reason.code,
                'reason_label': record.reason.label,
                'points': record.points,
                'note': record.note,
                'occurred_at': record.occurred_at.isoformat(),
            }

            dispatch_intent(
                foundation_id=record.foundation_id,
                category=category,
                template_key=f"campus.behaviour.{record.reason.category.lower()}",
                payload=payload,
                school_id=record.school_id,
                recipient_user=guardian.user,
                recipient_phone=getattr(guardian.user, 'phone_e164', ''),
                recipient_email=getattr(guardian.user, 'email', ''),
                dedupe_key=f"behaviour_record:{record.id}:{guardian.id}",
            )
    except Exception as exc:
        logger.warning(
            "Failed to dispatch behaviour notifications for record %s: %s",
            record.id,
            exc,
        )


def supersede_behaviour_record(
    original_record: BehaviourRecord,
    new_reason: BehaviourReason,
    recorded_by: User,
    correction_reason: str,
    points: Optional[int] = None,
    note: str = '',
    occurred_at=None,
) -> BehaviourRecord:
    """Performs an immutable correction by creating a superseding record (LIF-013)."""
    if original_record.is_superseded:
        raise ValidationError("Catatan perilaku ini sudah pernah dikoreksi/digantikan sebelumnya.")
    if not correction_reason or not correction_reason.strip():
        raise ValidationError("Alasan koreksi (correction_reason) wajib diisi (LIF-013).")

    if occurred_at is None:
        occurred_at = original_record.occurred_at

    with transaction.atomic():
        new_record = record_behaviour(
            foundation_id=original_record.foundation_id,
            school=original_record.school,
            student=original_record.student,
            reason=new_reason,
            recorded_by=recorded_by,
            term=original_record.term,
            points=points,
            note=note,
            occurred_at=occurred_at,
            check_escalation=False,
        )

        original_record.is_superseded = True
        original_record.superseded_by = new_record
        original_record.correction_reason = correction_reason.strip()
        original_record.save(update_fields=['is_superseded', 'superseded_by', 'correction_reason', 'updated_at'])

        audit(
            action='campus.behaviour.record_superseded',
            entity_type='BehaviourRecord',
            entity_id=original_record.id,
            actor_id=str(recorded_by.id) if recorded_by else None,
            foundation_id=original_record.foundation_id,
            school_id=original_record.school_id,
            diff={
                'superseded_by_id': new_record.id,
                'correction_reason': correction_reason,
            },
        )

        # Re-check escalation threshold after correction
        _evaluate_escalation_threshold(
            foundation_id=original_record.foundation_id,
            school=original_record.school,
            student=original_record.student,
            term=original_record.term,
        )

    return new_record


def acknowledge_behaviour_record(
    record: BehaviourRecord,
    guardian: Guardian,
    user: Optional[User] = None,
) -> BehaviourRecord:
    """Guardian acknowledges a behaviour record with timestamping (LIF-012)."""
    if record.is_superseded:
        raise ValidationError("Tidak dapat mengonfirmasi catatan perilaku yang sudah digantikan.")

    # Validate that this guardian is linked to the record's student
    is_linked = GuardianLink.objects.filter(
        foundation_id=record.foundation_id,
        guardian=guardian,
        student=record.student,
        deleted_at__isnull=True,
    ).exists()
    if not is_linked:
        raise ValidationError("Wali murid tidak memiliki hak akses pada siswa ini.")

    record.acknowledged_by_guardian_at = timezone.now()
    record.acknowledged_by = guardian
    record.save(update_fields=['acknowledged_by_guardian_at', 'acknowledged_by', 'updated_at'])

    actor = user or getattr(guardian, 'user', None)
    audit(
        action='campus.behaviour.record_acknowledged',
        entity_type='BehaviourRecord',
        entity_id=record.id,
        actor_id=str(actor.id) if actor else None,
        foundation_id=record.foundation_id,
        school_id=record.school_id,
        diff={
            'guardian_id': guardian.id,
            'acknowledged_at': record.acknowledged_by_guardian_at.isoformat(),
        },
    )
    return record


def get_student_behaviour_summary(student: Student, term=None) -> dict:
    """Calculates term and lifetime positive, negative, and net totals (LIF-008, LIF-010).
    
    Positive and negative totals are kept distinct and first-class.
    """
    base_qs = BehaviourRecord.objects.filter(
        student=student,
        is_superseded=False,
        deleted_at__isnull=True,
    )

    # Lifetime aggregates
    lifetime_pos = base_qs.filter(points__gt=0).aggregate(s=Sum('points'))['s'] or 0
    lifetime_neg = base_qs.filter(points__lt=0).aggregate(s=Sum('points'))['s'] or 0
    lifetime_net = lifetime_pos + lifetime_neg

    # Term aggregates
    term_qs = base_qs.filter(term=term) if term else base_qs
    term_pos = term_qs.filter(points__gt=0).aggregate(s=Sum('points'))['s'] or 0
    term_neg = term_qs.filter(points__lt=0).aggregate(s=Sum('points'))['s'] or 0
    term_net = term_pos + term_neg

    # Counts
    cat_counts = term_qs.values('reason__category').annotate(count=Count('id'))
    counts_map = {row['reason__category']: row['count'] for row in cat_counts}

    unacknowledged = term_qs.filter(
        points__lt=0,
        acknowledged_by_guardian_at__isnull=True,
    ).count()

    # Active cases
    cases_qs = BehaviourCase.objects.filter(
        student=student,
        status__in=[CaseStatus.OPEN, CaseStatus.IN_PROGRESS],
        deleted_at__isnull=True,
    )
    if term:
        cases_qs = cases_qs.filter(term=term)

    policy = get_or_create_behaviour_policy(student.school)

    return {
        'student_id': student.id,
        'term_id': term.id if term else None,
        'term_positive_points': term_pos,
        'term_negative_points': term_neg,
        'term_net_points': term_net,
        'lifetime_positive_points': lifetime_pos,
        'lifetime_negative_points': lifetime_neg,
        'lifetime_net_points': lifetime_net,
        'positive_records_count': counts_map.get(BehaviourCategory.POSITIVE, 0),
        'minor_records_count': counts_map.get(BehaviourCategory.MINOR, 0),
        'major_records_count': counts_map.get(BehaviourCategory.MAJOR, 0),
        'unacknowledged_infractions_count': unacknowledged,
        'active_cases_count': cases_qs.count(),
        'escalation_threshold': policy.escalation_negative_threshold,
    }


def record_counselling_session(
    foundation_id: int,
    school: School,
    student: Student,
    counsellor: Staff,
    recorded_by: User,
    case: Optional[BehaviourCase] = None,
    occurred_at=None,
    session_type: str = 'INITIAL',
    notes: str = '',
    follow_up_at=None,
    confidentiality: str = CounsellingConfidentiality.NORMAL,
    is_urgent: bool = False,
) -> CounsellingSession:
    """Creates a guidance counselling (BK) session record (spec/10 §5, LIF-015 to LIF-018).

    Notes are encrypted at rest and never included in the audit diff (LIF-015
    excludes RESTRICTED note content from every general trail; treated the
    same way regardless of confidentiality since the content itself is
    sensitive either way). Urgent sessions immediately escalate to the
    school's principal, bypassing the normal queue (LIF-017).
    """
    if student.school_id != school.id:
        raise ValidationError("Siswa tidak terdaftar di sekolah yang bersangkutan.")
    if case is not None and case.student_id != student.id:
        raise ValidationError("Kasus perilaku tidak sesuai dengan siswa.")
    if confidentiality not in CounsellingConfidentiality.values:
        raise ValidationError("Tingkat kerahasiaan (confidentiality) tidak valid.")
    if session_type not in CounsellingSessionType.values:
        raise ValidationError("Jenis sesi (type) tidak valid.")
    if occurred_at is None:
        occurred_at = timezone.now()

    with transaction.atomic():
        session = CounsellingSession(
            foundation_id=foundation_id,
            school=school,
            case=case,
            student=student,
            counsellor=counsellor,
            occurred_at=occurred_at,
            type=session_type,
            follow_up_at=follow_up_at,
            confidentiality=confidentiality,
            is_urgent=is_urgent,
        )
        session.notes = notes
        session.save()

        audit(
            action='campus.counselling.session_recorded',
            entity_type='CounsellingSession',
            entity_id=session.id,
            actor_id=str(recorded_by.id) if recorded_by else None,
            foundation_id=foundation_id,
            school_id=school.id,
            diff={
                'student_id': student.id,
                'counsellor_id': counsellor.id,
                'case_id': case.id if case else None,
                'type': session_type,
                'confidentiality': confidentiality,
                'is_urgent': is_urgent,
                'follow_up_at': follow_up_at.isoformat() if follow_up_at else None,
            },
        )

    if is_urgent:
        _dispatch_urgent_counselling_escalation(session)

    return session


def get_principal_users(school: School) -> list[User]:
    """Resolves the acting principal(s) for a school: `school_admin` role assignments
    scoped to that school, plus `foundation_admin` (Yayasan-level oversight) (LIF-015, LIF-017)."""
    school_admin_ids = RoleAssignment.all_tenants.filter(
        foundation_id=school.foundation_id,
        role=RoleAssignment.ROLE_SCHOOL_ADMIN,
        scope_type=RoleAssignment.SCOPE_SCHOOL,
        scope_id=school.id,
        deleted_at__isnull=True,
    ).values_list('user_id', flat=True)

    foundation_admin_ids = RoleAssignment.all_tenants.filter(
        foundation_id=school.foundation_id,
        role=RoleAssignment.ROLE_FOUNDATION_ADMIN,
        scope_type=RoleAssignment.SCOPE_FOUNDATION,
        deleted_at__isnull=True,
    ).values_list('user_id', flat=True)

    user_ids = set(school_admin_ids) | set(foundation_admin_ids)
    return list(User.all_tenants.filter(id__in=user_ids, deleted_at__isnull=True))


def is_counselling_reader_authorized(session: CounsellingSession, user: User, foundation_id: int) -> bool:
    """LIF-015: a RESTRICTED note is visible only to its authoring counsellor and the
    principal — never to teachers or guardians."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if session.counsellor.user_id == user.id:
        return True
    principal_ids = {u.id for u in get_principal_users(session.school)}
    return user.id in principal_ids


def access_counselling_session(session: CounsellingSession, user: User, foundation_id: int) -> CounsellingSession:
    """Authorizes and audits a read of a (possibly RESTRICTED) counselling session (LIF-015, LIF-016).

    Every access to a RESTRICTED note — granted or denied — writes an audit
    event naming the reader (LIF-016; also exercised by the "teacher receives
    403 and the attempt is audited" acceptance criterion).
    """
    if session.confidentiality != CounsellingConfidentiality.RESTRICTED:
        return session

    authorized = is_counselling_reader_authorized(session, user, foundation_id)
    audit(
        action='campus.counselling.restricted_note_accessed' if authorized else 'campus.counselling.restricted_note_access_denied',
        entity_type='CounsellingSession',
        entity_id=session.id,
        actor_id=str(user.id) if user and user.is_authenticated else None,
        foundation_id=foundation_id,
        school_id=session.school_id,
        diff={'student_id': session.student_id, 'granted': authorized},
    )
    if not authorized:
        raise PermissionDenied("Catatan konseling ini bersifat RESTRICTED (LIF-015).")
    return session


def _dispatch_urgent_counselling_escalation(session: CounsellingSession):
    """LIF-017: safeguarding escalation — notifies the principal directly, bypassing
    normal queues (immediate, CRITICAL priority, quiet-hours exempt)."""
    try:
        from apps.notifications.models import NotificationCategory
        from apps.notifications.services import dispatch_intent

        student_name = (
            session.student.person.full_name
            if getattr(session.student, 'person', None)
            else f"Siswa ID {session.student_id}"
        )
        principals = get_principal_users(session.school)
        if not principals:
            logger.warning(
                "Urgent counselling session %s has no principal (school_admin/foundation_admin) "
                "to escalate to at school %s — LIF-017 escalation NOT sent.",
                session.id,
                session.school_id,
            )
            return

        for principal in principals:
            dispatch_intent(
                foundation_id=session.foundation_id,
                category=NotificationCategory.COUNSELLING_URGENT,
                template_key='campus.counselling.urgent',
                payload={
                    'message': f"Sesi BK mendesak untuk {student_name} memerlukan perhatian segera.",
                    'student_name': student_name,
                    'session_id': session.id,
                    'occurred_at': session.occurred_at.isoformat(),
                },
                school_id=session.school_id,
                recipient_user=principal,
                recipient_phone=getattr(principal, 'phone_e164', ''),
                recipient_email=getattr(principal, 'email', ''),
                dedupe_key=f"counselling_urgent:{session.id}:{principal.id}",
                immediate=True,
            )
        # Only stamped once at least one principal was actually notified — LIF-017's
        # bypass-the-queue guarantee must not be reported as satisfied when nobody
        # was reachable (see the school-has-no-principal branch above).
        session.urgent_notified_at = timezone.now()
        session.save(update_fields=['urgent_notified_at', 'updated_at'])
    except Exception as exc:
        logger.warning(
            "Failed to dispatch urgent counselling escalation for session %s: %s",
            session.id,
            exc,
        )


def get_counsellor_followups_due(foundation_id: Optional[int] = None):
    """LIF-018: counselling sessions whose follow-up date has arrived and whose
    counsellor task reminder has not yet been sent."""
    qs = CounsellingSession.all_tenants.filter(
        follow_up_at__isnull=False,
        follow_up_at__lte=timezone.now(),
        follow_up_reminder_sent_at__isnull=True,
        deleted_at__isnull=True,
    )
    if foundation_id:
        qs = qs.filter(foundation_id=foundation_id)
    return qs


def queue_counsellor_followup_reminder(session: CounsellingSession) -> bool:
    """LIF-018: dispatches (and stamps) the counsellor task reminder for a due follow-up."""
    from apps.notifications.models import NotificationCategory
    from apps.notifications.services import dispatch_intent

    counsellor_user = session.counsellor.user
    if not counsellor_user:
        return False

    student_name = (
        session.student.person.full_name
        if getattr(session.student, 'person', None)
        else f"Siswa ID {session.student_id}"
    )
    dispatch_intent(
        foundation_id=session.foundation_id,
        category=NotificationCategory.COUNSELLING_FOLLOW_UP,
        template_key='campus.counselling.follow_up_reminder',
        payload={
            'message': f"Tindak lanjut sesi BK untuk {student_name} sudah jatuh tempo.",
            'student_name': student_name,
            'session_id': session.id,
            'follow_up_at': session.follow_up_at.isoformat(),
        },
        school_id=session.school_id,
        recipient_user=counsellor_user,
        recipient_phone=getattr(counsellor_user, 'phone_e164', ''),
        recipient_email=getattr(counsellor_user, 'email', ''),
        dedupe_key=f"counselling_followup:{session.id}",
    )
    session.follow_up_reminder_sent_at = timezone.now()
    session.save(update_fields=['follow_up_reminder_sent_at', 'updated_at'])
    return True


def get_behaviour_report_card_data(student: Student, term) -> dict:
    """Returns behaviour summary data for inclusion on report card if enabled (LIF-014)."""
    policy = get_or_create_behaviour_policy(student.school)
    if not policy.rapor_includes_behaviour:
        return {'enabled': False}

    summary = get_student_behaviour_summary(student, term)
    return {
        'enabled': True,
        'positive_points': summary['term_positive_points'],
        'negative_points': summary['term_negative_points'],
        'net_points': summary['term_net_points'],
        'positive_count': summary['positive_records_count'],
        'minor_count': summary['minor_records_count'],
        'major_count': summary['major_records_count'],
        'active_cases_count': summary['active_cases_count'],
    }


def remind_overdue_loans(foundation_id: int) -> dict:
    """Sweeps ACTIVE loans past due_at, flips them OVERDUE, and notifies the
    borrower's guardian (STUDENT) or the borrower directly (STAFF) via the
    LIBRARY_LOAN_DUE category — digest_only, so run_daily_digest folds these
    into the evening digest unchanged (spec/13 §3, this Open Item).

    No rate limit beyond dedupe_key-per-day: unlike ACD-030's homework
    reminder (explicit 12h throttle in spec), spec/10 §6 sets no reminder
    cadence for overdue loans, so this is safe to run once daily via cron.
    """
    now = timezone.now()
    overdue_loans = Loan.objects.filter(
        foundation_id=foundation_id, status=LoanStatus.ACTIVE, due_at__lt=now,
    ).select_related('item')

    reminded_loan_ids = []
    for loan in overdue_loans:
        loan.status = LoanStatus.OVERDUE
        loan.last_reminded_at = now
        loan.save(update_fields=['status', 'last_reminded_at', 'updated_at'])
        reminded_loan_ids.append(loan.id)

        try:
            from apps.notifications.models import NotificationCategory
            from apps.notifications.services import dispatch_intent

            payload = {'title': loan.item.title, 'due_at': str(loan.due_at)}
            dedupe_key = f"library_loan_due:{loan.id}:{now.date()}"

            if loan.borrower_type == LoanBorrowerType.STUDENT:
                guardian_links = GuardianLink.objects.filter(
                    foundation_id=foundation_id, student_id=loan.borrower_id, deleted_at__isnull=True,
                ).select_related('guardian__person', 'guardian__user')
                for link in guardian_links:
                    guardian = link.guardian
                    if not guardian.user:
                        continue
                    dispatch_intent(
                        foundation_id=foundation_id,
                        category=NotificationCategory.LIBRARY_LOAN_DUE,
                        template_key='campus.library.loan_due',
                        payload=payload,
                        school_id=loan.item.school_id,
                        recipient_user=guardian.user,
                        recipient_phone=getattr(guardian.user, 'phone_e164', '') or '',
                        recipient_email=getattr(guardian.user, 'email', '') or '',
                        recipient_name=(guardian.person.full_name if guardian.person else '') or '',
                        dedupe_key=f"{dedupe_key}:{guardian.id}",
                    )
            else:
                staff = Staff.objects.filter(foundation_id=foundation_id, id=loan.borrower_id).select_related('person', 'user').first()
                if staff and staff.user:
                    dispatch_intent(
                        foundation_id=foundation_id,
                        category=NotificationCategory.LIBRARY_LOAN_DUE,
                        template_key='campus.library.loan_due',
                        payload=payload,
                        school_id=loan.item.school_id,
                        recipient_user=staff.user,
                        recipient_phone=getattr(staff.user, 'phone_e164', '') or '',
                        recipient_email=getattr(staff.user, 'email', '') or '',
                        recipient_name=(staff.person.full_name if staff.person else '') or '',
                        dedupe_key=f"{dedupe_key}:{staff.id}",
                    )
        except Exception as exc:
            logger.warning(f"Error notifying borrower of overdue loan #{loan.id}: {exc}")

    return {'reminded_loan_ids': reminded_loan_ids, 'count': len(reminded_loan_ids)}
