"""Bank statement file parsers for SWIFT MT940 and ISO 20022 CAMT.053
(spec/14 CMP-026, spec/06 FIN-024) — used when a bank settles a direct VA
via a downloadable statement file rather than a gateway's JSON API (the
XenditPaymentProvider.fetch_settlement path), e.g. BCA/Mandiri/BRI direct
VA per CMP-024's "MAY be added later" note.

Both parsers normalize to the exact same settlement-record shape
PaymentProvider.fetch_settlement returns, so a parsed file feeds straight
into the existing GatewaySettlementBatch/PaymentDiscrepancy reconciliation
machinery (apps.finance.services.reconciliation) built for TASK-028.

Only credit entries are returned — money coming IN, matched against
Payment.external_id. Debit entries (bank fees, reversed transactions
leaving the account) aren't settlement records to reconcile against a
payment and are skipped.

Known simplifications (out of scope for this slice, same spirit as
XenditPaymentProvider's CMP-024 comment about Midtrans's missing API):
- MT940 multi-page statement continuation (:61:/:86: pairs spanning
  multiple SWIFT messages) is not stitched together; each file is parsed
  independently.
- MT942 (intraday) is not supported, only MT940 (end-of-day).
- CAMT.053 batched entries (NtryDtls with multiple TxDtls per Ntry) use
  only the first TxDtls for reference/remittance extraction.
"""
import re
import xml.etree.ElementTree as ET
from datetime import datetime, date
from decimal import Decimal, InvalidOperation

from django.utils import timezone


class BankStatementParseError(ValueError):
    """Raised when a statement file is malformed or in an unrecognized format."""
    pass


# SWIFT MT940 :61: statement line: 6!n[4!n]2a[1!a]15d1!a3!c16x[//16x]
# ValueDate(6) [EntryDate(4)] DCMark(C/D/RC/RD) [FundsCode] Amount(comma-decimal)
# TransactionTypeCode(e.g. NMSC, N654) CustomerReference [//BankReference]
_MT940_LINE61_RE = re.compile(
    r'^(?P<value_date>\d{6})'
    r'(?:\d{4})?'                       # optional entry date, unused
    r'(?P<dc_mark>R?[CD])'
    r'(?:[A-Z])?'                       # optional funds code
    r'(?P<amount>\d+,\d{0,2})'
    r'(?:[A-Z]{4}|N[A-Z0-9]{3})?'       # transaction type code
    r'(?P<reference>[^/\r\n]*)'
    r'(?://(?P<bank_ref>\S*))?'
)


def _parse_mt940_amount(raw: str) -> Decimal:
    try:
        return Decimal(raw.replace(',', '.'))
    except InvalidOperation as exc:
        raise BankStatementParseError(f"Invalid MT940 amount '{raw}': {exc}") from exc


def _parse_mt940_date(yymmdd: str) -> date:
    try:
        return datetime.strptime(yymmdd, '%y%m%d').date()
    except ValueError as exc:
        raise BankStatementParseError(f"Invalid MT940 value date '{yymmdd}': {exc}") from exc


def parse_mt940(content: str, bank_code: str = '') -> list[dict]:
    """Parse a SWIFT MT940 statement into normalized settlement records.

    Each :61: statement line is one entry; an immediately following :86:
    line supplies narrative/remittance info. Only credit ('C'/'RC') entries
    are returned.
    """
    if not content or ':61:' not in content:
        raise BankStatementParseError("Not a recognizable MT940 file: no :61: statement lines found.")

    lines = content.splitlines()
    records = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith(':61:'):
            match = _MT940_LINE61_RE.match(line[4:])
            if not match:
                raise BankStatementParseError(f"Malformed MT940 :61: line: {line!r}")

            dc_mark = match.group('dc_mark')
            is_credit = dc_mark in ('C', 'RC')

            narrative = ''
            if i + 1 < len(lines) and lines[i + 1].startswith(':86:'):
                narrative = lines[i + 1][4:].strip()
                i += 1

            if is_credit:
                reference = (match.group('reference') or '').strip()
                bank_ref = (match.group('bank_ref') or '').strip()
                external_id = reference or bank_ref or (narrative.split()[0] if narrative else '')
                if not external_id:
                    raise BankStatementParseError(f"MT940 credit entry has no usable reference: {line!r}")

                amount = _parse_mt940_amount(match.group('amount'))
                value_date = _parse_mt940_date(match.group('value_date'))
                settled_at = timezone.make_aware(datetime.combine(value_date, datetime.min.time()))

                records.append({
                    'external_id': external_id,
                    'amount': amount,
                    'fee': Decimal('0.00'),
                    'net': amount,
                    'settled_at': settled_at,
                    'channel': f'BANK_TRANSFER_{bank_code.upper()}' if bank_code else 'BANK_TRANSFER',
                    'bank': bank_code.upper() or None,
                    'raw': {'mt940_line61': line, 'mt940_line86': narrative},
                })
        i += 1

    return records


