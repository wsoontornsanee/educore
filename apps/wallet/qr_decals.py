"""Printed static QR decals (spec 18 §3b, QRS-030..036, QRS-042).

A decal is a QR bound to one named payment point of a merchant. It has no screen, no
countdown and no live channel, so the compensating controls live here: its own signed
token, a lower cap (see ``qr_charge``), instant revoke, rotation with a grace window,
and an audit-logged, operator-only PDF.
"""
import re
import secrets
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Optional, Tuple

from django.core import signing
from django.db import transaction
from django.utils import timezone, translation
from django.utils.html import escape
from django.utils.translation import gettext as _

from apps.core.services import audit
from apps.wallet.models import (
    POSPaymentPoint,
    POSPaymentPointStatus,
    POSQRDecal,
    POSQRDecalStatus,
)
from apps.wallet.qr_charge import DECAL_TOKEN_SALT, effective_cap, refusal_message, render_qr_svg

DECAL_GRACE = timedelta(hours=24)  # QRS-036 default rotation grace


class DecalError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(code)
        self.code = code
        self.message = message


def _require_static_enabled(merchant) -> None:
    if not (merchant.is_active and merchant.qr_self_amount_enabled and merchant.static_qr_enabled):
        raise DecalError('STATIC_QR_DISABLED', _("QR statis belum diaktifkan untuk merchant ini."))


def set_merchant_static_qr(merchant, enabled: bool, acknowledged: bool, actor) -> None:
    """QRS-008: static mode is its own switch, off by default, acknowledged like QR Charge itself."""
    if enabled and not acknowledged:
        raise DecalError(
            'ACKNOWLEDGEMENT_REQUIRED',
            _("Konfirmasi bahwa lembar QR statis tidak memiliki layar dan batas per transaksinya lebih rendah."),
        )
    merchant.static_qr_enabled = enabled
    if enabled:
        merchant.static_qr_ack_by = actor
        merchant.static_qr_ack_at = timezone.now()
    merchant.save(update_fields=['static_qr_enabled', 'static_qr_ack_by', 'static_qr_ack_at', 'updated_at'])
    audit(
        action='wallet.merchant.static_qr_set', entity_type='Merchant', entity_id=merchant.id,
        foundation_id=merchant.foundation_id, diff={'enabled': enabled},
    )


def create_payment_point(merchant, name: str, location: str, actor) -> POSPaymentPoint:
    """QRS-030: an operator names a counter under a merchant, with static QR already on."""
    _require_static_enabled(merchant)
    point = POSPaymentPoint.objects.create(
        foundation_id=merchant.foundation_id, merchant=merchant, name=name.strip(), location=location.strip(),
    )
    audit(
        action='wallet.payment_point.created', entity_type='POSPaymentPoint', entity_id=point.id,
        foundation_id=merchant.foundation_id, diff={'name': point.name},
    )
    return point


def close_payment_point(point: POSPaymentPoint, actor) -> POSPaymentPoint:
    """A closed counter refuses charges with PAYMENT_POINT_CLOSED without touching its decals."""
    point.status = POSPaymentPointStatus.CLOSED
    point.save(update_fields=['status', 'updated_at'])
    audit(
        action='wallet.payment_point.closed', entity_type='POSPaymentPoint', entity_id=point.id,
        foundation_id=point.foundation_id, diff={'by': getattr(actor, 'id', None)},
    )
    return point


def _merchant_prefix(merchant) -> str:
    """'Kantin Utama' -> 'KU'. Letters only, two characters, so a sheet reads across a yard."""
    initials = ''.join(w[0] for w in re.findall(r'[A-Za-z]+', merchant.name))[:2].upper()
    return initials or 'KP'


