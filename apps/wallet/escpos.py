"""ESC/POS thermal receipt encoding — pure Python, zero new dependencies.

The physical USB/Bluetooth printers are attached to the Android POS terminals
(spec/12 §2 POS_TERMINAL/KIOSK rows), which the server cannot reach directly.
The backend's job is therefore to RENDER the byte stream: the checkout response
carries it base64-encoded (WAL-018's <=3s budget — the terminal prints
immediately, no second round trip) and a reprint endpoint returns the raw
bytes. The terminal app is responsible for writing the bytes to its local
printer port.

Supported commands (subset of the ESC/POS de-facto standard, implemented by
every common thermal printer):
- ESC @        initialize
- ESC a n      justification (0 left, 1 center, 2 right)
- ESC ! n      print mode (bit 4: double height, bit 5: double width, bit 3: emphasized)
- GS ! n       character size (same bits, single command)
- GS k m ...   Code-128 barcode of the transaction id
- GS V m       full/partial cut
- LF / ESC d n line feeds

Text is encoded CP437 (the default codepage on receipt printers). Indonesian
text is Latin-based and fits; unencodable characters are transliterated
rather than crashing the checkout (a receipt must never block a sale).
"""
from decimal import Decimal

ESC = b'\x1b'
GS = b'\x1d'

# Printable character columns per paper width. 58mm printers are 32 columns,
# 80mm printers are 48 columns at the default font B size.
WIDTH_58MM = 32
WIDTH_80MM = 48


class EscposBuilder:
    """Accumulates ESC/POS bytes; text helpers lay out within a fixed width."""

    def __init__(self, width: int = WIDTH_58MM):
        if width not in (WIDTH_58MM, WIDTH_80MM):
            raise ValueError(f"Unsupported receipt width: {width}")
        self.width = width
        self._buf = bytearray()

    # -- raw commands -------------------------------------------------
    def raw(self, data: bytes) -> 'EscposBuilder':
        self._buf += data
        return self

    def init(self) -> 'EscposBuilder':
        return self.raw(ESC + b'@')

    def feed(self, lines: int = 1) -> 'EscposBuilder':
        if lines == 1:
            return self.raw(b'\n')
        return self.raw(ESC + b'd' + bytes([min(lines, 255)]))

    def cut(self, partial: bool = True) -> 'EscposBuilder':
        # GS V m : 66 = partial cut (feed n in variant 67/101 not used; the
        # plain 66 form is the most widely compatible).
        return self.raw(GS + b'V' + bytes([66 if partial else 65]))

    def justify(self, mode: int) -> 'EscposBuilder':
        """0=left, 1=center, 2=right."""
        if mode not in (0, 1, 2):
            raise ValueError(f"Invalid justification: {mode}")
        return self.raw(ESC + b'a' + bytes([mode]))

    def emphasis(self, on: bool) -> 'EscposBuilder':
        return self.raw(ESC + b'!' + bytes([0x08 if on else 0x00]))

    def double_size(self, on: bool) -> 'EscposBuilder':
        """Double height+width via GS ! (0x11) or reset to normal (0x00)."""
        return self.raw(GS + b'!' + bytes([0x11 if on else 0x00]))

    def barcode_code128(self, data: str) -> 'EscposBuilder':
        """GS k m=73 (CODE128) with explicit length payload — the portable form."""
        payload = b'{B' + data.encode('ascii', 'replace')
        return self.raw(
            GS + b'k' + bytes([73, len(payload)]) + payload + b'\x00'
        )

    # -- text layout --------------------------------------------------
    def _encode(self, text: str) -> bytes:
        # CP437 with a safe fallback: transliterate rather than crash a sale.
        try:
            return text.encode('cp437')
        except UnicodeEncodeError:
            return text.encode('cp437', 'replace')

    def _fit(self, text: str) -> str:
        """Clip one logical line to the printable width (CP437 bytes count)."""
        encoded = self._encode(text)
        if len(encoded) <= self.width:
            return text
        # Trim by encoded length, then decode back permissively.
        return encoded[:self.width].decode('cp437', 'replace')

    def line(self, text: str = '') -> 'EscposBuilder':
        return self.raw(self._fit(text).encode('cp437', 'replace') + b'\n')

    def centered(self, text: str) -> 'EscposBuilder':
        self.justify(1)
        self.line(text)
        return self.justify(0)

    def two_column(self, left: str, right: str, left_bold: bool = False) -> 'EscposBuilder':
        """Left-aligned text with a right-aligned value on the same row."""
        left_bytes = self._encode(left)
        right_bytes = self._encode(right)
        if len(left_bytes) + len(right_bytes) <= self.width:
            gap = self.width - len(left_bytes) - len(right_bytes)
            out = left_bytes + b' ' * gap + right_bytes
        else:
            # Too long: left line wraps, value goes on its own right-aligned row.
            out = self._fit(left).encode('cp437', 'replace') + b'\n' + \
                b' ' * max(0, self.width - len(right_bytes)) + right_bytes
        return self.raw(out + b'\n')

    def rule(self, char: str = '-') -> 'EscposBuilder':
        return self.line(char * self.width)

    def bytes(self) -> bytes:
        return bytes(self._buf)


