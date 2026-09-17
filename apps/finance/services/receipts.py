"""
Official Payment Receipt (Kwitansi Pembayaran) generation service (spec/08 PAR-009, spec/06 FIN-019).
Renders and persists downloadable/shareable payment receipts using the standard
weasyprint-with-HTML-fallback pattern catalogued in StoredFile.
"""
import datetime
import html
import logging
from decimal import Decimal
from typing import Any, Dict, Optional

from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core import storage
from apps.core.services import audit, build_signed_download, write_generated_file
from apps.finance.models import Payment, PaymentAllocation, PaymentStatus
from apps.finance.services.ledger import get_next_receipt_number

logger = logging.getLogger(__name__)

MONTH_NAMES_ID = [
    '', 'Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni',
    'Juli', 'Agustus', 'September', 'Oktober', 'November', 'Desember'
]


def terbilang(amount: Any) -> str:
    """
    Convert a monetary amount into Indonesian spelled-out words (terbilang).
    e.g. 1500000 -> "Satu Juta Lima Ratus Ribu Rupiah"
    """
    try:
        n = int(Decimal(str(amount)))
    except Exception:
        return "Nol Rupiah"

    if n == 0:
        return "Nol Rupiah"
    if n < 0:
        return "Minus " + terbilang(abs(n))

    def _helper(val: int) -> str:
        satuan = ["", "satu", "dua", "tiga", "empat", "lima", "enam", "tujuh", "delapan", "sembilan", "sepuluh", "sebelas"]
        if val < 12:
            return satuan[val]
        elif val < 20:
            return _helper(val - 10) + " belas"
        elif val < 100:
            rem = val % 10
            return _helper(val // 10) + " puluh" + ((" " + _helper(rem)) if rem else "")
        elif val < 200:
            rem = val - 100
            return "seratus" + ((" " + _helper(rem)) if rem else "")
        elif val < 1000:
            rem = val % 100
            return _helper(val // 100) + " ratus" + ((" " + _helper(rem)) if rem else "")
        elif val < 2000:
            rem = val - 1000
            return "seribu" + ((" " + _helper(rem)) if rem else "")
        elif val < 1000000:
            rem = val % 1000
            return _helper(val // 1000) + " ribu" + ((" " + _helper(rem)) if rem else "")
        elif val < 1000000000:
            rem = val % 1000000
            return _helper(val // 1000000) + " juta" + ((" " + _helper(rem)) if rem else "")
        elif val < 1000000000000:
            rem = val % 1000000000
            return _helper(val // 1000000000) + " miliar" + ((" " + _helper(rem)) if rem else "")
        else:
            rem = val % 1000000000000
            return _helper(val // 1000000000000) + " triliun" + ((" " + _helper(rem)) if rem else "")

    words = _helper(n).strip()
    return " ".join(part.capitalize() for part in words.split()) + " Rupiah"


def format_idr(amount: Any) -> str:
    """Format decimal amount into Indonesian Rupiah e.g. Rp 1.500.000 (CUR-026)."""
    try:
        val = Decimal(str(amount))
        int_val = int(val)
        return f"Rp {int_val:,}".replace(',', '.')
    except Exception:
        return f"Rp {amount}"


def format_wib_datetime(dt: Optional[datetime.datetime]) -> str:
    """Format datetime into Western Indonesia Time (WIB, UTC+7)."""
    if not dt:
        dt = timezone.now()
    wib_tz = datetime.timezone(datetime.timedelta(hours=7))
    if timezone.is_aware(dt):
        local_dt = dt.astimezone(wib_tz)
    else:
        local_dt = dt.replace(tzinfo=datetime.timezone.utc).astimezone(wib_tz)

    day = local_dt.day
    month_name = MONTH_NAMES_ID[local_dt.month] if 1 <= local_dt.month <= 12 else str(local_dt.month)
    year = local_dt.year
    time_str = local_dt.strftime('%H:%M')
    return f"{day} {month_name} {year}, {time_str} WIB"


def render_payment_receipt_html(payment: Payment) -> str:
    """
    Renders official Indonesian school payment receipt HTML.
    Adheres to spec/17 design tokens: 0px border radius, clear institutional typography,
    and high-contrast layout.
    """
    school = payment.school
    from apps.identity.models import Foundation
    foundation_id = payment.foundation_id or (school.foundation_id if school else None)
    foundation = Foundation.objects.filter(id=foundation_id).first() if foundation_id else None
    school_name = html.escape(school.name if school else "Sekolah")
    school_npsn = html.escape(school.npsn if school and school.npsn else "-")
    foundation_name = html.escape(foundation.legal_name if foundation else "Yayasan Pendidikan")

    student = payment.student
    student_name = html.escape(student.person.full_name if student and student.person else "-")
    student_nis = html.escape(student.nis or "-")
    student_nisn = html.escape(student.nisn or "-")

    receipt_no = html.escape(payment.receipt_number or f"RCP/{payment.id}")
    ref_no = html.escape(payment.reference or "-")
    paid_date_str = html.escape(format_wib_datetime(payment.paid_at or payment.settled_at))
    payment_method = html.escape(f"{payment.get_method_display()} ({payment.channel})")
    status_text = html.escape(payment.get_status_display() if payment.status else "LUNAS")

    formatted_total = format_idr(payment.amount)
    spelled_out = html.escape(terbilang(payment.amount))

    allocations = list(payment.allocations.select_related('invoice', 'invoice_line').all())
    rows_html = []
    if allocations:
        for idx, alloc in enumerate(allocations, start=1):
            inv = alloc.invoice
            inv_no = html.escape(inv.number if inv else "-")
            if alloc.invoice_line and alloc.invoice_line.description:
                desc_text = alloc.invoice_line.description
            elif inv and inv.period:
                desc_text = f"Tagihan SPP / Biaya Pendidikan ({inv.period})"
            else:
                desc_text = "Alokasi Pembayaran"
            inv_desc = html.escape(desc_text)
            alloc_amt = format_idr(alloc.amount)
            rows_html.append(f"""
                <tr>
                    <td style="padding: 10px; border-bottom: 1px solid #E2E8F0; text-align: center;">{idx}</td>
                    <td style="padding: 10px; border-bottom: 1px solid #E2E8F0; font-family: monospace;">{inv_no}</td>
                    <td style="padding: 10px; border-bottom: 1px solid #E2E8F0;">{inv_desc}</td>
                    <td style="padding: 10px; border-bottom: 1px solid #E2E8F0; text-align: right; font-weight: bold;">{alloc_amt}</td>
                </tr>
            """)
    else:
        # Generic allocation row if no direct invoice allocations
        rows_html.append(f"""
            <tr>
                <td style="padding: 10px; border-bottom: 1px solid #E2E8F0; text-align: center;">1</td>
                <td style="padding: 10px; border-bottom: 1px solid #E2E8F0; font-family: monospace;">{ref_no}</td>
                <td style="padding: 10px; border-bottom: 1px solid #E2E8F0;">Pembayaran Sekolah — {payment_method}</td>
                <td style="padding: 10px; border-bottom: 1px solid #E2E8F0; text-align: right; font-weight: bold;">{formatted_total}</td>
            </tr>
        """)

    officer_name = html.escape(payment.received_by.full_name if payment.received_by else "Sistem Pembayaran Otomatis EduCore")

    return f"""<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="utf-8">
<title>Kwitansi Pembayaran - {receipt_no}</title>
<style>
    @page {{
        size: A4 portrait;
        margin: 15mm;
    }}
    body {{
        font-family: 'IBM Plex Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
        color: #0F172A;
        background-color: #FFFFFF;
        margin: 0;
        padding: 0;
        font-size: 13px;
        line-height: 1.5;
    }}
    .receipt-container {{
        width: 100%;
        max-width: 800px;
        margin: 0 auto;
        border: 2px solid #0F172A;
        box-sizing: border-box;
        padding: 24px;
        border-radius: 0;
    }}
    .header-bar {{
        border-bottom: 3px solid #C8102E;
        padding-bottom: 16px;
        margin-bottom: 20px;
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
    }}
    .school-title {{
        font-size: 18px;
        font-weight: 700;
        color: #0F172A;
        text-transform: uppercase;
        margin: 0 0 4px 0;
    }}
    .school-meta {{
        font-size: 11px;
        color: #475569;
        margin: 0;
    }}
    .receipt-badge-title {{
        text-align: right;
    }}
    .receipt-title {{
        font-size: 20px;
        font-weight: 800;
        color: #C8102E;
        letter-spacing: 1px;
        margin: 0;
    }}
    .receipt-number {{
        font-family: monospace;
        font-size: 12px;
        font-weight: 600;
        color: #0F172A;
        margin-top: 4px;
    }}
    .meta-grid {{
        width: 100%;
        margin-bottom: 20px;
        border-collapse: collapse;
    }}
    .meta-grid td {{
        padding: 4px 8px;
        vertical-align: top;
    }}
    .meta-label {{
        width: 140px;
        color: #64748B;
        font-size: 12px;
        font-weight: 600;
    }}
    .meta-value {{
        font-size: 12px;
        font-weight: 600;
        color: #0F172A;
    }}
    .status-badge {{
        display: inline-block;
        background-color: #DCFCE7;
        color: #16A34A;
        font-weight: 700;
        font-size: 11px;
        padding: 2px 8px;
        border-radius: 0;
        border: 1px solid #16A34A;
    }}
    .table-allocations {{
        width: 100%;
        border-collapse: collapse;
        margin-bottom: 20px;
        border: 1px solid #CBD5E1;
    }}
    .table-allocations th {{
        background-color: #F1F5F9;
        color: #0F172A;
        font-weight: 700;
        font-size: 11px;
        padding: 8px 10px;
        border-bottom: 2px solid #CBD5E1;
        text-transform: uppercase;
    }}
    .terbilang-box {{
        background-color: #F8FAFC;
        border-left: 4px solid #C8102E;
        border-top: 1px solid #E2E8F0;
        border-right: 1px solid #E2E8F0;
        border-bottom: 1px solid #E2E8F0;
        padding: 12px 16px;
        margin-bottom: 24px;
    }}
    .terbilang-label {{
        font-size: 11px;
        font-weight: 700;
        color: #64748B;
        text-transform: uppercase;
        margin-bottom: 4px;
    }}
    .terbilang-text {{
        font-size: 13px;
        font-weight: 600;
        font-style: italic;
        color: #0F172A;
    }}
    .footer-section {{
        display: flex;
        justify-content: space-between;
        margin-top: 24px;
        padding-top: 16px;
        border-top: 1px dashed #CBD5E1;
    }}
    .disclaimer-box {{
        width: 60%;
        font-size: 10px;
        color: #64748B;
        line-height: 1.4;
    }}
    .signature-box {{
        width: 35%;
        text-align: center;
    }}
    .signature-line {{
        margin-top: 48px;
        border-top: 1px solid #0F172A;
        padding-top: 4px;
        font-weight: 700;
        font-size: 11px;
    }}
</style>
</head>
<body>

<div class="receipt-container">
    <div class="header-bar">
        <div>
            <h1 class="school-title">{school_name}</h1>
            <p class="school-meta">{foundation_name} | NPSN: {school_npsn}</p>
        </div>
        <div class="receipt-badge-title">
            <div class="receipt-title">KWITANSI</div>
            <div class="receipt-number">{receipt_no}</div>
        </div>
    </div>

    <table class="meta-grid">
        <tr>
            <td class="meta-label">Diterima Dari:</td>
            <td class="meta-value">{student_name}</td>
            <td class="meta-label">Tanggal Bayar:</td>
            <td class="meta-value">{paid_date_str}</td>
        </tr>
        <tr>
            <td class="meta-label">NIS / NISN:</td>
            <td class="meta-value">{student_nis} / {student_nisn}</td>
            <td class="meta-label">No. Referensi:</td>
            <td class="meta-value" style="font-family: monospace;">{ref_no}</td>
        </tr>
        <tr>
            <td class="meta-label">Metode Pembayaran:</td>
            <td class="meta-value">{payment_method}</td>
            <td class="meta-label">Status:</td>
            <td class="meta-value"><span class="status-badge">{status_text}</span></td>
        </tr>
    </table>

    <table class="table-allocations">
        <thead>
            <tr>
                <th style="width: 40px; text-align: center;">No</th>
                <th style="width: 150px; text-align: left;">No. Tagihan</th>
                <th style="text-align: left;">Keterangan Pembayaran</th>
                <th style="width: 160px; text-align: right;">Jumlah</th>
            </tr>
        </thead>
        <tbody>
            {"".join(rows_html)}
        </tbody>
        <tfoot>
            <tr style="background-color: #F8FAFC;">
                <td colspan="3" style="padding: 10px; text-align: right; font-weight: 800; text-transform: uppercase;">TOTAL DITERIMA:</td>
                <td style="padding: 10px; text-align: right; font-weight: 800; font-size: 14px; color: #C8102E;">{formatted_total}</td>
            </tr>
        </tfoot>
    </table>

    <div class="terbilang-box">
        <div class="terbilang-label">Terbilang:</div>
        <div class="terbilang-text"># {spelled_out} #</div>
    </div>

    <div class="footer-section">
        <div class="disclaimer-box">
            <p style="margin: 0 0 4px 0; font-weight: 600;">Bukti Pembayaran Elektronik Resmi</p>
            <p style="margin: 0;">Dokumen ini merupakan tanda bukti penerimaan pembayaran yang sah dan diterbitkan secara elektronik oleh Sistem Operasi Sekolah EduCore. Tidak diperlukan tanda tangan basah.</p>
        </div>
        <div class="signature-box">
            <div style="font-size: 11px; color: #475569;">Petugas / Verifikator:</div>
            <div class="signature-line">{officer_name}</div>
        </div>
    </div>
</div>

</body>
</html>"""


def generate_payment_receipt_pdf(payment: Payment) -> str:
    """
    Renders and persists the payment receipt document.
    Falls back to HTML if weasyprint's native libraries are unavailable.
    Stored via write_generated_file in StoredFile with deterministic object key.
    """
    if not payment.receipt_number:
        payment.receipt_number = get_next_receipt_number(payment.school)
        payment.save(update_fields=['receipt_number', 'updated_at'])

    html_content = render_payment_receipt_html(payment)

    try:
        from weasyprint import HTML
        filename = f"{payment.id}.pdf"
        data = HTML(string=html_content).write_pdf()
        content_type = 'application/pdf'
    except Exception:
        logger.warning("weasyprint unavailable, falling back to HTML payment receipt", exc_info=True)
        filename = f"{payment.id}.html"
        data = html_content.encode('utf-8')
        content_type = 'text/html'

    key = storage.build_deterministic_object_key('payment_receipt', filename)
    stored_file = write_generated_file(
        purpose='payment_receipt',
        filename=filename,
        data=data,
        key=key,
        content_type=content_type,
        foundation_id=payment.foundation_id,
        school_id=payment.school_id,
    )

    payment.receipt_pdf_key = stored_file.key
    payment.save(update_fields=['receipt_pdf_key', 'updated_at'])

    audit(
        action='finance.payment.receipt_rendered',
        entity_type='Payment',
        entity_id=payment.id,
        foundation_id=payment.foundation_id,
        school_id=payment.school_id,
        diff={
            'receipt_number': payment.receipt_number,
            'receipt_pdf_key': stored_file.key,
            'content_type': content_type,
        },
    )

    return stored_file.key


def get_or_create_payment_receipt(payment: Payment) -> Dict[str, Any]:
    """
    Resolves the receipt document for a payment, rendering on demand if missing.
    Returns dictionary with download_url, receipt_number, and metadata.
    """
    if not payment.receipt_pdf_key:
        generate_payment_receipt_pdf(payment)

    download_info = build_signed_download(payment.receipt_pdf_key, expires_seconds=3600)

    return {
        'payment_id': payment.id,
        'reference': payment.reference,
        'receipt_number': payment.receipt_number,
        'receipt_pdf_key': payment.receipt_pdf_key,
        'download_url': download_info['download_url'],
        'expires_at': download_info['expires_at'].isoformat(),
        'amount': str(payment.amount),
        'currency': payment.currency,
        'paid_at': payment.paid_at.isoformat() if payment.paid_at else None,
        'status': payment.status,
    }
