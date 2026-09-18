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

from apps.academic.models import AcademicYear, ClassEnrollment, ClassGroup
from apps.academic.views import StaffConsoleMixin
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