def _next_human_id(merchant) -> str:
    prefix = _merchant_prefix(merchant)
    used = POSQRDecal.objects.filter(
        foundation_id=merchant.foundation_id, payment_point__merchant=merchant,
    ).values_list('human_id', flat=True)
    highest = max((int(h.rsplit('-', 1)[1]) for h in used if h.rsplit('-', 1)[-1].isdigit()), default=0)
    return f"{prefix}-{highest + 1:02d}"


@transaction.atomic
def print_decal(point: POSPaymentPoint, actor, expires_on: Optional[date] = None) -> POSQRDecal:
    """QRS-030/036: mint a new sheet for the counter. Any earlier ACTIVE sheet is SUPERSEDED with a
    24 h grace window, so a half-replaced stall keeps taking payment."""
    if point.status != POSPaymentPointStatus.ACTIVE:
        raise DecalError('PAYMENT_POINT_CLOSED', _("Titik pembayaran ini sudah ditutup."))
    merchant = point.merchant
    _require_static_enabled(merchant)
    if expires_on is not None and expires_on <= timezone.localdate():
        raise DecalError('DECAL_EXPIRY_INVALID', _("Tanggal kedaluwarsa harus di masa depan."))

    now = timezone.now()
    for old in POSQRDecal.objects.select_for_update().filter(
        foundation_id=point.foundation_id, payment_point=point, status=POSQRDecalStatus.ACTIVE,
    ):
        old.status = POSQRDecalStatus.SUPERSEDED
        old.superseded_at = now
        old.grace_until = now + DECAL_GRACE
        old.save(update_fields=['status', 'superseded_at', 'grace_until', 'updated_at'])

    decal = POSQRDecal.objects.create(
        foundation_id=point.foundation_id, payment_point=point, human_id=_next_human_id(merchant),
        nonce=secrets.token_hex(8), printed_by=actor, printed_at=now, expires_on=expires_on,
    )
    audit(
        action='wallet.decal.printed', entity_type='POSQRDecal', entity_id=decal.id,
        foundation_id=decal.foundation_id, diff={'human_id': decal.human_id, 'payment_point': point.id},
    )
    return decal


def revoke_decal(decal: POSQRDecal, actor, reason: str = '') -> POSQRDecal:
    """QRS-035: instant, and scoped to this one sheet."""
    decal.status = POSQRDecalStatus.REVOKED
    decal.revoke_reason = reason[:255]
    decal.save(update_fields=['status', 'revoke_reason', 'updated_at'])
    audit(
        action='wallet.decal.revoked', entity_type='POSQRDecal', entity_id=decal.id,
        foundation_id=decal.foundation_id, diff={'human_id': decal.human_id, 'reason': reason[:255]},
    )
    return decal


def decal_token(decal: POSQRDecal) -> str:
    """QRS-034: signed, non-guessable, and no amount, student, balance or URL inside."""
    return signing.dumps({'d': decal.id, 'n': decal.nonce}, salt=DECAL_TOKEN_SALT, compress=False)


# --- printable sheet ---------------------------------------------------------------------------

_ONES = ['', 'satu', 'dua', 'tiga', 'empat', 'lima', 'enam', 'tujuh', 'delapan', 'sembilan', 'sepuluh', 'sebelas']


def _words(n: int) -> str:
    """Words for n >= 0, empty for 0 so compounds ('dua puluh' + 0) stay clean."""
    if n == 0:
        return ''
    if n < 12:
        return _ONES[n]
    if n < 20:
        return _words(n - 10) + ' belas'
    if n < 100:
        return f"{_words(n // 10)} puluh {_words(n % 10)}".strip()
    if n < 200:
        return f"seratus {_words(n - 100)}".strip()
    if n < 1000:
        return f"{_words(n // 100)} ratus {_words(n % 100)}".strip()
    if n < 2000:
        return f"seribu {_words(n - 1000)}".strip()
    if n < 1_000_000:
        return f"{_words(n // 1000)} ribu {_words(n % 1000)}".strip()
    if n < 1_000_000_000:
        return f"{_words(n // 1_000_000)} juta {_words(n % 1_000_000)}".strip()
    return f"{_words(n // 1_000_000_000)} miliar {_words(n % 1_000_000_000)}".strip()


