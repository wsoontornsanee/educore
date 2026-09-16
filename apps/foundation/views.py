"""Views for Foundation portal and School management (spec/02 §7, spec/03 §5)."""
import datetime
from decimal import Decimal
from django.db.models import Avg, Sum
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import generics, status, views, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from apps.core.models import ExportJob
from apps.core.pagination import AuditEventCursorPagination
from apps.core.services import (
    audit,
    create_export_job,
    get_export_allowed_formats,
    get_export_job_status,
    get_export_permission,
    get_export_renderer,
)
from apps.identity.models import Foundation, School
from apps.identity.permissions import HasRequiredPermission
from educore.middleware.tenancy import get_current_foundation_id
from .models import RptFoundationKPI
from .serializers import AuditEventSerializer, FoundationKPISerializer, FoundationSettingsSerializer, SchoolSerializer
from .services import REPORT_KEY_FOUNDATION_DASHBOARD, filter_foundation_audit_events, filter_foundation_kpis

FOUNDATION_KPI_STALE_AFTER_SECONDS = 15 * 60  # FND-006

FOUNDATION_KPI_SUM_FIELDS = [
    'billed', 'collected', 'outstanding', 'ar_0_30', 'ar_31_60', 'ar_61_90',
    'ar_90_plus', 'campus_spend', 'active_students',
]

class SchoolViewSet(viewsets.ModelViewSet):
    """CRUD API for schools under the authenticated user's foundation (spec/02 §7).
    
    Enforces:
    - 3-Layer tenancy isolation: TenantManager filters by thread-local foundation_id.
    - RBAC permissions: school_config.read for reads, school_config.write for mutations.
    - Soft deletion: never hard-delete school rows (TenantModel.deleted_at).
    - Audit logging: writes immutable AuditEvent on every mutating action.
    """
    serializer_class = SchoolSerializer
    permission_classes = [HasRequiredPermission]
    queryset = School.objects.all()
    action_permissions = {
        'list': 'school_config.read',
        'retrieve': 'school_config.read',
        'create': 'school_config.write',
        'update': 'school_config.write',
        'partial_update': 'school_config.write',
        'destroy': 'school_config.write',
    }

    def get_queryset(self):
        # Explicit ordering for CursorPagination
        return School.objects.all().order_by('-created_at')

    def perform_create(self, serializer):
        foundation_id = get_current_foundation_id() or getattr(self.request.user, 'foundation_id', None)
        school = serializer.save(
            foundation_id=foundation_id,
            created_by=str(self.request.user.id),
        )
        audit(
            action="identity.school.created",
            entity_type="School",
            entity_id=str(school.id),
            actor_id=str(self.request.user.id),
            role="foundation_admin",
            foundation_id=foundation_id,
            school_id=school.id,
            ip_address=self.request.META.get('REMOTE_ADDR'),
            diff={"name": {"after": school.name}, "npsn": {"after": school.npsn}},
        )

    def perform_update(self, serializer):
        before_instance = self.get_object()
        before_name = before_instance.name
        school = serializer.save(updated_by=str(self.request.user.id))
        audit(
            action="identity.school.updated",
            entity_type="School",
            entity_id=str(school.id),
            actor_id=str(self.request.user.id),
            role="foundation_admin",
            foundation_id=school.foundation_id,
            school_id=school.id,
            ip_address=self.request.META.get('REMOTE_ADDR'),
            diff={"name": {"before": before_name, "after": school.name}},
        )

    def perform_destroy(self, instance):
        school_id = instance.id
        foundation_id = instance.foundation_id
        # Soft delete
        instance.delete()
        audit(
            action="identity.school.deleted",
            entity_type="School",
            entity_id=str(school_id),
            actor_id=str(self.request.user.id),
            role="foundation_admin",
            foundation_id=foundation_id,
            school_id=school_id,
            ip_address=self.request.META.get('REMOTE_ADDR'),
            diff={"is_deleted": {"before": False, "after": True}},
        )

class FoundationSettingsView(generics.RetrieveUpdateAPIView):
    """View and configure Foundation-level governance settings (spec/03 §2, §5)."""
    serializer_class = FoundationSettingsSerializer
    permission_classes = [HasRequiredPermission]

    def get_required_permission(self) -> str:
        if self.request.method in ['GET', 'HEAD', 'OPTIONS']:
            return 'school_config.read'
        return 'school_config.write'

    def get_object(self):
        foundation_id = get_current_foundation_id() or getattr(self.request.user, 'foundation_id', None)
        return Foundation.objects.get(id=foundation_id)

    def perform_update(self, serializer):
        foundation = serializer.save()
        audit(
            action="foundation.settings.updated",
            entity_type="Foundation",
            entity_id=str(foundation.id),
            actor_id=str(self.request.user.id),
            role="foundation_admin",
            foundation_id=foundation.id,
            ip_address=self.request.META.get('REMOTE_ADDR'),
            diff={
                "legal_name": {"after": foundation.legal_name},
                "approval_threshold": {"after": str(foundation.approval_threshold)},
            },
        )

