"""Services for the Foundation portal: KPI filtering shared by the live
dashboard view and its exports (spec/03 §4/§5, FND-014, RPT-002/003)."""
import io
import logging

from apps.core.models import ExportJob
from apps.core.services import register_export_notifier, register_export_renderer
from apps.identity.models import School
from django.utils import timezone

from .models import RptFoundationKPI

logger = logging.getLogger(__name__)

REPORT_KEY_FOUNDATION_DASHBOARD = 'foundation_dashboard'


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


def _xlsx_safe(value):
    """Defuse Excel/Sheets formula injection: a cell value starting with
    =, +, -, or @ is otherwise interpreted as a formula by the spreadsheet
    app that opens the file. The only untrusted input reaching XLSX cells is
    the requester-supplied `filters` dict, via the header block below."""
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
        ws.append([label, _xlsx_safe(value)])
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


def _get_foundation_enrolment_pipeline_impl(
    foundation_id: int,
    school_ids=None,
    from_date=None,
    to_date=None,
    group_by: str = 'campus',
) -> dict:
    import datetime
    from apps.core.models import DomainEvent
    from apps.identity.models import School, Student
    from django.db.models import Prefetch

    if to_date is None:
        to_date = timezone.now().date()
    elif isinstance(to_date, str):
        to_date = datetime.date.fromisoformat(to_date)

    if from_date is None:
        try:
            from apps.academic.models import AcademicYear
            ay = AcademicYear.objects.filter(foundation_id=foundation_id, is_active=True).order_by('-start_date').first()
            from_date = ay.start_date if ay and ay.start_date else to_date.replace(month=1, day=1)
        except Exception:
            from_date = to_date.replace(month=1, day=1)
    elif isinstance(from_date, str):
        from_date = datetime.date.fromisoformat(from_date)

    group_by = (group_by or 'campus').lower().strip()
    if group_by not in ('campus', 'grade'):
        raise ValueError("Parameter group_by tidak valid. Pilihan yang didukung: campus, grade.")

    schools_qs = School.objects.filter(foundation_id=foundation_id, is_active=True)
    if school_ids:
        schools_qs = schools_qs.filter(id__in=school_ids)
    schools = list(schools_qs.order_by('name'))

    if not schools:
        return {
            'group_by': group_by,
            'period': {'from': str(from_date), 'to': str(to_date)},
            'summary': {
                'total_schools': 0,
                'total_prospects': 0,
                'total_accepted': 0,
                'total_active': 0,
                'total_churned': 0,
                'conversion_rate_pct': 0.0,
                'retention_rate_pct': 0.0,
            },
            'data': [],
        }

    # 1. Identify accepted student IDs in period [from_date, to_date]
    accepted_student_ids = set()

    # A. Status change events PROSPECT -> ACTIVE
    status_events = DomainEvent.objects.filter(
        foundation_id=foundation_id,
        name='identity.student.status_changed',
        occurred_at__date__gte=from_date,
        occurred_at__date__lte=to_date,
    )
    for ev in status_events:
        p = ev.payload or {}
        if p.get('new_status') == Student.STATUS_ACTIVE and p.get('old_status') == Student.STATUS_PROSPECT:
            if p.get('student_id'):
                accepted_student_ids.add(int(p['student_id']))

    # B. Class enrollment date in period
    try:
        from apps.academic.models import ClassEnrollment
        enrolled_ids = ClassEnrollment.objects.filter(
            foundation_id=foundation_id,
            student__school__in=schools,
            enrolled_at__gte=from_date,
            enrolled_at__lte=to_date,
        ).values_list('student_id', flat=True)
        accepted_student_ids.update(enrolled_ids)
    except Exception:
        pass

    # C. Directly created as ACTIVE in period
    direct_active = Student.objects.filter(
        foundation_id=foundation_id,
        school__in=schools,
        status=Student.STATUS_ACTIVE,
        created_at__date__gte=from_date,
        created_at__date__lte=to_date,
    ).values_list('id', flat=True)
    accepted_student_ids.update(direct_active)

    # 2. Identify churned student IDs in period [from_date, to_date]
    churned_student_ids = set()
    for ev in status_events:
        p = ev.payload or {}
        if p.get('new_status') in (Student.STATUS_INACTIVE, Student.STATUS_TRANSFERRED_OUT):
            if p.get('student_id'):
                churned_student_ids.add(int(p['student_id']))

    churned_students = Student.objects.filter(
        foundation_id=foundation_id,
        school__in=schools,
        status__in=[Student.STATUS_INACTIVE, Student.STATUS_TRANSFERRED_OUT],
        updated_at__date__gte=from_date,
        updated_at__date__lte=to_date,
    ).values_list('id', flat=True)
    churned_student_ids.update(churned_students)

    # 3. Fetch all students for target schools with active class group
    try:
        from apps.academic.models import ClassEnrollment, ClassGroup
        active_enrollments_qs = ClassEnrollment.objects.filter(
            foundation_id=foundation_id, is_active=True
        ).select_related('class_group')
        students = Student.objects.filter(
            foundation_id=foundation_id, school__in=schools
        ).prefetch_related(Prefetch('class_enrollments', queryset=active_enrollments_qs, to_attr='active_enrollments'))
    except Exception:
        students = Student.objects.filter(foundation_id=foundation_id, school__in=schools)

    school_grades = {s.id: set() for s in schools}
    try:
        from apps.academic.models import ClassGroup
        for s_id, gl in ClassGroup.objects.filter(foundation_id=foundation_id, school__in=schools).values_list('school_id', 'grade_level'):
            if gl is not None:
                school_grades[s_id].add(gl)
    except Exception:
        pass

    # 4. Tally metrics per (school_id, grade_level)
    matrix = {}  # (school_id, grade_level) -> {'prospects': 0, 'accepted': 0, 'active': 0, 'churned': 0}

    for st in students:
        s_id = st.school_id
        gr = st.effective_grade_level
        if gr is not None and s_id in school_grades:
            school_grades[s_id].add(gr)

        key = (s_id, gr)
        if key not in matrix:
            matrix[key] = {'prospects': 0, 'accepted': 0, 'active': 0, 'churned': 0}

        # Prospects in pipeline
        if st.status == Student.STATUS_PROSPECT and st.created_at.date() <= to_date:
            matrix[key]['prospects'] += 1

        # Accepted
        if st.id in accepted_student_ids:
            matrix[key]['accepted'] += 1

        # Active
        if st.status == Student.STATUS_ACTIVE and st.created_at.date() <= to_date:
            matrix[key]['active'] += 1

        # Churned
        if st.id in churned_student_ids:
            matrix[key]['churned'] += 1

    def calc_rates(p, a, act, ch):
        conv = round((a / (p + a) * 100), 2) if (p + a) > 0 else 0.0
        ret = round((act / (act + ch) * 100), 2) if (act + ch) > 0 else (100.0 if act > 0 else 0.0)
        return conv, ret

    tot_p = 0
    tot_a = 0
    tot_act = 0
    tot_ch = 0

    if group_by == 'campus':
        data = []
        for s in schools:
            s_p = 0
            s_a = 0
            s_act = 0
            s_ch = 0
            grades_list = []

            all_gr = sorted(school_grades.get(s.id, set()))
            if (s.id, None) in matrix:
                all_gr.append(None)

            for gr in all_gr:
                cell = matrix.get((s.id, gr), {'prospects': 0, 'accepted': 0, 'active': 0, 'churned': 0})
                cp, ca, cact, cch = cell['prospects'], cell['accepted'], cell['active'], cell['churned']
                c_conv, c_ret = calc_rates(cp, ca, cact, cch)
                s_p += cp
                s_a += ca
                s_act += cact
                s_ch += cch
                grades_list.append({
                    'grade_level': gr,
                    'grade_name': f"Kelas {gr}" if gr is not None else "Belum Ditentukan",
                    'prospects': cp,
                    'accepted': ca,
                    'active': cact,
                    'churned': cch,
                    'conversion_rate_pct': c_conv,
                    'retention_rate_pct': c_ret,
                })

            s_conv, s_ret = calc_rates(s_p, s_a, s_act, s_ch)
            tot_p += s_p
            tot_a += s_a
            tot_act += s_act
            tot_ch += s_ch

            data.append({
                'school_id': s.id,
                'school_name': s.name,
                'npsn': s.npsn,
                'level': s.level,
                'prospects': s_p,
                'accepted': s_a,
                'active': s_act,
                'churned': s_ch,
                'conversion_rate_pct': s_conv,
                'retention_rate_pct': s_ret,
                'grades': grades_list,
            })
    else:
        # group_by == 'grade'
        all_grades = sorted({gr for s_id in school_grades for gr in school_grades[s_id] if gr is not None})
        has_none = any(gr is None for (s_id, gr) in matrix.keys())
        if has_none:
            all_grades.append(None)

        data = []
        for gr in all_grades:
            g_p = 0
            g_a = 0
            g_act = 0
            g_ch = 0
            campuses_list = []

            for s in schools:
                cell = matrix.get((s.id, gr), {'prospects': 0, 'accepted': 0, 'active': 0, 'churned': 0})
                cp, ca, cact, cch = cell['prospects'], cell['accepted'], cell['active'], cell['churned']
                c_conv, c_ret = calc_rates(cp, ca, cact, cch)
                g_p += cp
                g_a += ca
                g_act += cact
                g_ch += cch
                campuses_list.append({
                    'school_id': s.id,
                    'school_name': s.name,
                    'npsn': s.npsn,
                    'level': s.level,
                    'prospects': cp,
                    'accepted': ca,
                    'active': cact,
                    'churned': cch,
                    'conversion_rate_pct': c_conv,
                    'retention_rate_pct': c_ret,
                })

            g_conv, g_ret = calc_rates(g_p, g_a, g_act, g_ch)
            tot_p += g_p
            tot_a += g_a
            tot_act += g_act
            tot_ch += g_ch

            data.append({
                'grade_level': gr,
                'grade_name': f"Kelas {gr}" if gr is not None else "Belum Ditentukan",
                'prospects': g_p,
                'accepted': g_a,
                'active': g_act,
                'churned': g_ch,
                'conversion_rate_pct': g_conv,
                'retention_rate_pct': g_ret,
                'campuses': campuses_list,
            })

    tot_conv, tot_ret = calc_rates(tot_p, tot_a, tot_act, tot_ch)
    summary = {
        'total_schools': len(schools),
        'total_prospects': tot_p,
        'total_accepted': tot_a,
        'total_active': tot_act,
        'total_churned': tot_ch,
        'conversion_rate_pct': tot_conv,
        'retention_rate_pct': tot_ret,
    }

    return {
        'group_by': group_by,
        'period': {'from': str(from_date), 'to': str(to_date)},
        'summary': summary,
        'data': data,
    }


def get_foundation_enrolment_pipeline(
    foundation_id: int,
    school_ids=None,
    from_date=None,
    to_date=None,
    group_by: str = 'campus',
) -> dict:
    """Computes the Enrolment Pipeline metrics for the Foundation portal (spec/03 §2, §5).

    Tracks admissions and retention metrics:
    - prospects: Prospective students in the pipeline (status=PROSPECT).
    - accepted: Students admitted/accepted during [from_date, to_date].
    - active: Currently active students as of to_date.
    - churned: Students who became inactive or transferred out during [from_date, to_date].
    - conversion_rate_pct: accepted / (prospects + accepted) * 100
    - retention_rate_pct: active / (active + churned) * 100

    Supports grouping by 'campus' or 'grade'.
    """
    from educore.middleware.tenancy import tenant_context

    with tenant_context(foundation_id):
        return _get_foundation_enrolment_pipeline_impl(
            foundation_id=foundation_id,
            school_ids=school_ids,
            from_date=from_date,
            to_date=to_date,
            group_by=group_by,
        )

