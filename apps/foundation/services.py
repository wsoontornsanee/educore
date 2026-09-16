"""Services for the Foundation portal: KPI filtering shared by the live
dashboard view and its exports (spec/03 §4/§5, FND-014, RPT-002/003), plus the
Audit Explorer's filtering and CSV export (spec/03 §2/§5, FND-010)."""
import csv
import io
import logging
from datetime import datetime as dt_datetime, time as dt_time, timedelta

from apps.core.models import AuditEvent, ExportJob
from apps.core.services import (
    register_export_formats,
    register_export_notifier,
    register_export_permission,
    register_export_renderer,
)
from apps.identity.models import School
from django.utils import timezone
from django.utils.dateparse import parse_date

from .models import RptFoundationKPI

logger = logging.getLogger(__name__)

REPORT_KEY_FOUNDATION_DASHBOARD = 'foundation_dashboard'
REPORT_KEY_FOUNDATION_AUDIT = 'foundation_audit'
AUDIT_EXPORT_MAX_ROWS = 100_000  # FND-010: CSV export capped at 100k rows


def filter_foundation_kpis(foundation_id, school_ids=None, from_date=None, to_date=None):
    """Shared FND-001/FND-005 KPI queryset filter, used by both the live
    dashboard (FoundationKPIView) and its PDF/XLSX export renderer below."""
    queryset = RptFoundationKPI.objects.filter(foundation_id=foundation_id)
    if school_ids:
        queryset = queryset.filter(school_id__in=school_ids)
    if from_date:
        queryset = queryset.filter(period_start__gte=from_date)
    if to_date:
        queryset = queryset.filter(period_end__lte=to_date)
    return queryset.order_by('school_id', 'period_start')


_KPI_COLUMNS = [
    ('school_name', 'Sekolah'),
    ('period_start', 'Periode Mulai'),
    ('period_end', 'Periode Selesai'),
    ('billed', 'Ditagih'),
    ('collected', 'Terkumpul'),
    ('outstanding', 'Piutang'),
    ('ar_0_30', 'AR 0-30 Hari'),
    ('ar_31_60', 'AR 31-60 Hari'),
    ('ar_61_90', 'AR 61-90 Hari'),
    ('ar_90_plus', 'AR 90+ Hari'),
    ('active_students', 'Siswa Aktif'),
    ('avg_attendance_pct', 'Rerata Kehadiran %'),
    ('campus_spend', 'Belanja Kantin'),
    ('currency', 'Mata Uang'),
]


def _export_rows(job: ExportJob):
    """Resolve the job's filters into (kpi_rows, school_names_by_id) for rendering."""
    filters = job.filters or {}
    queryset = filter_foundation_kpis(
        foundation_id=job.foundation_id,
        school_ids=filters.get('school_ids') or None,
        from_date=filters.get('from'),
        to_date=filters.get('to'),
    )
    school_ids = {row.school_id for row in queryset if row.school_id}
    school_names = dict(School.all_tenants.filter(id__in=school_ids).values_list('id', 'name'))
    return list(queryset), school_names


def _header_lines(job: ExportJob):
    """RPT-003/FND-014: report name, filters, generation timestamp, actor name."""
    return [
        ("Laporan", "Dasbor Yayasan (Foundation Dashboard)"),
        ("Filter", ", ".join(f"{k}={v}" for k, v in (job.filters or {}).items()) or "(tidak ada)"),
        ("Dibuat Pada", timezone.now().strftime('%Y-%m-%d %H:%M:%S %Z')),
        ("Dibuat Oleh", job.requested_by_name or job.requested_by or '-'),
    ]


def _formula_safe(value):
    """Defuse Excel/Sheets formula injection: a cell value starting with
    =, +, -, or @ is otherwise interpreted as a formula by the spreadsheet
    app that opens the file. Applied to every requester-influenced cell in
    both the XLSX dashboard export (the `filters` header block) and the CSV
    audit export (actor_id/entity_id/diff, which can carry free-text values
    from elsewhere in the system)."""
    text = str(value)
    if text and text[0] in ('=', '+', '-', '@'):
        return "'" + text
    return text


