"""Calendar academic mapping services (spec/14 §6, spec/04).

Provides classification, mapping, and synchronization of external calendar events
(Google Workspace / Microsoft 365) into dated academic models (AcademicCalendarEvent, Exam).
Strictly adheres to:
- Red line: auto_sync_enabled and auto_create_exams default to False (explicit opt-in).
- Tenancy: TenantModel, 3-layer tenancy.
- Soft-delete: Cancelled events are soft-deleted, never hard-deleted.
"""
import re
from datetime import datetime
from typing import Optional, Tuple

from django.db import transaction
from django.utils import timezone

from apps.academic.models import (
    AcademicCalendarEvent,
    AcademicCalendarEventType,
    CalendarAcademicSyncPolicy,
    AcademicYear,
    Term,
    Exam,
    ExamMode,
    ClassSubject,
)
from apps.calendar_sync.models import ExternalCalendarEvent
from apps.identity.models import School, Staff, RoleAssignment


TAG_TYPE_MAP = {
    'UJIAN': (AcademicCalendarEventType.EXAM, True),
    'EXAM': (AcademicCalendarEventType.EXAM, True),
    'UTS': (AcademicCalendarEventType.EXAM, True),
    'UAS': (AcademicCalendarEventType.EXAM, True),
    'PAT': (AcademicCalendarEventType.EXAM, True),
    'PAS': (AcademicCalendarEventType.EXAM, True),
    'LIBUR': (AcademicCalendarEventType.HOLIDAY, True),
    'HOLIDAY': (AcademicCalendarEventType.HOLIDAY, True),
    'CUTI': (AcademicCalendarEventType.HOLIDAY, True),
    'BATAL': (AcademicCalendarEventType.TIMETABLE_EXCEPTION, True),
    'EXCEPTION': (AcademicCalendarEventType.TIMETABLE_EXCEPTION, True),
    'CANCEL': (AcademicCalendarEventType.TIMETABLE_EXCEPTION, True),
    'RAPAT': (AcademicCalendarEventType.STAFF_MEETING, False),
    'MEETING': (AcademicCalendarEventType.STAFF_MEETING, False),
    'ACARA': (AcademicCalendarEventType.SCHOOL_EVENT, False),
    'EVENT': (AcademicCalendarEventType.SCHOOL_EVENT, False),
    'UPACARA': (AcademicCalendarEventType.SCHOOL_EVENT, False),
}

HEURISTIC_KEYWORDS = [
    (AcademicCalendarEventType.EXAM, True, [
        'ujian', 'uts', 'uas', 'penilaian akhir', 'asesmen', 'exam', 'ulangan', 'tryout', 'quiz'
    ]),
    (AcademicCalendarEventType.HOLIDAY, True, [
        'libur', 'cuti bersama', 'holiday', 'tanggal merah'
    ]),
    (AcademicCalendarEventType.TIMETABLE_EXCEPTION, True, [
        'dibatalkan', 'kelas ditiadakan', 'jadwal khusus', 'batal'
    ]),
    (AcademicCalendarEventType.STAFF_MEETING, False, [
        'rapat', 'briefing guru', 'staff meeting', 'mgmp', 'musyawarah guru'
    ]),
    (AcademicCalendarEventType.SCHOOL_EVENT, False, [
        'upacara', 'pentas seni', 'class meeting', 'mos', 'mpls', 'study tour', 'workshop'
    ]),
]


