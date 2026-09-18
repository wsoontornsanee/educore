"""Read-only Akademik pages for the web console (Siswa & kelas, Jadwal,
Antrean penilaian, Rapor). Design: docs/superpowers/specs/2026-09-19-web-console-akademik-design.md.

Every view is gated by StaffConsoleMixin: the RBAC read permission for the
page plus a linked Staff profile (a guardian holds student_records.read /
grades.read too and must never reach the school-side console).
"""
from django.db.models import Count, Q
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academic.models import (
    AcademicYear,
    ClassEnrollment,
    ClassGroup,
    ClassSubject,
    DayOfWeek,
    HomeworkSubmissionStatus,
    TimetableSlot,
)
from apps.academic.services import get_homework_grading_queue
from apps.identity.console_access import StaffConsoleMixin
from apps.identity.models import Staff
from educore.middleware.tenancy import get_current_foundation_id

NO_STAFF_PROFILE_MESSAGE = _("Akun ini tidak terhubung ke profil staf.")


class ClassListPageView(StaffConsoleMixin, APIView):
    """GET /web/academic/classes/ — class groups with active-enrolment counts.

    Defaults to every active academic year (one per school); ?academic_year=<id>
    shows that year instead."""

    def get_required_permission(self):
        return 'student_records.read'

    def get(self, request):
        if self._resolve_staff(request) is None:
            return Response({'error': NO_STAFF_PROFILE_MESSAGE}, status=status.HTTP_404_NOT_FOUND)

        foundation_id = get_current_foundation_id()
        academic_years = list(
            AcademicYear.objects.filter(foundation_id=foundation_id, deleted_at__isnull=True)
            .select_related('school').order_by('-start_date', 'school__name')
        )

        selected_year_id = request.query_params.get('academic_year', '')
        selected_year_id = int(selected_year_id) if selected_year_id.isdigit() else None

        class_groups = ClassGroup.objects.filter(foundation_id=foundation_id, deleted_at__isnull=True)
        if selected_year_id:
            class_groups = class_groups.filter(academic_year_id=selected_year_id)
        else:
            class_groups = class_groups.filter(academic_year__is_active=True)
        class_groups = class_groups.select_related(
            'school', 'academic_year', 'homeroom_teacher__person',
        ).annotate(
            active_student_count=Count(
                'enrollments',
                filter=Q(enrollments__is_active=True, enrollments__deleted_at__isnull=True),
            ),
        ).order_by('school__name', 'grade_level', 'name')

        return render(request, 'pages/academic_class_list.html', {
            'academic_years': academic_years,
            'selected_year_id': selected_year_id,
            'class_groups': class_groups,
        })


