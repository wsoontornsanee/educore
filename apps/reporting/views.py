import datetime as _dt
from django.utils.translation import gettext_lazy as _
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.identity.models import School
from apps.identity.permissions import HasRequiredPermission
from apps.reporting.models import (
    RptAcademicPerformance,
    RptActiveStudent,
    RptDailyAttendance,
    RptDailyFinance,
    RptWalletActivity,
)
from apps.reporting.serializers import (
    RptAcademicPerformanceSerializer,
    RptActiveStudentSerializer,
    RptDailyAttendanceSerializer,
    RptDailyFinanceSerializer,
    RptWalletActivitySerializer,
)
from educore.middleware.tenancy import get_current_foundation_id


class _DateRangeParseError(Exception):
    def __init__(self, response):
        self.response = response


def _apply_date_range(request, rows, field='date', from_param='date_from', to_param='date_to'):
    """Shared date-range filtering for every rpt_* list view."""
    from_value = request.query_params.get(from_param)
    if from_value:
        try:
            rows = rows.filter(**{f'{field}__gte': _dt.date.fromisoformat(from_value)})
        except ValueError:
            raise _DateRangeParseError(Response({'error': _("Format %(param)s tidak valid (YYYY-MM-DD).") % {'param': from_param}}, status=status.HTTP_400_BAD_REQUEST))

    to_value = request.query_params.get(to_param)
    if to_value:
        try:
            rows = rows.filter(**{f'{field}__lte': _dt.date.fromisoformat(to_value)})
        except ValueError:
            raise _DateRangeParseError(Response({'error': _("Format %(param)s tidak valid (YYYY-MM-DD).") % {'param': to_param}}, status=status.HTTP_400_BAD_REQUEST))

    return rows


class WalletActivityReportView(APIView):
    """GET /reporting/wallet-activity/?school_id=&date_from=&date_to= (spec/15 §2, RPT-001, RPT-005)."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'reporting.read'

    def get(self, request):
        foundation_id = get_current_foundation_id()

        school_id = request.query_params.get('school_id')
        if not school_id:
            return Response({'error': _("Parameter school_id wajib diisi.")}, status=status.HTTP_400_BAD_REQUEST)
        school = School.objects.filter(id=school_id, foundation_id=foundation_id).first()
        if not school:
            return Response({'error': _("Sekolah tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        rows = RptWalletActivity.objects.filter(foundation_id=foundation_id, school=school)
        try:
            rows = _apply_date_range(request, rows)
        except _DateRangeParseError as e:
            return e.response

        rows = rows.order_by('date')
        return Response({'rows': RptWalletActivitySerializer(rows, many=True).data})


class DailyAttendanceReportView(APIView):
    """GET /reporting/daily-attendance/?school_id=&date_from=&date_to=&class_group_id= (spec/15 §2, §3)."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'reporting.read'

    def get(self, request):
        foundation_id = get_current_foundation_id()

        school_id = request.query_params.get('school_id')
        if not school_id:
            return Response({'error': _("Parameter school_id wajib diisi.")}, status=status.HTTP_400_BAD_REQUEST)
        school = School.objects.filter(id=school_id, foundation_id=foundation_id).first()
        if not school:
            return Response({'error': _("Sekolah tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        rows = RptDailyAttendance.objects.filter(foundation_id=foundation_id, school=school)
        try:
            rows = _apply_date_range(request, rows)
        except _DateRangeParseError as e:
            return e.response

        class_group_id = request.query_params.get('class_group_id')
        if class_group_id:
            rows = rows.filter(class_group_id=class_group_id)

        rows = rows.order_by('date', 'class_group_id')
        return Response({'rows': RptDailyAttendanceSerializer(rows, many=True).data})


class AcademicPerformanceReportView(APIView):
    """GET /reporting/academic-performance/?school_id=&term_id=&class_group_id= (spec/15 §2, §3)."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'reporting.read'

    def get(self, request):
        foundation_id = get_current_foundation_id()

        school_id = request.query_params.get('school_id')
        if not school_id:
            return Response({'error': _("Parameter school_id wajib diisi.")}, status=status.HTTP_400_BAD_REQUEST)
        school = School.objects.filter(id=school_id, foundation_id=foundation_id).first()
        if not school:
            return Response({'error': _("Sekolah tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        rows = RptAcademicPerformance.objects.filter(foundation_id=foundation_id, school=school)

        term_id = request.query_params.get('term_id')
        if term_id:
            rows = rows.filter(term_id=term_id)

        class_group_id = request.query_params.get('class_group_id')
        if class_group_id:
            rows = rows.filter(class_group_id=class_group_id)

        rows = rows.order_by('term_id', 'class_group_id', 'subject_id')
        return Response({'rows': RptAcademicPerformanceSerializer(rows, many=True).data})


class ActiveStudentsReportView(APIView):
    """GET /reporting/active-students/?school_id=&month_from=&month_to= (spec/15 §2, §4, RPT-007/008)."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'reporting.read'

    def get(self, request):
        foundation_id = get_current_foundation_id()

        school_id = request.query_params.get('school_id')
        if not school_id:
            return Response({'error': _("Parameter school_id wajib diisi.")}, status=status.HTTP_400_BAD_REQUEST)
        school = School.objects.filter(id=school_id, foundation_id=foundation_id).first()
        if not school:
            return Response({'error': _("Sekolah tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        rows = RptActiveStudent.objects.filter(foundation_id=foundation_id, school=school)
        try:
            rows = _apply_date_range(request, rows, field='month', from_param='month_from', to_param='month_to')
        except _DateRangeParseError as e:
            return e.response

        rows = rows.order_by('month')
        return Response({'rows': RptActiveStudentSerializer(rows, many=True).data})


class DailyFinanceReportView(APIView):
    """GET /reporting/daily-finance/?school_id=&date_from=&date_to= (spec/15 §2, §3)."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'reporting.read'

    def get(self, request):
        foundation_id = get_current_foundation_id()

        school_id = request.query_params.get('school_id')
        if not school_id:
            return Response({'error': _("Parameter school_id wajib diisi.")}, status=status.HTTP_400_BAD_REQUEST)
        school = School.objects.filter(id=school_id, foundation_id=foundation_id).first()
        if not school:
            return Response({'error': _("Sekolah tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        rows = RptDailyFinance.objects.filter(foundation_id=foundation_id, school=school)
        try:
            rows = _apply_date_range(request, rows)
        except _DateRangeParseError as e:
            return e.response

        rows = rows.order_by('date')
        return Response({'rows': RptDailyFinanceSerializer(rows, many=True).data})