def _render_xlsx(job: ExportJob) -> bytes:
    from openpyxl import Workbook

    rows, school_names = _export_rows(job)
    wb = Workbook()
    ws = wb.active
    ws.title = "Dasbor Yayasan"

    for label, value in _header_lines(job):
        ws.append([label, _formula_safe(value)])
    ws.append([])
    ws.append([label for _key, label in _KPI_COLUMNS])
    for row in rows:
        ws.append([
            school_names.get(row.school_id, str(row.school_id) if row.school_id else 'Semua Sekolah'),
            row.period_start.isoformat(),
            row.period_end.isoformat(),
            str(row.billed),
            str(row.collected),
            str(row.outstanding),
            str(row.ar_0_30),
            str(row.ar_31_60),
            str(row.ar_61_90),
            str(row.ar_90_plus),
            row.active_students,
            str(row.avg_attendance_pct),
            str(row.campus_spend),
            row.currency,
        ])

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _render_pdf_html(job: ExportJob) -> str:
    import html as html_module

    esc = html_module.escape
    rows, school_names = _export_rows(job)
    header_rows = "".join(
        f"<tr><td style='padding:4px 8px;color:#6B615C'>{esc(label)}</td>"
        f"<td style='padding:4px 8px;font-weight:600'>{esc(str(value))}</td></tr>"
        for label, value in _header_lines(job)
    )
    column_headers = "".join(f"<th style='padding:5px 8px;text-align:left'>{esc(label)}</th>" for _key, label in _KPI_COLUMNS)
    data_rows = "".join(
        "<tr>" + "".join(f"<td style='padding:4px 8px;border-top:1px solid #E5DDD9'>{esc(str(cell))}</td>" for cell in (
            school_names.get(row.school_id, str(row.school_id) if row.school_id else 'Semua Sekolah'),
            row.period_start.isoformat(), row.period_end.isoformat(), row.billed, row.collected,
            row.outstanding, row.ar_0_30, row.ar_31_60, row.ar_61_90, row.ar_90_plus,
            row.active_students, row.avg_attendance_pct, row.campus_spend, row.currency,
        )) + "</tr>"
        for row in rows
    )

    return f"""<html><head><meta charset="utf-8"><style>
@page {{ size: A4 landscape; margin: 10mm; }}
body {{ font-family: sans-serif; font-size: 9pt; color: #16110F; }}
table {{ border-collapse: collapse; width: 100%; }}
</style></head><body>
<h2>Dasbor Yayasan (Foundation Dashboard)</h2>
<table>{header_rows}</table>
<br/>
<table><thead><tr>{column_headers}</tr></thead><tbody>{data_rows}</tbody></table>
</body></html>"""


@register_export_renderer(REPORT_KEY_FOUNDATION_DASHBOARD)
def render_foundation_dashboard_export(job: ExportJob):
    """Renders the Foundation Dashboard export (FND-014, RPT-002/003).

    Returns (data: bytes, content_type: str, filename: str) for core's generic
    export-job worker to write through the StoredFile pipeline.
    """
    if job.format == ExportJob.FORMAT_XLSX:
        data = _render_xlsx(job)
        return data, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', f'foundation_dashboard_{job.id}.xlsx'

    html_content = _render_pdf_html(job)
    try:
        from weasyprint import HTML
        data = HTML(string=html_content).write_pdf()
        return data, 'application/pdf', f'foundation_dashboard_{job.id}.pdf'
    except Exception:
        logger.warning("weasyprint unavailable, falling back to HTML foundation dashboard export", exc_info=True)
        return html_content.encode('utf-8'), 'text/html', f'foundation_dashboard_{job.id}.html'


register_export_formats(REPORT_KEY_FOUNDATION_DASHBOARD, {ExportJob.FORMAT_PDF, ExportJob.FORMAT_XLSX})