def _local_tag(elem) -> str:
    """Strip the XML namespace off an ElementTree tag, e.g.
    '{urn:iso:...:camt.053.001.02}Ntry' -> 'Ntry'."""
    return elem.tag.rsplit('}', 1)[-1]


def _find_local(elem, *path: str):
    """Namespace-agnostic descendant lookup by a path of local tag names."""
    current = [elem]
    for tag in path:
        next_level = []
        for node in current:
            next_level.extend(child for child in node if _local_tag(child) == tag)
        current = next_level
        if not current:
            return None
    return current[0] if current else None


def parse_camt053(xml_content, bank_code: str = '') -> list[dict]:
    """Parse an ISO 20022 CAMT.053 statement into normalized settlement records.

    Namespace-agnostic (strips whatever camt.053 schema version the bank
    uses). Only credit ('CRDT') entries are returned.
    """
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as exc:
        raise BankStatementParseError(f"Invalid CAMT.053 XML: {exc}") from exc

    stmt = _find_local(root, 'BkToCstmrStmt', 'Stmt')
    if stmt is None:
        raise BankStatementParseError("Not a recognizable CAMT.053 file: no BkToCstmrStmt/Stmt element found.")

    records = []
    for entry in stmt:
        if _local_tag(entry) != 'Ntry':
            continue

        cdt_dbt = _find_local(entry, 'CdtDbtInd')
        if cdt_dbt is None or cdt_dbt.text != 'CRDT':
            continue

        amt_elem = _find_local(entry, 'Amt')
        if amt_elem is None or not amt_elem.text:
            raise BankStatementParseError("CAMT.053 Ntry missing Amt element.")
        try:
            amount = Decimal(amt_elem.text)
        except InvalidOperation as exc:
            raise BankStatementParseError(f"Invalid CAMT.053 amount '{amt_elem.text}': {exc}") from exc

        booking_date_elem = _find_local(entry, 'BookgDt', 'Dt')
        settled_at = timezone.now()
        if booking_date_elem is not None and booking_date_elem.text:
            try:
                settled_at = timezone.make_aware(datetime.strptime(booking_date_elem.text, '%Y-%m-%d'))
            except ValueError as exc:
                raise BankStatementParseError(f"Invalid CAMT.053 booking date '{booking_date_elem.text}': {exc}") from exc

        tx_dtls = _find_local(entry, 'NtryDtls', 'TxDtls')
        end_to_end_id = _find_local(tx_dtls, 'Refs', 'EndToEndId') if tx_dtls is not None else None
        acct_svcr_ref = _find_local(entry, 'AcctSvcrRef')
        ntry_ref = _find_local(entry, 'NtryRef')

        external_id = None
        for candidate in (end_to_end_id, acct_svcr_ref, ntry_ref):
            if candidate is not None and candidate.text:
                external_id = candidate.text.strip()
                break
        if not external_id:
            raise BankStatementParseError("CAMT.053 Ntry has no usable reference (EndToEndId/AcctSvcrRef/NtryRef).")

        ustrd = _find_local(tx_dtls, 'RmtInf', 'Ustrd') if tx_dtls is not None else None
        narrative = ustrd.text.strip() if ustrd is not None and ustrd.text else ''

        records.append({
            'external_id': external_id,
            'amount': amount,
            'fee': Decimal('0.00'),
            'net': amount,
            'settled_at': settled_at,
            'channel': f'BANK_TRANSFER_{bank_code.upper()}' if bank_code else 'BANK_TRANSFER',
            'bank': bank_code.upper() or None,
            'raw': {'camt053_narrative': narrative},
        })

    return records


def parse_bank_statement(content, file_format: str, bank_code: str = '') -> list[dict]:
    """Dispatch to the correct parser by file_format ('MT940' or 'CAMT053')."""
    fmt = (file_format or '').upper()
    if fmt == 'MT940':
        return parse_mt940(content, bank_code=bank_code)
    if fmt == 'CAMT053':
        return parse_camt053(content, bank_code=bank_code)
    raise BankStatementParseError(f"Unsupported bank statement format: '{file_format}'. Use 'MT940' or 'CAMT053'.")
