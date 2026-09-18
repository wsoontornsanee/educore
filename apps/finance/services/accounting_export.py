"""General ledger export service for external accounting software (spec/14 §6).

Supports CPSSoft Accurate Online and Mekari Jurnal.id standard journal import formats.
Enforces pre-export double-entry integrity verification (sum(debit) == sum(credit)).
Generates SHA-256 export batch hash and audit logging.
"""
import csv
import hashlib
import io
from datetime import date, datetime
from decimal import Decimal
from typing import Optional, Union

from django.utils.translation import gettext_lazy as _

from apps.core.services import audit
from apps.finance.models import (
    ExternalAccountMapping,
    ExternalLedgerSystem,
    LedgerJournal,
)


class UnbalancedJournalExportError(ValueError):
    """Raised when one or more journals in the export batch are unbalanced."""
    def __init__(self, message: str, journal_number: Optional[str] = None):
        super().__init__(message)
        self.journal_number = journal_number


class AccountingExportService:
    """Service providing general ledger journal exports for external accounting software."""

    @staticmethod
    def get_account_mappings(foundation_id: int, system: str) -> dict[str, tuple[str, str]]:
        """
        Fetch active account mappings for the given foundation and external ledger system.
        Returns a dict mapping: {internal_code: (external_code, external_name)}.
        """
        mappings = ExternalAccountMapping.objects.filter(
            foundation_id=foundation_id,
            system=system,
            deleted_at__isnull=True,
        )
        return {
            m.internal_code: (m.external_code, m.external_name)
            for m in mappings
        }

    @classmethod
    def get_and_validate_journals(
        cls,
        foundation_id: int,
        start_date: Optional[Union[date, str]] = None,
        end_date: Optional[Union[date, str]] = None,
        school_id: Optional[int] = None,
        ref_type: Optional[str] = None,
    ) -> list[LedgerJournal]:
        """
        Query journals for the foundation matching the specified filters,
        and strictly assert that every journal has sum(debit) == sum(credit).
        Raises UnbalancedJournalExportError if any journal is unbalanced.
        """
        qs = LedgerJournal.objects.filter(
            foundation_id=foundation_id,
            deleted_at__isnull=True,
        ).prefetch_related('entries').order_by('occurred_at', 'number')

        if start_date:
            if isinstance(start_date, str):
                start_date = cls.parse_date(start_date)
            qs = qs.filter(occurred_at__date__gte=start_date)

        if end_date:
            if isinstance(end_date, str):
                end_date = cls.parse_date(end_date)
            qs = qs.filter(occurred_at__date__lte=end_date)

        if school_id:
            qs = qs.filter(school_id=school_id)

        if ref_type:
            qs = qs.filter(ref_type=ref_type)

        journals = list(qs)

        for journal in journals:
            entries = list(journal.entries.all())
            total_debit = Decimal('0.00')
            total_credit = Decimal('0.00')
            for entry in entries:
                total_debit += entry.debit
                total_credit += entry.credit

            if total_debit != total_credit:
                msg = _(
                    "Jurnal %(number)s tidak seimbang: Total Debit (%(debit)s) != Total Kredit (%(credit)s)"
                ) % {
                    'number': journal.number,
                    'debit': f"{total_debit:.2f}",
                    'credit': f"{total_credit:.2f}",
                }
                raise UnbalancedJournalExportError(str(msg), journal_number=journal.number)

        return journals

    @classmethod
    def export_accurate(
        cls,
        foundation_id: int,
        start_date: Optional[Union[date, str]] = None,
        end_date: Optional[Union[date, str]] = None,
        school_id: Optional[int] = None,
        ref_type: Optional[str] = None,
        actor_id: Optional[str] = None,
        role: str = '',
        ip_address: Optional[str] = None,
    ) -> tuple[str, str, int]:
        """
        Export general ledger journals in CPSSoft Accurate Online format.
        Header: Tanggal Transaksi,No Bukti,Nomor Akun,Nama Akun,Debit,Kredit,Keterangan
        Returns (csv_string, sha256_hash, journal_count).
        """
        journals = cls.get_and_validate_journals(
            foundation_id=foundation_id,
            start_date=start_date,
            end_date=end_date,
            school_id=school_id,
            ref_type=ref_type,
        )
        mappings = cls.get_account_mappings(foundation_id, ExternalLedgerSystem.ACCURATE)

        output = io.StringIO()
        writer = csv.writer(output, lineterminator='\r\n')
        writer.writerow([
            "Tanggal Transaksi",
            "No Bukti",
            "Nomor Akun",
            "Nama Akun",
            "Debit",
            "Kredit",
            "Keterangan",
        ])

        total_entries = 0
        for journal in journals:
            date_str = journal.occurred_at.strftime('%d/%m/%Y')
            bukti_str = journal.number
            for entry in journal.entries.all():
                total_entries += 1
                if entry.account_code in mappings:
                    ext_code, ext_name = mappings[entry.account_code]
                    account_code_val = ext_code
                    account_name_val = ext_name if ext_name else entry.account_name
                else:
                    account_code_val = entry.account_code
                    account_name_val = entry.account_name

                writer.writerow([
                    date_str,
                    bukti_str,
                    account_code_val,
                    account_name_val,
                    f"{entry.debit:.2f}",
                    f"{entry.credit:.2f}",
                    journal.description,
                ])

        csv_content = output.getvalue()
        sha256_hash = hashlib.sha256(csv_content.encode('utf-8-sig')).hexdigest()

        audit(
            action='finance.accounting.export',
            entity_type='LedgerJournal',
            entity_id=sha256_hash,
            actor_id=actor_id,
            role=role,
            foundation_id=foundation_id,
            school_id=school_id,
            ip_address=ip_address,
            diff={
                'system': ExternalLedgerSystem.ACCURATE,
                'journal_count': len(journals),
                'entry_count': total_entries,
                'sha256': sha256_hash,
                'start_date': str(start_date) if start_date else None,
                'end_date': str(end_date) if end_date else None,
                'school_id': school_id,
                'ref_type': ref_type,
            },
        )

        return csv_content, sha256_hash, len(journals)

    @classmethod
    def export_jurnal(
        cls,
        foundation_id: int,
        start_date: Optional[Union[date, str]] = None,
        end_date: Optional[Union[date, str]] = None,
        school_id: Optional[int] = None,
        ref_type: Optional[str] = None,
        actor_id: Optional[str] = None,
        role: str = '',
        ip_address: Optional[str] = None,
    ) -> tuple[str, str, int]:
        """
        Export general ledger journals in Mekari Jurnal.id format.
        Header: Transaction Date,Transaction Number,Account Code,Account Name,Debit,Credit,Description
        Returns (csv_string, sha256_hash, journal_count).
        """
        journals = cls.get_and_validate_journals(
            foundation_id=foundation_id,
            start_date=start_date,
            end_date=end_date,
            school_id=school_id,
            ref_type=ref_type,
        )
        mappings = cls.get_account_mappings(foundation_id, ExternalLedgerSystem.JURNAL)

        output = io.StringIO()
        writer = csv.writer(output, lineterminator='\r\n')
        writer.writerow([
            "Transaction Date",
            "Transaction Number",
            "Account Code",
            "Account Name",
            "Debit",
            "Credit",
            "Description",
        ])

        total_entries = 0
        for journal in journals:
            date_str = journal.occurred_at.strftime('%d/%m/%Y')
            tx_number = journal.number
            for entry in journal.entries.all():
                total_entries += 1
                if entry.account_code in mappings:
                    ext_code, ext_name = mappings[entry.account_code]
                    account_code_val = ext_code
                    account_name_val = ext_name if ext_name else entry.account_name
                else:
                    account_code_val = entry.account_code
                    account_name_val = entry.account_name

                writer.writerow([
                    date_str,
                    tx_number,
                    account_code_val,
                    account_name_val,
                    f"{entry.debit:.2f}",
                    f"{entry.credit:.2f}",
                    journal.description,
                ])

        csv_content = output.getvalue()
        sha256_hash = hashlib.sha256(csv_content.encode('utf-8-sig')).hexdigest()

        audit(
            action='finance.accounting.export',
            entity_type='LedgerJournal',
            entity_id=sha256_hash,
            actor_id=actor_id,
            role=role,
            foundation_id=foundation_id,
            school_id=school_id,
            ip_address=ip_address,
            diff={
                'system': ExternalLedgerSystem.JURNAL,
                'journal_count': len(journals),
                'entry_count': total_entries,
                'sha256': sha256_hash,
                'start_date': str(start_date) if start_date else None,
                'end_date': str(end_date) if end_date else None,
                'school_id': school_id,
                'ref_type': ref_type,
            },
        )

        return csv_content, sha256_hash, len(journals)

    @staticmethod
    def parse_date(date_str: str) -> date:
        """Parse date from YYYY-MM-DD or DD/MM/YYYY formats."""
        for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
            try:
                return datetime.strptime(date_str.strip(), fmt).date()
            except ValueError:
                continue
        raise ValueError(f"Format tanggal tidak valid: {date_str}. Gunakan format YYYY-MM-DD atau DD/MM/YYYY.")