@register_export_notifier(REPORT_KEY_FOUNDATION_DASHBOARD)
def notify_foundation_dashboard_export_ready(job: ExportJob, download_url: str):
    from apps.identity.models import User
    from apps.notifications.models import NotificationCategory
    from apps.notifications.services import dispatch_intent

    if not job.requested_by:
        return
    recipient = User.all_tenants.filter(pk=job.requested_by, foundation_id=job.foundation_id).first()
    if not recipient:
        return
    dispatch_intent(
        foundation_id=job.foundation_id,
        category=NotificationCategory.EXPORT_READY,
        template_key='core.export.ready',
        payload={'report_name': 'Dasbor Yayasan', 'format': job.format, 'deep_link': download_url},
        recipient_user=recipient,
        immediate=True,
    )


def _day_start(date_value):
    """Parse a date (or 'YYYY-MM-DD' string) into an aware start-of-day datetime
    in the current timezone. Filtering `timestamp` (a DateTimeField) against a
    plain datetime range — instead of `timestamp__date=...`, which wraps the
    indexed column in a DATE() cast — keeps the (foundation_id, timestamp)
    index usable as a range scan. Returns None if the value can't be parsed."""
    if isinstance(date_value, str):
        date_value = parse_date(date_value)
    if not date_value:
        return None
    return timezone.make_aware(dt_datetime.combine(date_value, dt_time.min))


def filter_foundation_audit_events(
    foundation_id, actor=None, school_id=None, module=None, action=None,
    entity_type=None, entity_id=None, from_date=None, to_date=None,
):
    """FND-010 Audit Explorer filter, shared by the live cursor-paginated list
    view and the CSV export renderer below. `module` matches the leading
    `module.` segment of AuditEvent.action (e.g. 'finance.invoice.issue')."""
    queryset = AuditEvent.objects.filter(foundation_id=foundation_id)
    if actor:
        queryset = queryset.filter(actor_id=actor)
    if school_id:
        queryset = queryset.filter(school_id=school_id)
    if module:
        queryset = queryset.filter(action__startswith=f'{module}.')
    if action:
        queryset = queryset.filter(action=action)
    if entity_type:
        queryset = queryset.filter(entity_type=entity_type)
    if entity_id:
        queryset = queryset.filter(entity_id=entity_id)
    start = _day_start(from_date) if from_date else None
    if start:
        queryset = queryset.filter(timestamp__gte=start)
    end = _day_start(to_date) if to_date else None
    if end:
        queryset = queryset.filter(timestamp__lt=end + timedelta(days=1))
    return queryset.order_by('-timestamp')


_AUDIT_CSV_COLUMNS = [
    ('id', 'ID'),
    ('timestamp', 'Waktu'),
    ('actor_id', 'Aktor'),
    ('role', 'Peran'),
    ('school_id', 'Sekolah'),
    ('ip_address', 'Alamat IP'),
    ('action', 'Aksi'),
    ('entity_type', 'Tipe Entitas'),
    ('entity_id', 'ID Entitas'),
    ('diff', 'Perubahan'),
]


@register_export_renderer(REPORT_KEY_FOUNDATION_AUDIT)
def render_foundation_audit_export(job: ExportJob):
    """Renders the Audit Explorer CSV export (FND-010). Capped at
    AUDIT_EXPORT_MAX_ROWS regardless of how many rows match the filter."""
    filters = job.filters or {}
    queryset = filter_foundation_audit_events(
        foundation_id=job.foundation_id,
        actor=filters.get('actor'),
        school_id=filters.get('school'),
        module=filters.get('module'),
        action=filters.get('action'),
        entity_type=filters.get('entity_type'),
        entity_id=filters.get('entity_id'),
        from_date=filters.get('from'),
        to_date=filters.get('to'),
    )[:AUDIT_EXPORT_MAX_ROWS]

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([label for _key, label in _AUDIT_CSV_COLUMNS])
    for row in queryset:
        writer.writerow([_formula_safe(getattr(row, key)) for key, _label in _AUDIT_CSV_COLUMNS])

    return buffer.getvalue().encode('utf-8-sig'), 'text/csv', f'foundation_audit_{job.id}.csv'


register_export_formats(REPORT_KEY_FOUNDATION_AUDIT, {ExportJob.FORMAT_CSV})
register_export_permission(REPORT_KEY_FOUNDATION_AUDIT, 'audit_log.read')
