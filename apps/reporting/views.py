import datetime as _dt
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.identity.console_access import accessible_school_ids, accessible_school_ids_for_all
from apps.identity.models import School
from apps.identity.permissions import HasRequiredPermission
from apps.reporting.health import get_health_metrics
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
from apps.reporting.services import get_metering_roster, get_metering_statement
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


class MeteringStatementView(APIView):
    """GET /metering/statements/?month=YYYY-MM[&foundation_id=] (spec/15 §4, §6, RPT-009).

    The counted students per school for a month, as the subscription invoice basis reads them.
    The foundation is always the caller's own: `foundation_id` is accepted only because the spec
    lists it, and a value other than the caller's foundation is a 404 (never trusted, ARC-004).
    A school-scoped caller sees only their own schools.
    """
    permission_classes = [HasRequiredPermission]
    required_permission = 'reporting.read'

    def get(self, request):
        foundation_id = get_current_foundation_id()

        requested = request.query_params.get('foundation_id')
        if requested and str(requested) != str(foundation_id):
            return Response({'error': _("Yayasan tidak valid.")}, status=status.HTTP_404_NOT_FOUND)

        month, error = _parse_metering_month(request)
        if error:
            return error

        ceiling = accessible_school_ids(request.user, foundation_id, self.required_permission)
        return Response(get_metering_statement(foundation_id, month, school_ids=ceiling))


def _parse_metering_month(request):
    """(first day of the requested month, None), or (None, a 400 response). Defaults to the current month,
    the same one the rollup treats as open."""
    raw_month = request.query_params.get('month')
    if not raw_month:
        return timezone.now().date().replace(day=1), None
    try:
        return _dt.datetime.strptime(raw_month, '%Y-%m').date(), None
    except ValueError:
        return None, Response(
            {'error': _("Format bulan tidak valid (YYYY-MM).")}, status=status.HTTP_400_BAD_REQUEST,
        )


class MeteringRosterView(APIView):
    """GET /metering/statements/roster/?school_id=&month=YYYY-MM (spec/15 RPT-009).

    WHICH students one school's count was made of, so an invoice dispute can be settled. It lists students
    with their NIS, so it needs both `reporting.read` (the metering surface) and `student_records.read`
    (the PII), held for that school. A school-scoped caller asking for another school is refused with a 403 by the
    permission layer (it reads `school_id`); a school of another foundation, or one that does not exist, is a 404.
    A month with no roster (frozen before rosters were captured, or not computed) says so instead of
    returning an empty list. NISN is not returned.
    """
    permission_classes = [HasRequiredPermission]
    required_permission = 'reporting.read'

    def get(self, request):
        foundation_id = get_current_foundation_id()

        school_id = request.query_params.get('school_id')
        if not school_id:
            return Response({'error': _("Parameter school_id wajib diisi.")}, status=status.HTTP_400_BAD_REQUEST)
        month, error = _parse_metering_month(request)
        if error:
            return error

        allowed = accessible_school_ids_for_all(request.user, foundation_id, 'reporting.read', 'student_records.read')
        school = School.objects.filter(id=school_id, foundation_id=foundation_id).first() if str(school_id).isdigit() else None
        if school is None or (allowed is not None and school.id not in allowed):
            return Response({'error': _("Sekolah tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)
        return Response(get_metering_roster(foundation_id, school, month))


class HealthMetricsView(APIView):
    """GET /internal/health-metrics/?foundation_id= (spec/15 §6, RPT-012, RPT-014; platform role only).

    Deliberately not a tenant-derived viewset: the platform role has no tenant context and reads
    every foundation (or one, via `foundation_id`) through `all_tenants` in the read model, so the
    cross-tenant-404 harness does not apply. Access is gated by the platform permission alone.
    """
    permission_classes = [HasRequiredPermission]
    required_permission = 'platform.health.read'

    def get(self, request):
        raw = request.query_params.get('foundation_id')
        foundation_id = None
        if raw not in (None, ''):
            if not (raw.isascii() and raw.isdigit()):
                return Response({'error': _("Parameter foundation_id tidak valid.")}, status=status.HTTP_400_BAD_REQUEST)
            foundation_id = int(raw)
        return Response({'schools': get_health_metrics(foundation_id=foundation_id)})