def classify_calendar_event(
    title: str,
    description: str = '',
    policy: Optional[CalendarAcademicSyncPolicy] = None,
) -> Tuple[str, bool]:
    """Classifies an event's title and description into (event_type, affects_attendance).

    Priority:
    1. Explicit tags in brackets (e.g. `[UJIAN]`, `[LIBUR]`, `[RAPAT]`).
    2. Policy-configured custom keywords.
    3. Default heuristic keywords.
    4. Default fallback: (OTHER, False).
    """
    raw_text = f"{title} {description}".strip()
    lower_text = raw_text.lower()

    # 1. Explicit bracket tags in title
    tag_matches = re.findall(r'\[(.*?)\]', title)
    for tag in tag_matches:
        cleaned_tag = tag.strip().upper()
        # If policy defines tag_prefix like '[EDUCORE]', handle sub-tags like '[EDUCORE:UJIAN]' or '[UJIAN]'
        if ':' in cleaned_tag:
            prefix, sub = cleaned_tag.split(':', 1)
            cleaned_tag = sub.strip()
        if cleaned_tag in TAG_TYPE_MAP:
            return TAG_TYPE_MAP[cleaned_tag]

    # 2. Policy-configured custom keywords
    if policy and policy.custom_keywords:
        for etype_str, kw_list in policy.custom_keywords.items():
            if isinstance(kw_list, list):
                for kw in kw_list:
                    if kw.lower() in lower_text:
                        # Determine affects_attendance based on event type
                        affects = etype_str in [
                            AcademicCalendarEventType.EXAM,
                            AcademicCalendarEventType.HOLIDAY,
                            AcademicCalendarEventType.TIMETABLE_EXCEPTION,
                        ]
                        return etype_str, affects

    # 3. Default heuristic keywords
    for etype, affects, keywords in HEURISTIC_KEYWORDS:
        for kw in keywords:
            # Word boundary search for short acronyms like uts, uas
            if len(kw) <= 4:
                if re.search(rf'\b{re.escape(kw)}\b', lower_text):
                    return etype, affects
            else:
                if kw in lower_text:
                    return etype, affects

    # 4. Fallback
    return AcademicCalendarEventType.OTHER, False


def resolve_school_for_user(user, foundation_id: int, preferred_school_id: Optional[int] = None) -> Optional[School]:
    """Resolves the operating School for a given user in a foundation."""
    if preferred_school_id:
        return School.objects.filter(foundation_id=foundation_id, id=preferred_school_id).first()

    staff_profile = Staff.objects.filter(
        foundation_id=foundation_id,
        user=user,
        deleted_at__isnull=True,
    ).select_related('school').first()
    if staff_profile and staff_profile.school:
        return staff_profile.school

    role_assignment = RoleAssignment.objects.filter(
        foundation_id=foundation_id,
        user=user,
        scope_type=RoleAssignment.SCOPE_SCHOOL,
        deleted_at__isnull=True,
    ).first()
    if role_assignment:
        return School.objects.filter(foundation_id=foundation_id, id=role_assignment.scope_id).first()

    return School.objects.filter(foundation_id=foundation_id, is_active=True).first()


def resolve_sync_policy(foundation_id: int, school: Optional[School] = None) -> CalendarAcademicSyncPolicy:
    """Fetches the governing sync policy for a school or the foundation default.
    Returns an inactive policy if none is configured.
    """
    if school:
        policy = CalendarAcademicSyncPolicy.objects.filter(
            foundation_id=foundation_id,
            school=school,
            deleted_at__isnull=True,
        ).first()
        if policy:
            return policy

    policy = CalendarAcademicSyncPolicy.objects.filter(
        foundation_id=foundation_id,
        school__isnull=True,
        deleted_at__isnull=True,
    ).first()
    if policy:
        return policy

    return CalendarAcademicSyncPolicy(
        foundation_id=foundation_id,
        school=school,
        auto_sync_enabled=False,
        auto_create_exams=False,
    )


