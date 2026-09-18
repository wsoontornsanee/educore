"""Universal PII export watermarking utility (spec/14 §3 CMP-016, spec/15 §2 RPT-004).

Stamps rendered export files (XLSX, CSV) with requester identity, timestamp,
and institutional warning per Indonesian UU PDP No. 27/2022.
"""
import io
import logging
from typing import Tuple

from django.utils import timezone

logger = logging.getLogger(__name__)


def build_watermark_text(job, timestamp=None) -> str:
    """Construct standard Indonesian PII export watermark text string."""
    ts = timestamp or timezone.now()
    try:
        ts_local = timezone.localtime(ts)
    except Exception:
        ts_local = ts
    timestamp_str = ts_local.strftime('%Y-%m-%d %H:%M:%S %Z')

    user_display = (job.requested_by_name or '').strip()
    user_id = str(job.requested_by or '').strip()
    if not user_display:
        user_display = f"User #{user_id}" if user_id else "Sistem"

    return f"RAHASIA PII — Diekspor oleh: {user_display} (ID: {user_id or '-'}) pada {timestamp_str} — Job #{job.id}"


def watermark_export_data(data: bytes, content_type: str, filename: str, job) -> Tuple[bytes, str, int]:
    """Apply PII watermark to rendered export bytes.

    Returns:
        (watermarked_bytes, watermark_text, estimated_record_count)
    """
    ts = timezone.now()
    try:
        ts_local = timezone.localtime(ts)
    except Exception:
        ts_local = ts
    timestamp_str = ts_local.strftime('%Y-%m-%d %H:%M:%S %Z')

    user_display = (job.requested_by_name or '').strip()
    user_id = str(job.requested_by or '').strip()
    if not user_display:
        user_display = f"User #{user_id}" if user_id else "Sistem"

    watermark_text = (
        f"RAHASIA PII — Diekspor oleh: {user_display} (ID: {user_id or '-'}) pada {timestamp_str} — Job #{job.id}"
    )

    format_upper = (getattr(job, 'format', '') or '').upper()
    is_xlsx = (
        format_upper == 'XLSX'
        or filename.lower().endswith('.xlsx')
        or 'spreadsheetml' in (content_type or '').lower()
    )
    is_csv = (
        format_upper == 'CSV'
        or filename.lower().endswith('.csv')
        or 'csv' in (content_type or '').lower()
    )

    if is_xlsx:
        return _watermark_xlsx(data, user_display, user_id, timestamp_str, job.id, watermark_text)
    elif is_csv:
        return _watermark_csv(data, user_display, user_id, timestamp_str, job.id, watermark_text)
    else:
        # Fallback for plain text or unsupported formats
        try:
            text = data.decode('utf-8')
            banner = f"\n# {watermark_text}\n"
            watermarked_bytes = (text + banner).encode('utf-8')
            return watermarked_bytes, watermark_text, 0
        except Exception:
            logger.warning(
                "core.watermark: Unsupported format/binary for PII watermarking (job #%s, %s)",
                job.id,
                filename,
            )
            return data, watermark_text, 0


def _watermark_xlsx(
    data: bytes,
    user_display: str,
    user_id: str,
    timestamp_str: str,
    job_id: int,
    watermark_text: str,
) -> Tuple[bytes, str, int]:
    """Watermark an XLSX workbook using openpyxl."""
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data))

    # 1. Update document properties
    wb.properties.creator = user_display
    wb.properties.description = watermark_text

    # 2. Add / update 'Info' sheet with visible audit watermark block
    if 'Info' in wb.sheetnames:
        info_ws = wb['Info']
    else:
        info_ws = wb.create_sheet(title='Info', index=0)

    info_ws.append([])
    info_ws.append(['KLASIFIKASI DATA', 'RAHASIA / PII (UU No. 27/2022 UU PDP)'])
    info_ws.append(['DIEKSPOR OLEH', f"{user_display} (ID: {user_id or '-'})"])
    info_ws.append(['WAKTU EKSPOR', timestamp_str])
    info_ws.append(['ID PEKERJAAN', f"ExportJob#{job_id}"])
    info_ws.append([
        'PERINGATAN',
        'Dilarang menyalin, mendistribusikan, atau menyalahgunakan dokumen ini tanpa hak dan dasar hukum yang sah.',
    ])

    # 3. Add print headers and footers to all worksheets
    for ws in wb.worksheets:
        ws.oddHeader.left.text = f"RAHASIA PII — {user_display}"
        ws.oddHeader.right.text = timestamp_str
        ws.oddFooter.center.text = f"EduCore ExportJob#{job_id} — UU PDP No. 27/2022"
        ws.evenHeader.left.text = ws.oddHeader.left.text
        ws.evenHeader.right.text = ws.oddHeader.right.text
        ws.evenFooter.center.text = ws.oddFooter.center.text

    # 4. Count records across non-info data sheets
    record_count = 0
    for ws in wb.worksheets:
        if ws.title != 'Info' and ws.max_row and ws.max_row > 1:
            record_count += (ws.max_row - 1)

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue(), watermark_text, record_count


def _watermark_csv(
    data: bytes,
    user_display: str,
    user_id: str,
    timestamp_str: str,
    job_id: int,
    watermark_text: str,
) -> Tuple[bytes, str, int]:
    """Watermark a CSV export file by appending a trailing metadata comment banner."""
    # Attempt to decode with utf-8-sig first to preserve BOM if present
    encoding = 'utf-8-sig'
    try:
        text = data.decode(encoding)
    except UnicodeDecodeError:
        encoding = 'utf-8'
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            encoding = 'latin-1'
            text = data.decode(encoding)

    # Estimate record count: non-empty, non-comment lines minus header
    lines = [line for line in text.splitlines() if line.strip() and not line.strip().startswith('#')]
    record_count = max(0, len(lines) - 1)

    banner = (
        f"\n# ----------------------------------------------------------------------\n"
        f"# RAHASIA PII — UU No. 27/2022 (UU PDP)\n"
        f"# Diekspor oleh: {user_display} (ID: {user_id or '-'})\n"
        f"# Waktu Ekspor: {timestamp_str} | Job: ExportJob#{job_id}\n"
        f"# Peringatan: Dilarang menyebarluaskan data ini tanpa hak dan izin sah.\n"
        f"# ----------------------------------------------------------------------\n"
    )

    if not text.endswith('\n'):
        text += '\n'
    text += banner

    return text.encode(encoding), watermark_text, record_count