def terbilang(n: int) -> str:
    """Indonesian number in words (25000 -> 'dua puluh lima ribu'), for the cap on the sheet (QRS-032)."""
    if n < 0:
        return 'minus ' + terbilang(-n)
    return _words(n) or 'nol'


def _money_id(amount: Decimal, currency: str) -> str:
    return f"Rp {int(amount):,}".replace(',', '.') if currency == 'IDR' else f"{currency} {amount:,.2f}"


def render_decal_html(decal: POSQRDecal) -> str:
    """One A4 sheet, black on white, always in Indonesian (the school's language; QRS-032/033)."""
    point = decal.payment_point
    merchant = point.merchant
    school = merchant.school
    cap = effective_cap(merchant, static=True)
    with translation.override('id'):
        steps = [
            _("Scan dengan aplikasi Astra EduCore"),
            _("Masukkan jumlah yang disebut petugas"),
            _("Tunjukkan kode 4 huruf ke petugas"),
        ]
        title = _("Bayar pakai Dompet Siswa")
        cap_line = _("Maksimum %(cap)s per transaksi") % {'cap': _money_id(cap, school.base_currency)}
        printed = _("DICETAK")
    cap_words = f"({terbilang(int(cap))} rupiah)" if school.base_currency == 'IDR' else ''
    items = ''.join(f"<li>{escape(s)}</li>" for s in steps)
    return f"""<!DOCTYPE html><html lang="id"><head><meta charset="utf-8"><title>{escape(decal.human_id)}</title>
<style>
@page {{ size: A4; margin: 0 }}
* {{ box-sizing: border-box; color: #000; background: #fff; font-family: Arial, Helvetica, sans-serif }}
body {{ margin: 0 }}
.sheet {{ width: 210mm; height: 297mm; padding: 14mm; }}
.cut {{ border: 0.4mm dashed #000; height: 100%; padding: 12mm 14mm; text-align: center;
        display: flex; flex-direction: column; align-items: center; justify-content: space-between }}
.school {{ font-size: 15pt; letter-spacing: .12em; text-transform: uppercase; font-weight: 700 }}
h1 {{ font-size: 34pt; margin: 4mm 0 0 }}
h2 {{ font-size: 26pt; margin: 2mm 0 0 }}
.where {{ font-size: 15pt }}
.qr {{ width: 90mm; height: 90mm }} .qr svg {{ width: 100%; height: 100% }}
ol {{ font-size: 17pt; text-align: left; line-height: 1.5; margin: 0; padding-left: 8mm }}
.cap {{ font-size: 20pt; font-weight: 700 }} .words {{ font-size: 14pt }}
.id {{ font-size: 12pt; font-family: 'Courier New', monospace; letter-spacing: .08em }}
</style></head><body><div class="sheet"><div class="cut">
<div><div class="school">{escape(school.name)}</div><h1>{escape(title)}</h1>
<h2>{escape(point.name)}</h2>
<div class="where">{escape(merchant.name)}{(' · ' + escape(point.location)) if point.location else ''}</div></div>
<div class="qr">{render_qr_svg(decal_token(decal))}</div>
<ol>{items}</ol>
<div><div class="cap">{escape(cap_line)}</div><div class="words">{escape(cap_words)}</div></div>
<div class="id">{escape(decal.human_id)} · {escape(printed)} {decal.printed_at.astimezone(timezone.get_current_timezone()).strftime('%d %b %Y').upper()}</div>
</div></div></body></html>"""


