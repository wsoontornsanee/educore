"""Read-only Akademik pages for the web console (Siswa & kelas, Jadwal,
Antrean penilaian, Rapor). Design: docs/superpowers/specs/2026-09-19-web-console-akademik-design.md.

Every view is gated by StaffConsoleMixin: the RBAC read permission for the
page plus a linked Staff profile (a guardian holds student_records.read /
grades.read too and must never reach the school-side console).
"""
from decimal import Decimal

from django.db.models import Count, Q
from django.http import HttpResponse
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
    ReportCard,
    ReportCardStatus,
    Term,
    TimetableSlot,
)
from apps.academic.services import compute_descriptor, get_homework_grading_queue, render_report_card_html
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


REPORT_CARD_PAGE_SIZE = 200

# Fixed display order and id-ID labels for the rapor attendance summary
# (AttendanceStatus's own labels mix Indonesian and English).
ATTENDANCE_SUMMARY_LABELS = (
    ('HADIR', _("Hadir")),
    ('TERLAMBAT', _("Terlambat")),
    ('SAKIT', _("Sakit")),
    ('IZIN', _("Izin")),
    ('ALPA', _("Tanpa keterangan")),
    ('DISPEN', _("Dispensasi")),
)


class ReportCardListPageView(StaffConsoleMixin, APIView):
    """GET /web/academic/report-cards/?term=<id>&class_group=<id> — current
    report cards with per-status counts. Default term: the most recent term of
    an active academic year; an explicit empty `term=` means every term.
    Read-only: generate/approve/publish stay on the JSON API."""

    def get_required_permission(self):
        return 'grades.read'

    def get(self, request):
        if self._resolve_staff(request) is None:
            return Response({'error': NO_STAFF_PROFILE_MESSAGE}, status=status.HTTP_404_NOT_FOUND)

        foundation_id = get_current_foundation_id()
        terms = list(
            Term.objects.filter(foundation_id=foundation_id, deleted_at__isnull=True)
            .select_related('academic_year__school').order_by('-start_date', 'academic_year__school__name')
        )
        class_groups = list(
            ClassGroup.objects.filter(
                foundation_id=foundation_id, deleted_at__isnull=True, academic_year__is_active=True,
            ).select_related('school').order_by('school__name', 'grade_level', 'name')
        )

        raw_term = request.query_params.get('term')
        if raw_term is None:
            default_term = next((t for t in terms if t.academic_year.is_active), None)
            selected_term_id = default_term.id if default_term else None
        else:
            selected_term_id = int(raw_term) if raw_term.isdigit() else None
        raw_class = request.query_params.get('class_group', '')
        selected_class_group_id = int(raw_class) if raw_class.isdigit() else None

        cards = ReportCard.objects.filter(foundation_id=foundation_id, deleted_at__isnull=True, is_current=True)
        if selected_term_id:
            cards = cards.filter(term_id=selected_term_id)
        if selected_class_group_id:
            cards = cards.filter(class_group_id=selected_class_group_id)

        counts_by_status = dict(cards.values_list('status').annotate(n=Count('id')).order_by())
        status_counts = [
            {'status': value, 'label': label, 'count': counts_by_status.get(value, 0)}
            for value, label in ReportCardStatus.choices
        ]
        total_count = sum(counts_by_status.values())
        report_cards = list(
            cards.select_related('student__person', 'class_group', 'term')
            .order_by('class_group__name', 'student__person__full_name', 'id')[:REPORT_CARD_PAGE_SIZE]
        )

        return render(request, 'pages/academic_report_card_list.html', {
            'terms': terms,
            'class_groups': class_groups,
            'selected_term_id': selected_term_id,
            'selected_class_group_id': selected_class_group_id,
            'status_counts': status_counts,
            'total_count': total_count,
            'shown_count': len(report_cards),
            'report_cards': report_cards,
        })


class _ReportCardAccessMixin(StaffConsoleMixin):
    def get_required_permission(self):
        return 'grades.read'

    def _get_report_card(self, request, report_card_id):
        """The report card, or None for a missing Staff profile / unknown / cross-tenant id."""
        if self._resolve_staff(request) is None:
            return None
        return ReportCard.objects.filter(
            id=report_card_id, foundation_id=get_current_foundation_id(), deleted_at__isnull=True,
        ).select_related(
            'student__person', 'term', 'class_group__school', 'class_group__homeroom_teacher__person',
        ).first()


class ReportCardDetailPageView(_ReportCardAccessMixin, APIView):
    """GET /web/academic/report-cards/<id>/ — the frozen rapor snapshot inside
    the console chrome (grades, attendance, narrative), with a link to the
    printable branded layout."""

    def get(self, request, report_card_id):
        report_card = self._get_report_card(request, report_card_id)
        if report_card is None:
            return Response({'error': _("Rapor tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        grade_rows = []
        for entry in report_card.grades_snapshot:
            grade = entry.get('grade')
            grade_rows.append({
                'subject': entry.get('subject', ''),
                'grade': f"{Decimal(str(grade)):.0f}" if grade is not None else None,
                'descriptor': compute_descriptor(Decimal(str(grade)), Decimal('100')) if grade is not None else None,
                'narrative': entry.get('objective_narrative', ''),
            })
        attendance_summary = report_card.attendance_summary
        attendance_rows = [
            {'label': label, 'days': attendance_summary[key]}
            for key, label in ATTENDANCE_SUMMARY_LABELS if key in attendance_summary
        ]

        return render(request, 'pages/academic_report_card_detail.html', {
            'report_card': report_card,
            'grade_rows': grade_rows,
            'attendance_rows': attendance_rows,
        })


class ReportCardPrintPageView(_ReportCardAccessMixin, APIView):
    """GET /web/academic/report-cards/<id>/print/ — the branded printable
    rapor layout (the same HTML the PDF is rendered from), opened in a new tab."""

    def get(self, request, report_card_id):
        report_card = self._get_report_card(request, report_card_id)
        if report_card is None:
            return Response({'error': _("Rapor tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)
        return HttpResponse(render_report_card_html(report_card))