class ClassDetailPageView(StaffConsoleMixin, APIView):
    """GET /web/academic/classes/<id>/ — class header and active roster
    (name + NIS only; never NISN/NIK/contact data)."""

    def get_required_permission(self):
        return 'student_records.read'

    def get(self, request, class_group_id):
        if self._resolve_staff(request) is None:
            return Response({'error': NO_STAFF_PROFILE_MESSAGE}, status=status.HTTP_404_NOT_FOUND)

        foundation_id = get_current_foundation_id()
        class_group = ClassGroup.objects.filter(
            id=class_group_id, foundation_id=foundation_id, deleted_at__isnull=True,
        ).select_related('school', 'academic_year', 'homeroom_teacher__person').first()
        if class_group is None:
            return Response({'error': _("Kelas tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        enrollments = ClassEnrollment.objects.filter(
            class_group=class_group, is_active=True,
            foundation_id=foundation_id, deleted_at__isnull=True,
        ).select_related('student__person').order_by('student__person__full_name', 'student__nis')

        return render(request, 'pages/academic_class_detail.html', {
            'class_group': class_group,
            'enrollments': enrollments,
        })


def _parse_lens(raw):
    """'class:12' / 'teacher:5' -> ('class', 12); anything else -> None."""
    kind, _sep, value = (raw or '').partition(':')
    if kind in ('class', 'teacher') and value.isdigit():
        return kind, int(value)
    return None


def build_timetable_grid(slots):
    """Weekly grid from TimetableSlot rows: Mon-Sat columns (Sunday only when a
    slot exists on it), one row per period_no ordered by number. Each row's
    `cells` is aligned with the returned `days`; a cell is a list of slots
    (normally one; a list so bad data shows instead of vanishing). Row times
    are the earliest start / latest end among that period's slots."""
    slots = list(slots)
    days = [day for day in DayOfWeek if day != DayOfWeek.SUNDAY or any(s.day_of_week == day for s in slots)]
    by_period = {}
    for slot in slots:
        by_period.setdefault(slot.period_no, []).append(slot)
    rows = []
    for period_no in sorted(by_period):
        period_slots = by_period[period_no]
        rows.append({
            'period_no': period_no,
            'start_time': min(s.start_time for s in period_slots),
            'end_time': max(s.end_time for s in period_slots),
            'cells': [[s for s in period_slots if s.day_of_week == day] for day in days],
        })
    return {'days': days, 'rows': rows}


class TimetablePageView(StaffConsoleMixin, APIView):
    """GET /web/academic/timetable/?lens=class:<id>|teacher:<staff id> — weekly
    grid for one class or one teacher. Default: the first class of an active
    academic year. Recurring slots only; substitutions are not overlaid."""

    def get_required_permission(self):
        return 'student_records.read'

    def get(self, request):
        if self._resolve_staff(request) is None:
            return Response({'error': NO_STAFF_PROFILE_MESSAGE}, status=status.HTTP_404_NOT_FOUND)

        foundation_id = get_current_foundation_id()
        class_groups = list(
            ClassGroup.objects.filter(
                foundation_id=foundation_id, deleted_at__isnull=True, academic_year__is_active=True,
            ).select_related('school').order_by('school__name', 'grade_level', 'name')
        )
        teachers = list(
            Staff.objects.filter(
                foundation_id=foundation_id, deleted_at__isnull=True, teaching_assignments__isnull=False,
            ).select_related('person').distinct().order_by('person__full_name')
        )

        lens = _parse_lens(request.query_params.get('lens'))
        if lens is None and class_groups:
            lens = ('class', class_groups[0].id)

        slots = TimetableSlot.objects.filter(foundation_id=foundation_id, deleted_at__isnull=True)
        title = None
        if lens is not None:
            kind, object_id = lens
            if kind == 'class':
                selected = next((c for c in class_groups if c.id == object_id), None) or ClassGroup.objects.filter(
                    id=object_id, foundation_id=foundation_id, deleted_at__isnull=True,
                ).first()
                title = selected.name if selected else None
                slots = slots.filter(class_subject__class_group_id=object_id)
            else:
                selected = next((t for t in teachers if t.id == object_id), None)
                title = selected.person.full_name if selected else None
                slots = slots.filter(class_subject__teacher_id=object_id)
            if selected is None:
                return Response({'error': _("Jadwal tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)
            slots = slots.select_related(
                'class_subject__subject', 'class_subject__teacher__person', 'class_subject__class_group',
            )
            grid = build_timetable_grid(slots)
        else:
            grid = build_timetable_grid([])

        return render(request, 'pages/academic_timetable.html', {
            'class_groups': class_groups,
            'teachers': teachers,
            'lens': lens,
            'title': title,
            'is_teacher_lens': lens is not None and lens[0] == 'teacher',
            'grid': grid,
        })


GRADING_QUEUE_PAGE_SIZE = 200


class GradingQueuePageView(StaffConsoleMixin, APIView):
    """GET /web/academic/grading-queue/?class_subject=<id> — homework
    submissions awaiting grading (SUBMITTED + LATE), oldest first, capped at
    GRADING_QUEUE_PAGE_SIZE rows. Read-only: grading stays on the JSON API."""

    def get_required_permission(self):
        return 'grades.read'

    def get(self, request):
        if self._resolve_staff(request) is None:
            return Response({'error': NO_STAFF_PROFILE_MESSAGE}, status=status.HTTP_404_NOT_FOUND)

        foundation_id = get_current_foundation_id()
        raw_class_subject = request.query_params.get('class_subject', '')
        selected_class_subject_id = int(raw_class_subject) if raw_class_subject.isdigit() else None

        queue = get_homework_grading_queue(foundation_id, class_subject_id=selected_class_subject_id)
        total_count = queue.count()
        late_count = queue.filter(status=HomeworkSubmissionStatus.LATE).count()
        submissions = list(queue[:GRADING_QUEUE_PAGE_SIZE])

        class_subjects = ClassSubject.objects.filter(
            foundation_id=foundation_id, deleted_at__isnull=True, class_group__academic_year__is_active=True,
        ).select_related('subject', 'class_group').order_by('class_group__name', 'subject__name')

        return render(request, 'pages/academic_grading_queue.html', {
            'submissions': submissions,
            'total_count': total_count,
            'late_count': late_count,
            'shown_count': len(submissions),
            'class_subjects': class_subjects,
            'selected_class_subject_id': selected_class_subject_id,
        })