class FoundationKPIView(views.APIView):
    """Dashboard KPIs for the Foundation portal (spec/03 §4, §5).

    Reads from rpt_foundation_kpis rollup table (FND-005). Never queries
    transactional tables directly.
    """
    permission_classes = [HasRequiredPermission]
    required_permission = 'school_config.read'

    def get(self, request):
        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        if not foundation_id:
            return Response({"detail": "Konteks Yayasan tidak ditemukan."}, status=status.HTTP_400_BAD_REQUEST)

        school_ids = request.query_params.getlist('school_ids') or request.query_params.getlist('school_ids[]')
        school_id = request.query_params.get('school_id')
        if school_id:
            school_ids = [school_id]
        elif len(school_ids) == 1 and ',' in school_ids[0]:
            school_ids = school_ids[0].split(',')

        from_date = request.query_params.get('from')
        to_date = request.query_params.get('to')

        queryset = filter_foundation_kpis(foundation_id, school_ids=school_ids, from_date=from_date, to_date=to_date)

        serializer = FoundationKPISerializer(queryset, many=True)

        computed_ats = [row.computed_at for row in queryset if row.computed_at]
        latest_computed_at = max(computed_ats) if computed_ats else None
        seconds_since_refresh = (timezone.now() - latest_computed_at).total_seconds() if latest_computed_at else None
        freshness = {
            'computed_at': latest_computed_at.isoformat() if latest_computed_at else None,
            'seconds_since_refresh': int(seconds_since_refresh) if seconds_since_refresh is not None else None,
            'stale': seconds_since_refresh is None or seconds_since_refresh > FOUNDATION_KPI_STALE_AFTER_SECONDS,
        }

        compare = None
        if request.query_params.get('compare') == 'prev_period' and from_date and to_date:
            compare = self._compare_prev_period(foundation_id, school_ids, from_date, to_date, queryset)

        return Response({'results': serializer.data, 'freshness': freshness, 'compare': compare})

    def _compare_prev_period(self, foundation_id, school_ids, from_date, to_date, current_queryset):
        """FND-003: absolute + percentage delta against the immediately preceding,
        equal-length period. Skipped (returns None from the caller) unless both
        `from` and `to` are given -- there is no well-defined "previous period"
        for an unbounded window.
        """
        from_dt = datetime.date.fromisoformat(str(from_date))
        to_dt = datetime.date.fromisoformat(str(to_date))

        is_whole_calendar_month = from_dt.day == 1 and to_dt == (
            (from_dt.replace(day=28) + datetime.timedelta(days=4)).replace(day=1) - datetime.timedelta(days=1)
        )
        if is_whole_calendar_month:
            # "current month" style query -> compare to the previous whole calendar month.
            prior_to = from_dt - datetime.timedelta(days=1)
            prior_from = prior_to.replace(day=1)
        else:
            # Arbitrary custom range -> compare to the immediately preceding, equal-length window.
            duration = (to_dt - from_dt).days + 1
            prior_to = from_dt - datetime.timedelta(days=1)
            prior_from = prior_to - datetime.timedelta(days=duration - 1)

        prior_queryset = RptFoundationKPI.objects.filter(
            foundation_id=foundation_id, period_start__gte=prior_from, period_end__lte=prior_to,
        )
        if school_ids:
            prior_queryset = prior_queryset.filter(school_id__in=school_ids)

        current_totals = current_queryset.aggregate(
            **{field: Sum(field) for field in FOUNDATION_KPI_SUM_FIELDS},
            avg_attendance_pct=Avg('avg_attendance_pct'),
        )
        prior_totals = prior_queryset.aggregate(
            **{field: Sum(field) for field in FOUNDATION_KPI_SUM_FIELDS},
            avg_attendance_pct=Avg('avg_attendance_pct'),
        )

        delta = {}
        delta_pct = {}
        for field in FOUNDATION_KPI_SUM_FIELDS + ['avg_attendance_pct']:
            current_value = Decimal(str(current_totals.get(field) or '0.00'))
            prior_value = Decimal(str(prior_totals.get(field) or '0.00'))
            delta[field] = str(current_value - prior_value)
            delta_pct[field] = (
                str(((current_value - prior_value) / prior_value * Decimal('100')).quantize(Decimal('0.01')))
                if prior_value else None
            )

        return {
            'prior_period': {'from': str(prior_from), 'to': str(prior_to)},
            'current': {k: str(v or Decimal('0.00')) for k, v in current_totals.items()},
            'prior': {k: str(v or Decimal('0.00')) for k, v in prior_totals.items()},
            'delta': delta,
            'delta_pct': delta_pct,
        }