def map_external_event_to_academic(
    external_event: ExternalCalendarEvent,
    policy: Optional[CalendarAcademicSyncPolicy] = None,
    school: Optional[School] = None,
    dry_run: bool = False,
) -> Optional[AcademicCalendarEvent]:
    """Maps a single ExternalCalendarEvent into an AcademicCalendarEvent.
    
    Adheres strictly to the opt-in guardrail: if auto_sync_enabled is False,
    returns None without mutating data (unless dry_run is True for simulation).
    """
    foundation_id = external_event.foundation_id

    # 1. Resolve school
    if not school:
        user = external_event.connection.user if external_event.connection else None
        if user:
            school = resolve_school_for_user(user, foundation_id)
    if not school:
        return None

    # 2. Resolve policy
    if not policy:
        policy = resolve_sync_policy(foundation_id, school)

    # Opt-in guardrail: no sync unless enabled or dry_run simulation
    if not policy.auto_sync_enabled and not dry_run:
        return None

    # 3. Handle cancellation propagation
    existing = AcademicCalendarEvent.all_tenants.with_deleted().filter(
        foundation_id=foundation_id,
        external_event=external_event,
    ).first()

    if external_event.is_cancelled:
        if existing and not existing.is_deleted:
            if not dry_run:
                existing.delete()  # soft-delete
            return existing
        return None

    # 4. Classification
    event_type, affects_attendance = classify_calendar_event(
        external_event.title,
        external_event.description,
        policy=policy,
    )

    # 5. Resolve AcademicYear and Term
    event_date = external_event.start_at.date()
    academic_year = AcademicYear.objects.filter(
        foundation_id=foundation_id,
        school=school,
        start_date__lte=event_date,
        end_date__gte=event_date,
        deleted_at__isnull=True,
    ).first()

    term = None
    if academic_year:
        term = Term.objects.filter(
            foundation_id=foundation_id,
            academic_year=academic_year,
            start_date__lte=event_date,
            end_date__gte=event_date,
            deleted_at__isnull=True,
        ).first()

    # 6. Exam draft creation if opted-in
    exam = None
    if event_type == AcademicCalendarEventType.EXAM and policy.auto_create_exams:
        user = external_event.connection.user if external_event.connection else None
        staff = Staff.objects.filter(
            foundation_id=foundation_id,
            user=user,
            school=school,
            deleted_at__isnull=True,
        ).first() if user else None

        if staff and term:
            # Look for a class subject matching teacher and term
            class_subject_qs = ClassSubject.objects.filter(
                foundation_id=foundation_id,
                teacher=staff,
                term=term,
                deleted_at__isnull=True,
            ).select_related('subject')

            # Try to match subject from event title
            matched_cs = None
            for cs in class_subject_qs:
                if (cs.subject.name.lower() in external_event.title.lower() or
                        cs.subject.code.lower() in external_event.title.lower()):
                    matched_cs = cs
                    break

            # If not matched by name but teacher only teaches 1 subject in this term
            if not matched_cs and class_subject_qs.count() == 1:
                matched_cs = class_subject_qs.first()

            if matched_cs:
                duration_min = max(15, int((external_event.end_at - external_event.start_at).total_seconds() // 60))
                if not dry_run:
                    exam, _ = Exam.objects.get_or_create(
                        foundation_id=foundation_id,
                        class_subject=matched_cs,
                        title=(external_event.title or 'Ujian')[:128],
                        defaults={
                            'window_start': external_event.start_at,
                            'window_end': external_event.end_at,
                            'duration_min': duration_min,
                            'mode': ExamMode.ONLINE,
                            'published': False,  # Strict draft safety
                        },
                    )
                else:
                    exam = Exam(
                        foundation_id=foundation_id,
                        class_subject=matched_cs,
                        title=(external_event.title or 'Ujian')[:128],
                        window_start=external_event.start_at,
                        window_end=external_event.end_at,
                        duration_min=duration_min,
                        mode=ExamMode.ONLINE,
                        published=False,
                    )

    # 7. Upsert AcademicCalendarEvent
    title = (external_event.title or 'Acara Kalender')[:255]
    desc = external_event.description
    location = (external_event.location or '')[:255]

    if dry_run:
        event = AcademicCalendarEvent(
            foundation_id=foundation_id,
            school=school,
            academic_year=academic_year,
            term=term,
            event_type=event_type,
            title=title,
            description=desc,
            location=location,
            start_at=external_event.start_at,
            end_at=external_event.end_at,
            is_all_day=external_event.is_all_day,
            affects_attendance=affects_attendance,
            source=AcademicCalendarEvent.SOURCE_SYNCED,
            external_event=external_event,
            exam=exam,
        )
        return event

    with transaction.atomic():
        if existing:
            if existing.is_deleted:
                existing.restore()
            existing.school = school
            existing.academic_year = academic_year
            existing.term = term
            existing.event_type = event_type
            existing.title = title
            existing.description = desc
            existing.location = location
            existing.start_at = external_event.start_at
            existing.end_at = external_event.end_at
            existing.is_all_day = external_event.is_all_day
            existing.affects_attendance = affects_attendance
            existing.source = AcademicCalendarEvent.SOURCE_SYNCED
            if exam:
                existing.exam = exam
            existing.save()
            return existing

        created_event = AcademicCalendarEvent.objects.create(
            foundation_id=foundation_id,
            school=school,
            academic_year=academic_year,
            term=term,
            event_type=event_type,
            title=title,
            description=desc,
            location=location,
            start_at=external_event.start_at,
            end_at=external_event.end_at,
            is_all_day=external_event.is_all_day,
            affects_attendance=affects_attendance,
            source=AcademicCalendarEvent.SOURCE_SYNCED,
            external_event=external_event,
            exam=exam,
        )
        return created_event


def sync_external_calendar_events_to_academic(
    foundation_id: int,
    school_id: Optional[int] = None,
    dry_run: bool = False,
    since: Optional[datetime] = None,
) -> dict:
    """Synchronizes external calendar events in the foundation/school into AcademicCalendarEvent.
    Respects CalendarAcademicSyncPolicy.auto_sync_enabled opt-in guardrail.
    """
    school = None
    if school_id:
        school = School.objects.filter(foundation_id=foundation_id, id=school_id).first()

    policy = resolve_sync_policy(foundation_id, school)

    # Opt-in check
    if not policy.auto_sync_enabled and not dry_run:
        return {
            'total_scanned': 0,
            'created': 0,
            'updated': 0,
            'cancelled': 0,
            'skipped': 0,
            'events': [],
            'dry_run': dry_run,
            'policy_enabled': False,
            'message': 'Sinkronisasi kalender akademik dinonaktifkan (kebijakan opt-in belum diaktifkan).',
        }

    events_qs = ExternalCalendarEvent.objects.filter(
        foundation_id=foundation_id,
        deleted_at__isnull=True,
    ).select_related('connection__user')

    if school_id:
        # Filter connections belonging to staff of this school
        staff_user_ids = Staff.objects.filter(
            foundation_id=foundation_id,
            school_id=school_id,
            deleted_at__isnull=True,
        ).values_list('user_id', flat=True)
        events_qs = events_qs.filter(connection__user_id__in=staff_user_ids)

    if since:
        events_qs = events_qs.filter(start_at__gte=since)

    total_scanned = 0
    created_count = 0
    updated_count = 0
    cancelled_count = 0
    skipped_count = 0
    result_events = []

    for ext_event in events_qs:
        total_scanned += 1
        target_school = school or resolve_school_for_user(ext_event.connection.user, foundation_id)
        if not target_school:
            skipped_count += 1
            continue

        target_policy = resolve_sync_policy(foundation_id, target_school)
        if not target_policy.auto_sync_enabled and not dry_run:
            skipped_count += 1
            continue

        existing_id = AcademicCalendarEvent.all_tenants.filter(
            foundation_id=foundation_id,
            external_event=ext_event,
        ).values_list('id', flat=True).first()

        res = map_external_event_to_academic(
            ext_event,
            policy=target_policy,
            school=target_school,
            dry_run=dry_run,
        )

        if ext_event.is_cancelled:
            if existing_id:
                cancelled_count += 1
            else:
                skipped_count += 1
        elif res:
            if existing_id:
                updated_count += 1
            else:
                created_count += 1
            result_events.append({
                'id': res.id if hasattr(res, 'id') else None,
                'external_event_id': ext_event.id,
                'title': res.title,
                'event_type': res.event_type,
                'affects_attendance': res.affects_attendance,
                'start_at': res.start_at.isoformat(),
                'end_at': res.end_at.isoformat(),
                'exam_created': bool(res.exam),
            })
        else:
            skipped_count += 1

    return {
        'total_scanned': total_scanned,
        'created': created_count,
        'updated': updated_count,
        'cancelled': cancelled_count,
        'skipped': skipped_count,
        'events': result_events,
        'dry_run': dry_run,
        'policy_enabled': policy.auto_sync_enabled,
        'message': f"Berhasil memproses {total_scanned} acara kalender ({created_count} dibuat, {updated_count} diperbarui, {cancelled_count} dibatalkan).",
    }