def render_decal_pdf(decal: POSQRDecal, actor) -> Tuple[bytes, str]:
    """QRS-042: only the authenticated operator downloads it, and every download is audit-logged.

    Falls back to the printable HTML if weasyprint's native libraries are missing, as the settlement
    statement does."""
    if decal.status != POSQRDecalStatus.ACTIVE:
        raise DecalError('DECAL_NOT_ACTIVE', _("Hanya lembar aktif yang dapat diunduh."))
    html = render_decal_html(decal)
    try:
        from weasyprint import HTML
        data, content_type = HTML(string=html).write_pdf(), 'application/pdf'
    except Exception:
        data, content_type = html.encode('utf-8'), 'text/html; charset=utf-8'
    audit(
        action='wallet.decal.downloaded', entity_type='POSQRDecal', entity_id=decal.id,
        foundation_id=decal.foundation_id, actor_id=str(getattr(actor, 'id', '')),
        diff={'human_id': decal.human_id, 'payment_point': decal.payment_point_id, 'format': content_type},
    )
    return data, content_type


# --- operator Counter feed (QRS-038) -------------------------------------------------------------

COUNTER_FEED_LIMIT = 50


def get_counter_feed(point: POSPaymentPoint, now=None) -> dict:
    """Today's charges at one counter, newest first: paid ones and refusals (spec 18 §3b, QRS-038).

    "Today" is the school's own local day. Totals are summed here in Decimal (clients never sum money)
    and count only COMPLETED sales.
    """
    from django.db.models import Sum

    from apps.attendance.services import get_school_timezone
    from apps.wallet.models import POSEntryMode, POSTransaction, POSTransactionStatus

    school = point.merchant.school
    local_now = (now or timezone.now()).astimezone(get_school_timezone(school))
    day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    base = POSTransaction.objects.filter(
        foundation_id=point.foundation_id, qr_decal__payment_point=point, entry_mode=POSEntryMode.SELF_ENTERED,
        occurred_at__gte=day_start, occurred_at__lt=day_start + timedelta(days=1), deleted_at__isnull=True,
    )
    completed = base.filter(status=POSTransactionStatus.COMPLETED)
    rows = base.select_related('student', 'student__person').order_by('-occurred_at', '-id')[:COUNTER_FEED_LIMIT]
    return {
        'payment_point': point,
        'currency': school.base_currency,
        'count_today': completed.count(),
        'total_today': (completed.aggregate(t=Sum('total'))['t'] or Decimal('0.00')).quantize(Decimal('0.01')),
        'items': [
            {
                'id': tx.id, 'status': tx.status, 'reject_reason': tx.reject_reason,
                'reject_label': refusal_message(tx.reject_reason) if tx.reject_reason else '',
                'student_name': tx.student.person.full_name, 'student_nis': tx.student.nis,
                'amount': tx.total, 'confirmation_code': tx.confirmation_code, 'occurred_at': tx.occurred_at,
            }
            for tx in rows
        ],
    }


# --- console reporting (QRS-003, QRS-040) --------------------------------------------------------

def local_day_bounds(school, day: Optional[date] = None):
    """(day, start, end) of the school's local day; ``day`` defaults to today there."""
    from apps.attendance.services import get_school_timezone

    tz = get_school_timezone(school)
    day = day or timezone.now().astimezone(tz).date()
    start = datetime.combine(day, time.min, tzinfo=tz)
    return day, start, start + timedelta(days=1)


def get_sales_by_payment_point(transactions) -> list:
    """QRS-040: COMPLETED sales grouped by printed-decal counter. Anything not sold through a decal (card-tap and
    terminal-QR sales) is one row whose payment point is None. Totals are summed here in Decimal."""
    rows = {}
    for tx in transactions.select_related('qr_decal__payment_point'):
        point = tx.qr_decal.payment_point if tx.qr_decal_id else None
        row = rows.setdefault(point.id if point else None, {
            'payment_point_id': point.id if point else None, 'payment_point_name': point.name if point else None,
            'count': 0, 'total': Decimal('0.00'),
        })
        row['count'] += 1
        row['total'] += tx.total
    return sorted(rows.values(), key=lambda r: (r['payment_point_name'] is None, r['payment_point_name'] or ''))