class FoundationExportView(views.APIView):
    """POST /foundation/exports — enqueue a PDF/XLSX/CSV export of any
    registered Foundation portal report (FND-014, RPT-002/003, FND-010,
    spec/03 §6). `report` defaults to the dashboard for backward compatibility;
    any other value must be a report_key with a registered export renderer.
    The required permission varies by report (see register_export_permission);
    a report with no registered override falls back to 'school_config.read'."""
    permission_classes = [HasRequiredPermission]

    def get_required_permission(self) -> str:
        report = self.request.data.get('report') or REPORT_KEY_FOUNDATION_DASHBOARD
        if not isinstance(report, str):
            report = REPORT_KEY_FOUNDATION_DASHBOARD
        return get_export_permission(report, default='school_config.read')

    def post(self, request):
        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        if not foundation_id:
            return Response({"detail": "Konteks Yayasan tidak ditemukan."}, status=status.HTTP_400_BAD_REQUEST)

        export_format = (request.data.get('format') or '').upper()
        if export_format not in (ExportJob.FORMAT_PDF, ExportJob.FORMAT_XLSX, ExportJob.FORMAT_CSV):
            return Response(
                {"detail": "Format harus PDF, XLSX, atau CSV."}, status=status.HTTP_400_BAD_REQUEST,
            )

        report = request.data.get('report') or REPORT_KEY_FOUNDATION_DASHBOARD
        if not isinstance(report, str) or not get_export_renderer(report):
            return Response({"detail": f"Laporan '{report}' tidak dikenali."}, status=status.HTTP_400_BAD_REQUEST)

        allowed_formats = get_export_allowed_formats(report)
        if allowed_formats is not None and export_format not in allowed_formats:
            return Response(
                {"detail": f"Format '{export_format}' tidak didukung untuk laporan '{report}'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        job = create_export_job(
            report_key=report,
            export_format=export_format,
            filters=request.data.get('filters') or {},
            foundation_id=foundation_id,
            requested_by=str(request.user.id),
            requested_by_name=getattr(request.user, 'full_name', '') or '',
        )
        return Response({'job_id': job.id}, status=status.HTTP_202_ACCEPTED)


class FoundationExportStatusView(views.APIView):
    """GET /foundation/exports/:job_id — poll an export job's status and, once
    COMPLETED, its 24h-expiring signed download link (RPT-002)."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'school_config.read'

    def get(self, request, job_id):
        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        if not foundation_id:
            return Response({"detail": "Konteks Yayasan tidak ditemukan."}, status=status.HTTP_400_BAD_REQUEST)
        result = get_export_job_status(job_id, foundation_id=foundation_id)
        if result is None:
            return Response({"detail": "Export tidak ditemukan."}, status=status.HTTP_404_NOT_FOUND)
        return Response(result)


class FoundationAuditEventView(generics.ListAPIView):
    """GET /foundation/audit — cursor-paginated, filterable Audit Explorer
    over core.AuditEvent (FND-010, spec/03 §2/§5). Filters: actor, school,
    module (action-prefix), action (exact), entity_type, entity_id, from, to."""
    serializer_class = AuditEventSerializer
    pagination_class = AuditEventCursorPagination
    permission_classes = [HasRequiredPermission]
    required_permission = 'audit_log.read'

    def list(self, request, *args, **kwargs):
        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        if not foundation_id:
            return Response({"detail": "Konteks Yayasan tidak ditemukan."}, status=status.HTTP_400_BAD_REQUEST)
        return super().list(request, *args, **kwargs)

    def get_queryset(self):
        foundation_id = get_current_foundation_id() or getattr(self.request.user, 'foundation_id', None)
        params = self.request.query_params

        school_id = params.get('school')
        if school_id is not None:
            try:
                school_id = int(school_id)
            except ValueError:
                raise ValidationError({"school": "Harus berupa angka."})

        for field in ('from', 'to'):
            value = params.get(field)
            if value and not parse_date(value):
                raise ValidationError({field: "Format tanggal tidak valid (YYYY-MM-DD)."})

        return filter_foundation_audit_events(
            foundation_id,
            actor=params.get('actor'),
            school_id=school_id,
            module=params.get('module'),
            action=params.get('action'),
            entity_type=params.get('entity_type'),
            entity_id=params.get('entity_id'),
            from_date=params.get('from'),
            to_date=params.get('to'),
        )