def format_idr(amount) -> str:
    """Indonesian grouping, no decimals shown (CUR-026 rendering rule):
    1500000.00 -> Rp 1.500.000."""
    value = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    whole = int(value)
    s = f"{whole:,}".replace(',', '.')
    return f"Rp {s}"


def render_pos_receipt(pos_tx, width: int = WIDTH_58MM) -> bytes:
    """Render a complete ESC/POS receipt for a POS transaction.

    Header: school/merchant + terminal name; body: item lines from the
    items JSONField; footer: transaction id barcode. REJECTED and VOIDED
    transactions render their own clearly-marked slip (WAL-013: rejected
    sales are still logged — the customer gets proof of the refusal).
    Deliberately excludes guardian PII (red line 5): only the student's NIS
    and name, which already appear on the screen slip.
    """
    from django.utils import timezone

    b = EscposBuilder(width)
    b.init()

    student = pos_tx.student
    person = getattr(student, 'person', None)
    student_name = (person.full_name if person else '') or ''
    school = getattr(pos_tx.merchant, 'school', None)

    b.centered(school.name if school else 'Kantin Sekolah')
    b.centered(pos_tx.merchant.name)
    if pos_tx.terminal.name:
        b.centered(pos_tx.terminal.name)

    occurred = timezone.localtime(pos_tx.occurred_at)
    b.feed()
    b.line(f"Waktu: {occurred:%d/%m/%Y %H:%M}")
    b.line(f"Siswa: {student_name} ({student.nis})")
    b.rule('=')

    if pos_tx.status == 'REJECTED':
        b.centered('** TRANSAKSI DITOLAK **')
        b.feed()
        b.line('Pembayaran tidak dapat diproses.')
        b.line('Saldo tidak berkurang.')
        b.rule('=')
        b.feed(2)
        b.barcode_code128(str(pos_tx.client_transaction_id)[:40])
        b.feed(3)
        b.cut()
        return b.bytes()

    total = Decimal('0')
    for item in pos_tx.items:
        name = str(item.get('name', ''))
        qty = _dec(item.get('qty', 1))
        unit_price = _dec(item.get('unit_price', 0))
        line_total = qty * unit_price
        total += line_total
        b.two_column(f"{_fmt_qty(qty)} x {name}", format_idr(line_total))

    b.rule('-')
    b.two_column('TOTAL', format_idr(pos_tx.total))
    if pos_tx.status == 'VOIDED':
        b.rule('-')
        b.centered('** DIBATALKAN **')
        if pos_tx.voided_at:
            v = timezone.localtime(pos_tx.voided_at)
            b.line(f"Dibatalkan: {v:%d/%m/%Y %H:%M}")
        if pos_tx.void_reason:
            b.line(f"Alasan: {pos_tx.void_reason[:width]}")
    b.rule('=')
    b.centered('Terima kasih')
    b.feed()
    b.barcode_code128(str(pos_tx.client_transaction_id)[:40])
    b.feed(3)
    b.cut()
    return b.bytes()


def _dec(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _fmt_qty(qty: Decimal) -> str:
    return str(int(qty)) if qty == qty.to_integral_value() else f"{qty:.2f}".rstrip('0')
