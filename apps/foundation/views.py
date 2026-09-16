"""Views for Foundation portal and School management (spec/02 §7, spec/03 §5)."""
import csv
import datetime
from decimal import Decimal
import io
from django.db.models import Avg, Max, Sum
from django.http import HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import generics, status, views, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
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
from .serializers import (
    AuditEventSerializer,
    CampusComparisonSchoolSerializer,
    CampusComparisonSummarySerializer,
    FoundationKPISerializer,
    FoundationSettingsSerializer,
    SchoolSerializer,
)
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


CAMPUS_COMPARISON_METRIC_MAP = {
    'collection_rate': 'collection_rate_pct',
    'collection_rate_pct': 'collection_rate_pct',
    'billed': 'billed',
    'collected': 'collected',
    'outstanding': 'outstanding',
    'attendance_rate': 'avg_attendance_pct',
    'avg_attendance_pct': 'avg_attendance_pct',
    'active_students': 'active_students',
    'campus_spend': 'campus_spend',
    'ar_0_30': 'ar_0_30',
    'ar_31_60': 'ar_31_60',
    'ar_61_90': 'ar_61_90',
    'ar_90_plus': 'ar_90_plus',
    'name': 'school_name',
    'school_name': 'school_name',
}


class CampusComparisonView(views.APIView):
    """Campus Comparison endpoint for ranking and comparing schools (spec/03 §2, §5, FND-004).

    Features:
    - Sourced from rpt_foundation_kpis rollup table (FND-005: never transactional joins).
    - Server-side aggregate supporting up to 25 schools without pagination lag (FND-004).
    - Sortable by any KPI metric (collection_rate, billed, active_students, etc.).
    - Freshness tracking with <= 15 minute staleness indicator (FND-006).
    - Consolidated totals in foundation reporting_currency, per-school figures in school currency (FND-005b).
    - Exportable to CSV and XLSX with mandatory audit headers (FND-014).
    """
    permission_classes = [HasRequiredPermission]
    required_permission = 'school_config.read'

    def perform_content_negotiation(self, request, force=False):
        """Bypass DRF's default renderer filtering for csv/xlsx so custom export responses can stream directly."""
        format_param = request.query_params.get('format', '').lower()
        if format_param in ['csv', 'xlsx']:
            renderers = self.get_renderers()
            return (renderers[0], renderers[0].media_type)
        return super().perform_content_negotiation(request, force=force)

    def get(self, request):
        foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
        if not foundation_id:
            return Response({"detail": "Konteks Yayasan tidak ditemukan."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            foundation = Foundation.objects.get(id=foundation_id)
        except Foundation.DoesNotExist:
            return Response({"detail": "Yayasan tidak ditemukan."}, status=status.HTTP_404_NOT_FOUND)

        # 1. Parse & validate filters
        from_param = request.query_params.get('from')
        to_param = request.query_params.get('to')
        if from_param:
            try:
                datetime.date.fromisoformat(from_param)
            except ValueError:
                return Response(
                    {"detail": "Format tanggal 'from' tidak valid. Gunakan format YYYY-MM-DD."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        if to_param:
            try:
                datetime.date.fromisoformat(to_param)
            except ValueError:
                return Response(
                    {"detail": "Format tanggal 'to' tidak valid. Gunakan format YYYY-MM-DD."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        if from_param and to_param and from_param > to_param:
            return Response(
                {"detail": "Tanggal 'from' tidak boleh melebihi tanggal 'to'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        metric = request.query_params.get('metric', 'collection_rate')
        if metric not in CAMPUS_COMPARISON_METRIC_MAP:
            return Response(
                {"detail": f"Metrik '{metric}' tidak valid. Metrik yang didukung: {', '.join(sorted(CAMPUS_COMPARISON_METRIC_MAP.keys()))}."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        metric_field = CAMPUS_COMPARISON_METRIC_MAP[metric]

        order_param = request.query_params.get('order')
        if order_param:
            order = order_param.lower()
            if order not in ['asc', 'desc']:
                return Response(
                    {"detail": "Parameter order harus 'asc' atau 'desc'."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            order = 'asc' if metric_field == 'school_name' else 'desc'

        school_ids = request.query_params.getlist('school_ids') or request.query_params.getlist('school_ids[]')
        if len(school_ids) == 1 and ',' in school_ids[0]:
            school_ids = [s.strip() for s in school_ids[0].split(',') if s.strip()]

        valid_school_ids = []
        for sid in school_ids:
            try:
                valid_school_ids.append(int(sid))
            except (ValueError, TypeError):
                pass

        # 2. Query schools under this foundation
        schools_qs = School.objects.filter(foundation_id=foundation_id, deleted_at__isnull=True, is_active=True)
        if school_ids:
            schools_qs = schools_qs.filter(id__in=valid_school_ids)
        schools = list(schools_qs.order_by('name'))

        # 3. Query RptFoundationKPI rollup table
        kpis_qs = RptFoundationKPI.objects.filter(foundation_id=foundation_id, school_id__isnull=False)
        if school_ids:
            kpis_qs = kpis_qs.filter(school_id__in=valid_school_ids)
        if from_param:
            kpis_qs = kpis_qs.filter(period_start__gte=from_param)
        if to_param:
            kpis_qs = kpis_qs.filter(period_end__lte=to_param)

        # 4. Freshness evaluation (FND-006)
        computed_ats = [row.computed_at for row in kpis_qs if row.computed_at]
        latest_computed_at = max(computed_ats) if computed_ats else None
        seconds_since_refresh = (timezone.now() - latest_computed_at).total_seconds() if latest_computed_at else None
        freshness = {
            'computed_at': latest_computed_at.isoformat() if latest_computed_at else None,
            'seconds_since_refresh': int(seconds_since_refresh) if seconds_since_refresh is not None else None,
            'stale': seconds_since_refresh is None or seconds_since_refresh > FOUNDATION_KPI_STALE_AFTER_SECONDS,
        }

        # 5. Server-side grouping by school
        school_aggregates = {
            agg['school_id']: agg
            for agg in kpis_qs.values('school_id').annotate(
                billed=Sum('billed'),
                collected=Sum('collected'),
                outstanding=Sum('outstanding'),
                ar_0_30=Sum('ar_0_30'),
                ar_31_60=Sum('ar_31_60'),
                ar_61_90=Sum('ar_61_90'),
                ar_90_plus=Sum('ar_90_plus'),
                campus_spend=Sum('campus_spend'),
                active_students=Max('active_students'),
                avg_attendance_pct=Avg('avg_attendance_pct'),
            )
        }

        # Build school-level results
        school_results = []
        for school in schools:
            agg = school_aggregates.get(school.id, {})
            billed = Decimal(str(agg.get('billed') or '0.00'))
            collected = Decimal(str(agg.get('collected') or '0.00'))
            collection_rate_pct = (
                (collected / billed * Decimal('100.00')).quantize(Decimal('0.01'))
                if billed > Decimal('0.00')
                else Decimal('0.00')
            )
            outstanding = Decimal(str(agg.get('outstanding') or '0.00'))
            ar_0_30 = Decimal(str(agg.get('ar_0_30') or '0.00'))
            ar_31_60 = Decimal(str(agg.get('ar_31_60') or '0.00'))
            ar_61_90 = Decimal(str(agg.get('ar_61_90') or '0.00'))
            ar_90_plus = Decimal(str(agg.get('ar_90_plus') or '0.00'))
            campus_spend = Decimal(str(agg.get('campus_spend') or '0.00'))
            active_students = int(agg.get('active_students') or 0)
            avg_attendance_pct = (
                Decimal(str(agg.get('avg_attendance_pct') or '0.00')).quantize(Decimal('0.01'))
                if agg.get('avg_attendance_pct') is not None
                else Decimal('0.00')
            )

            school_results.append({
                'school_id': school.id,
                'school_name': school.name,
                'npsn': school.npsn or '',
                'level': school.level or '',
                'currency': getattr(school, 'base_currency', 'IDR') or 'IDR',
                'billed': billed,
                'collected': collected,
                'collection_rate_pct': collection_rate_pct,
                'outstanding': outstanding,
                'ar_0_30': ar_0_30,
                'ar_31_60': ar_31_60,
                'ar_61_90': ar_61_90,
                'ar_90_plus': ar_90_plus,
                'campus_spend': campus_spend,
                'active_students': active_students,
                'avg_attendance_pct': avg_attendance_pct,
            })

        # 6. Server-side sorting and ranking
        is_reverse = (order == 'desc')
        if metric_field == 'school_name':
            school_results.sort(key=lambda s: s['school_name'].lower(), reverse=is_reverse)
        else:
            school_results.sort(key=lambda s: s[metric_field], reverse=is_reverse)

        for rank_idx, item in enumerate(school_results, start=1):
            item['rank'] = rank_idx

        # 7. Summary calculation
        tot_billed = sum(s['billed'] for s in school_results) if school_results else Decimal('0.00')
        tot_collected = sum(s['collected'] for s in school_results) if school_results else Decimal('0.00')
        tot_collection_rate = (
            (tot_collected / tot_billed * Decimal('100.00')).quantize(Decimal('0.01'))
            if tot_billed > Decimal('0.00')
            else Decimal('0.00')
        )
        tot_outstanding = sum(s['outstanding'] for s in school_results) if school_results else Decimal('0.00')
        tot_campus_spend = sum(s['campus_spend'] for s in school_results) if school_results else Decimal('0.00')
        tot_students = sum(s['active_students'] for s in school_results) if school_results else 0
        overall_attendance = (
            (sum(s['avg_attendance_pct'] for s in school_results) / Decimal(len(school_results))).quantize(Decimal('0.01'))
            if school_results
            else Decimal('0.00')
        )

        summary_data = {
            'total_schools': len(school_results),
            'billed': tot_billed,
            'collected': tot_collected,
            'collection_rate_pct': tot_collection_rate,
            'outstanding': tot_outstanding,
            'campus_spend': tot_campus_spend,
            'active_students': tot_students,
            'avg_attendance_pct': overall_attendance,
            'reporting_currency': foundation.reporting_currency or 'IDR',
        }

        export_format = request.query_params.get('format', request.query_params.get('export', 'json')).lower()

        # 8. Export handlers (FND-014)
        actor_name = getattr(request.user, 'full_name', None) or getattr(request.user, 'phone_e164', None) or str(request.user.id)
        generated_at = timezone.now().strftime("%Y-%m-%d %H:%M:%S UTC")
        filter_summary = f"Dari={from_param or 'Semua'}, Sampai={to_param or 'Semua'}, Metrik={metric}, Urutan={order}"

        if export_format == 'csv':
            return self._render_csv(school_results, summary_data, generated_at, actor_name, filter_summary)
        elif export_format == 'xlsx':
            return self._render_xlsx(school_results, summary_data, generated_at, actor_name, filter_summary)

        # Default JSON response
        school_serializer = CampusComparisonSchoolSerializer(school_results, many=True)
        summary_serializer = CampusComparisonSummarySerializer(summary_data)
        return Response({
            'summary': summary_serializer.data,
            'results': school_serializer.data,
            'freshness': freshness,
            'filters': {
                'from': from_param,
                'to': to_param,
                'metric': metric,
                'order': order,
                'school_ids': valid_school_ids if school_ids else None,
            },
        })

    def _render_csv(self, school_results, summary_data, generated_at, actor_name, filter_summary):
        output = io.StringIO()
        writer = csv.writer(output)

        # FND-014: Mandatory export audit header
        output.write(f"# Laporan Perbandingan Kampus Yayasan (EduCore)\n")
        output.write(f"# Waktu Dibuat: {generated_at}\n")
        output.write(f"# Pengguna: {actor_name}\n")
        output.write(f"# Filter: {filter_summary}\n")
        output.write("#\n")

        headers = [
            "Peringkat", "Nama Sekolah", "NPSN", "Jenjang", "Mata Uang",
            "Tagihan", "Pembayaran", "Tingkat Penagihan (%)", "Piutang",
            "AR 0-30", "AR 31-60", "AR 61-90", "AR >90",
            "Pengeluaran Kantin", "Siswa Aktif", "Rata-rata Kehadiran (%)",
        ]
        writer.writerow(headers)

        for row in school_results:
            writer.writerow([
                row['rank'],
                row['school_name'],
                row['npsn'],
                row['level'],
                row['currency'],
                f"{row['billed']:.2f}",
                f"{row['collected']:.2f}",
                f"{row['collection_rate_pct']:.2f}",
                f"{row['outstanding']:.2f}",
                f"{row['ar_0_30']:.2f}",
                f"{row['ar_31_60']:.2f}",
                f"{row['ar_61_90']:.2f}",
                f"{row['ar_90_plus']:.2f}",
                f"{row['campus_spend']:.2f}",
                row['active_students'],
                f"{row['avg_attendance_pct']:.2f}",
            ])

        # Summary footer row
        writer.writerow([
            "Total / Rata-rata",
            f"{summary_data['total_schools']} Sekolah",
            "",
            "",
            summary_data['reporting_currency'],
            f"{summary_data['billed']:.2f}",
            f"{summary_data['collected']:.2f}",
            f"{summary_data['collection_rate_pct']:.2f}",
            f"{summary_data['outstanding']:.2f}",
            "",
            "",
            "",
            "",
            f"{summary_data['campus_spend']:.2f}",
            summary_data['active_students'],
            f"{summary_data['avg_attendance_pct']:.2f}",
        ])

        response = HttpResponse(output.getvalue().encode('utf-8-sig'), content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="perbandingan_kampus.csv"'
        return response

    def _render_xlsx(self, school_results, summary_data, generated_at, actor_name, filter_summary):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Perbandingan Kampus"

        # Styles
        title_font = Font(name="Arial", size=14, bold=True, color="C8102E")
        meta_font = Font(name="Arial", size=9, italic=True, color="555555")
        header_font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="1F2937", end_color="1F2937", fill_type="solid")
        summary_font = Font(name="Arial", size=10, bold=True)
        thin_border = Border(
            left=Side(style='thin', color='D1D5DB'),
            right=Side(style='thin', color='D1D5DB'),
            top=Side(style='thin', color='D1D5DB'),
            bottom=Side(style='thin', color='D1D5DB'),
        )
        top_double_border = Border(
            top=Side(style='thin', color='1F2937'),
            bottom=Side(style='double', color='1F2937'),
        )

        # FND-014: Metadata Header Banner
        ws.cell(row=1, column=1, value="Laporan Perbandingan Kampus Yayasan (EduCore)").font = title_font
        ws.cell(row=2, column=1, value=f"Waktu Dibuat : {generated_at}").font = meta_font
        ws.cell(row=3, column=1, value=f"Pengguna     : {actor_name}").font = meta_font
        ws.cell(row=4, column=1, value=f"Filter       : {filter_summary}").font = meta_font

        headers = [
            "Peringkat", "Nama Sekolah", "NPSN", "Jenjang", "Mata Uang",
            "Tagihan", "Pembayaran", "Tingkat Penagihan (%)", "Piutang",
            "AR 0-30", "AR 31-60", "AR 61-90", "AR >90",
            "Pengeluaran Kantin", "Siswa Aktif", "Rata-rata Kehadiran (%)",
        ]
        start_row = 6
        for col_idx, h in enumerate(headers, start=1):
            c = ws.cell(row=start_row, column=col_idx, value=h)
            c.font = header_font
            c.fill = header_fill
            c.alignment = Alignment(horizontal="center" if col_idx in [1, 3, 4, 5] else "left", vertical="center")

        current_row = start_row + 1
        for row in school_results:
            values = [
                row['rank'],
                row['school_name'],
                row['npsn'],
                row['level'],
                row['currency'],
                float(row['billed']),
                float(row['collected']),
                float(row['collection_rate_pct']),
                float(row['outstanding']),
                float(row['ar_0_30']),
                float(row['ar_31_60']),
                float(row['ar_61_90']),
                float(row['ar_90_plus']),
                float(row['campus_spend']),
                row['active_students'],
                float(row['avg_attendance_pct']),
            ]
            for col_idx, val in enumerate(values, start=1):
                c = ws.cell(row=current_row, column=col_idx, value=val)
                c.border = thin_border
                if col_idx in [1, 3, 4, 5]:
                    c.alignment = Alignment(horizontal="center")
                elif col_idx in [6, 7, 9, 10, 11, 12, 13, 14]:
                    c.number_format = '#,##0.00'
                    c.alignment = Alignment(horizontal="right")
                elif col_idx in [8, 16]:
                    c.number_format = '0.00"%"'
                    c.alignment = Alignment(horizontal="right")
                elif col_idx == 15:
                    c.number_format = '#,##0'
                    c.alignment = Alignment(horizontal="right")
            current_row += 1

        # Summary Row
        summary_values = [
            "Total / Rata-rata",
            f"{summary_data['total_schools']} Sekolah",
            "",
            "",
            summary_data['reporting_currency'],
            float(summary_data['billed']),
            float(summary_data['collected']),
            float(summary_data['collection_rate_pct']),
            float(summary_data['outstanding']),
            "",
            "",
            "",
            "",
            float(summary_data['campus_spend']),
            summary_data['active_students'],
            float(summary_data['avg_attendance_pct']),
        ]
        for col_idx, val in enumerate(summary_values, start=1):
            c = ws.cell(row=current_row, column=col_idx, value=val)
            c.font = summary_font
            c.border = top_double_border
            if col_idx in [6, 7, 9, 14]:
                c.number_format = '#,##0.00'
                c.alignment = Alignment(horizontal="right")
            elif col_idx in [8, 16]:
                c.number_format = '0.00"%"'
                c.alignment = Alignment(horizontal="right")
            elif col_idx == 15:
                c.number_format = '#,##0'
                c.alignment = Alignment(horizontal="right")

        # Auto-adjust column widths
        for col in ws.columns:
            max_len = 0
            col_letter = col[0].column_letter
            for cell in col:
                if cell.row < start_row:
                    continue
                val_str = str(cell.value or '')
                if len(val_str) > max_len:
                    max_len = len(val_str)
            ws.column_dimensions[col_letter].width = max(max_len + 4, 12)

        buffer = io.BytesIO()
        wb.save(buffer)
        buffer.seek(0)

        response = HttpResponse(
            buffer.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        response['Content-Disposition'] = 'attachment; filename="perbandingan_kampus.xlsx"'
        return response

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
