import csv
import datetime
import hashlib
import io
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.core.models import AuditEvent
from apps.finance.models import (
    AccountCode,
    ExternalAccountMapping,
    ExternalLedgerSystem,
    LedgerEntry,
    LedgerJournal,
)
from apps.finance.services.accounting_export import (
    AccountingExportService,
    UnbalancedJournalExportError,
)
from apps.identity.models import Foundation, RoleAssignment, School, User
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id, tenant_context


class AccountingExportTests(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.client = APIClient()

        # Foundation A
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan EduCore Cemerlang",
            brand_name="EduCore Cemerlang",
            npwp="01.888.777.6-555.000",
            address="Jakarta Selatan",
        )
        set_current_foundation_id(self.foundation.id)

        self.school_1 = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SMA EduCore 1",
            npsn="20101111",
            level=School.LEVEL_SMA,
            base_currency="IDR",
        )
        self.school_2 = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SMP EduCore 2",
            npsn="20102222",
            level=School.LEVEL_SMP,
            base_currency="IDR",
        )

        # Users
        self.finance_user = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6281111111111",
            email="finance@educore.sch.id",
            full_name="Staff Keuangan",
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id,
            user=self.finance_user,
            role=RoleAssignment.ROLE_FINANCE_OFFICER,
            scope_type=RoleAssignment.SCOPE_FOUNDATION,
            scope_id=self.foundation.id,
        )

        self.parent_user = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6282222222222",
            email="parent@educore.sch.id",
            full_name="Orang Tua Murid",
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id,
            user=self.parent_user,
            role=RoleAssignment.ROLE_PARENT,
            scope_type=RoleAssignment.SCOPE_FOUNDATION,
            scope_id=self.foundation.id,
        )

        # Foundation B (for tenancy isolation)
        self.other_foundation = Foundation.objects.create(
            legal_name="Yayasan Lain Sejahtera",
            brand_name="Lain Sejahtera",
            npwp="02.999.888.7-666.000",
            address="Surabaya",
        )
        self.other_school = School.all_tenants.create(
            foundation_id=self.other_foundation.id,
            name="SMA Lain",
            npsn="20109999",
            level=School.LEVEL_SMA,
            base_currency="IDR",
        )
        self.other_finance_user = User.objects.create(
            foundation_id=self.other_foundation.id,
            phone_e164="+6283333333333",
            email="finance@lain.sch.id",
            full_name="Staff Keuangan Yayasan Lain",
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.other_foundation.id,
            user=self.other_finance_user,
            role=RoleAssignment.ROLE_FINANCE_OFFICER,
            scope_type=RoleAssignment.SCOPE_FOUNDATION,
            scope_id=self.other_foundation.id,
        )

    def _create_balanced_journal(self, school, number, occurred_at, ref_type="INVOICE", amount=Decimal("500000.00")):
        """Helper to create a balanced 2-entry ledger journal."""
        jrn = LedgerJournal.all_tenants.create(
            foundation_id=school.foundation_id,
            school=school,
            number=number,
            description=f"Jurnal {ref_type} #{number}",
            ref_type=ref_type,
            ref_id=number,
            currency="IDR",
            occurred_at=occurred_at,
        )
        # Dr Kas / Bank 1100
        LedgerEntry.all_tenants.create(
            foundation_id=school.foundation_id,
            school=school,
            journal=jrn,
            account_code=AccountCode.CASH_BANK,
            account_name="Kas / Bank",
            debit=amount,
            credit=Decimal("0.00"),
            currency="IDR",
            occurred_at=occurred_at,
        )
        # Cr Pendapatan SPP 4100
        LedgerEntry.all_tenants.create(
            foundation_id=school.foundation_id,
            school=school,
            journal=jrn,
            account_code=AccountCode.TUITION_REVENUE,
            account_name="Pendapatan SPP",
            debit=Decimal("0.00"),
            credit=amount,
            currency="IDR",
            occurred_at=occurred_at,
        )
        return jrn

    def test_accurate_export_format_and_headers(self):
        """Test Accurate Online export CSV formatting, columns, dates DD/MM/YYYY, and 2dp numbers."""
        self.client.force_authenticate(user=self.finance_user)
        dt = timezone.datetime(2026, 9, 15, 10, 30, tzinfo=datetime.timezone.utc)
        self._create_balanced_journal(self.school_1, "JRN/SMA/2026/000001", dt, amount=Decimal("750000.00"))

        resp = self.client.get('/api/v1/finance/accounting/export/accurate/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp['Content-Type'], 'text/csv; charset=utf-8')
        self.assertIn('attachment; filename="accurate_export_', resp['Content-Disposition'])
        self.assertTrue(resp.has_header('X-Export-SHA256'))
        self.assertEqual(resp['X-Export-Journal-Count'], '1')

        # Read CSV content
        content = resp.content.decode('utf-8-sig')
        reader = list(csv.reader(io.StringIO(content)))
        expected_header = [
            "Tanggal Transaksi",
            "No Bukti",
            "Nomor Akun",
            "Nama Akun",
            "Debit",
            "Kredit",
            "Keterangan",
        ]
        self.assertEqual(reader[0], expected_header)
        self.assertEqual(len(reader), 3)  # Header + 2 entries

        # Entry 1 (Debit)
        row1 = reader[1]
        self.assertEqual(row1[0], "15/09/2026")
        self.assertEqual(row1[1], "JRN/SMA/2026/000001")
        self.assertEqual(row1[2], "1100")
        self.assertEqual(row1[3], "Kas / Bank")
        self.assertEqual(row1[4], "750000.00")
        self.assertEqual(row1[5], "0.00")
        self.assertEqual(row1[6], "Jurnal INVOICE #JRN/SMA/2026/000001")

        # Entry 2 (Credit)
        row2 = reader[2]
        self.assertEqual(row2[0], "15/09/2026")
        self.assertEqual(row2[1], "JRN/SMA/2026/000001")
        self.assertEqual(row2[2], "4100")
        self.assertEqual(row2[3], "Pendapatan SPP")
        self.assertEqual(row2[4], "0.00")
        self.assertEqual(row2[5], "750000.00")

    def test_jurnal_export_format_and_headers(self):
        """Test Mekari Jurnal export CSV formatting, columns, dates DD/MM/YYYY, and 2dp numbers."""
        self.client.force_authenticate(user=self.finance_user)
        dt = timezone.datetime(2026, 9, 18, 8, 0, tzinfo=datetime.timezone.utc)
        self._create_balanced_journal(self.school_1, "JRN/SMA/2026/000002", dt, amount=Decimal("1200000.00"))

        resp = self.client.get('/api/v1/finance/accounting/export/jurnal/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp['Content-Type'], 'text/csv; charset=utf-8')
        self.assertIn('attachment; filename="jurnal_export_', resp['Content-Disposition'])
        self.assertTrue(resp.has_header('X-Export-SHA256'))
        self.assertEqual(resp['X-Export-Journal-Count'], '1')

        content = resp.content.decode('utf-8-sig')
        reader = list(csv.reader(io.StringIO(content)))
        expected_header = [
            "Transaction Date",
            "Transaction Number",
            "Account Code",
            "Account Name",
            "Debit",
            "Credit",
            "Description",
        ]
        self.assertEqual(reader[0], expected_header)
        self.assertEqual(len(reader), 3)

        # Debit entry
        row1 = reader[1]
        self.assertEqual(row1[0], "18/09/2026")
        self.assertEqual(row1[1], "JRN/SMA/2026/000002")
        self.assertEqual(row1[2], "1100")
        self.assertEqual(row1[3], "Kas / Bank")
        self.assertEqual(row1[4], "1200000.00")
        self.assertEqual(row1[5], "0.00")

        # Credit entry
        row2 = reader[2]
        self.assertEqual(row2[0], "18/09/2026")
        self.assertEqual(row2[1], "JRN/SMA/2026/000002")
        self.assertEqual(row2[2], "4100")
        self.assertEqual(row2[3], "Pendapatan SPP")
        self.assertEqual(row2[4], "0.00")
        self.assertEqual(row2[5], "1200000.00")

    def test_account_mapping_overrides_in_export(self):
        """Test that configured ExternalAccountMapping overrides internal account codes and names."""
        self.client.force_authenticate(user=self.finance_user)

        # Accurate mapping: 1100 -> 1-10001 (Kas Utama BCA)
        ExternalAccountMapping.all_tenants.create(
            foundation_id=self.foundation.id,
            system=ExternalLedgerSystem.ACCURATE,
            internal_code=AccountCode.CASH_BANK,
            external_code="1-10001",
            external_name="Kas Utama BCA",
        )
        # Jurnal mapping: 4100 -> 4-40001 (Pendapatan SPP Reguler)
        ExternalAccountMapping.all_tenants.create(
            foundation_id=self.foundation.id,
            system=ExternalLedgerSystem.JURNAL,
            internal_code=AccountCode.TUITION_REVENUE,
            external_code="4-40001",
            external_name="Pendapatan SPP Reguler",
        )

        dt = timezone.datetime(2026, 9, 10, 10, 0, tzinfo=datetime.timezone.utc)
        self._create_balanced_journal(self.school_1, "JRN/SMA/2026/000003", dt, amount=Decimal("300000.00"))

        # 1. Test Accurate Export
        resp_acc = self.client.get('/api/v1/finance/accounting/export/accurate/')
        self.assertEqual(resp_acc.status_code, status.HTTP_200_OK)
        rows_acc = list(csv.reader(io.StringIO(resp_acc.content.decode('utf-8-sig'))))
        # 1100 is mapped to 1-10001
        self.assertEqual(rows_acc[1][2], "1-10001")
        self.assertEqual(rows_acc[1][3], "Kas Utama BCA")
        # 4100 is unmapped for Accurate -> retains internal 4100 & Pendapatan SPP
        self.assertEqual(rows_acc[2][2], "4100")
        self.assertEqual(rows_acc[2][3], "Pendapatan SPP")

        # 2. Test Jurnal Export
        resp_jur = self.client.get('/api/v1/finance/accounting/export/jurnal/')
        self.assertEqual(resp_jur.status_code, status.HTTP_200_OK)
        rows_jur = list(csv.reader(io.StringIO(resp_jur.content.decode('utf-8-sig'))))
        # 1100 is unmapped for Jurnal -> retains internal 1100
        self.assertEqual(rows_jur[1][2], "1100")
        # 4100 is mapped to 4-40001 for Jurnal
        self.assertEqual(rows_jur[2][2], "4-40001")
        self.assertEqual(rows_jur[2][3], "Pendapatan SPP Reguler")

    def test_pre_export_integrity_check_fails_on_unbalanced_journal(self):
        """Export must immediately abort with 400 when an unbalanced journal exists in the batch."""
        self.client.force_authenticate(user=self.finance_user)
        dt = timezone.datetime(2026, 9, 12, 10, 0, tzinfo=datetime.timezone.utc)

        # Create an unbalanced journal directly in DB
        unbalanced_jrn = LedgerJournal.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school_1,
            number="JRN/SMA/2026/UNBALANCED",
            description="Unbalanced test journal",
            ref_type="INVOICE",
            ref_id="BAD1",
            occurred_at=dt,
        )
        LedgerEntry.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school_1,
            journal=unbalanced_jrn,
            account_code=AccountCode.CASH_BANK,
            account_name="Kas / Bank",
            debit=Decimal("100000.00"),
            credit=Decimal("0.00"),
            occurred_at=dt,
        )
        LedgerEntry.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school_1,
            journal=unbalanced_jrn,
            account_code=AccountCode.TUITION_REVENUE,
            account_name="Pendapatan SPP",
            debit=Decimal("0.00"),
            credit=Decimal("90000.00"),  # Deficit 10,000
            occurred_at=dt,
        )

        # Service level assertion
        with self.assertRaises(UnbalancedJournalExportError) as ctx:
            AccountingExportService.export_accurate(foundation_id=self.foundation.id)
        self.assertIn("JRN/SMA/2026/UNBALANCED", str(ctx.exception))

        # API level assertion
        resp = self.client.get('/api/v1/finance/accounting/export/accurate/')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data['error'], 'UNBALANCED_JOURNAL')
        self.assertEqual(resp.data['journal_number'], 'JRN/SMA/2026/UNBALANCED')

        # Jurnal API level assertion
        resp_jur = self.client.get('/api/v1/finance/accounting/export/jurnal/')
        self.assertEqual(resp_jur.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp_jur.data['error'], 'UNBALANCED_JOURNAL')

    def test_filter_by_date_range_school_and_ref_type(self):
        """Test filtering journals by start_date, end_date, school_id, and ref_type."""
        self.client.force_authenticate(user=self.finance_user)

        # August journal - school 1 - INVOICE
        self._create_balanced_journal(
            self.school_1,
            "JRN/SMA/2026/AUG",
            timezone.datetime(2026, 8, 20, 10, 0, tzinfo=datetime.timezone.utc),
            ref_type="INVOICE",
            amount=Decimal("100000.00"),
        )
        # September journal 1 - school 1 - INVOICE
        self._create_balanced_journal(
            self.school_1,
            "JRN/SMA/2026/SEP1",
            timezone.datetime(2026, 9, 5, 10, 0, tzinfo=datetime.timezone.utc),
            ref_type="INVOICE",
            amount=Decimal("200000.00"),
        )
        # September journal 2 - school 1 - PAYMENT
        self._create_balanced_journal(
            self.school_1,
            "JRN/SMA/2026/SEP2",
            timezone.datetime(2026, 9, 10, 10, 0, tzinfo=datetime.timezone.utc),
            ref_type="PAYMENT",
            amount=Decimal("300000.00"),
        )
        # September journal 3 - school 2 - INVOICE
        self._create_balanced_journal(
            self.school_2,
            "JRN/SMP/2026/SEP3",
            timezone.datetime(2026, 9, 15, 10, 0, tzinfo=datetime.timezone.utc),
            ref_type="INVOICE",
            amount=Decimal("400000.00"),
        )

        # 1. Filter by September only
        resp_sep = self.client.get('/api/v1/finance/accounting/export/accurate/?start_date=2026-09-01&end_date=2026-09-30')
        self.assertEqual(resp_sep.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_sep['X-Export-Journal-Count'], '3')

        # 2. Filter by school_1 only
        resp_sch1 = self.client.get(f'/api/v1/finance/accounting/export/accurate/?school_id={self.school_1.id}')
        self.assertEqual(resp_sch1.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_sch1['X-Export-Journal-Count'], '3')

        # 3. Filter by ref_type=PAYMENT only
        resp_pay = self.client.get('/api/v1/finance/accounting/export/accurate/?ref_type=PAYMENT')
        self.assertEqual(resp_pay.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_pay['X-Export-Journal-Count'], '1')

        # 4. Filter combination: Sept 2026, school 1, ref_type INVOICE -> exactly 1 journal
        resp_combo = self.client.get(
            f'/api/v1/finance/accounting/export/accurate/?start_date=2026-09-01&end_date=2026-09-30&school_id={self.school_1.id}&ref_type=INVOICE'
        )
        self.assertEqual(resp_combo.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_combo['X-Export-Journal-Count'], '1')
        rows = list(csv.reader(io.StringIO(resp_combo.content.decode('utf-8-sig'))))
        self.assertEqual(rows[1][1], "JRN/SMA/2026/SEP1")

    def test_export_hash_and_audit_logging(self):
        """Export generates SHA-256 header and logs AuditEvent."""
        self.client.force_authenticate(user=self.finance_user)
        dt = timezone.datetime(2026, 9, 18, 9, 0, tzinfo=datetime.timezone.utc)
        self._create_balanced_journal(self.school_1, "JRN/SMA/2026/AUDIT", dt, amount=Decimal("150000.00"))

        resp = self.client.get('/api/v1/finance/accounting/export/accurate/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        sha256_header = resp['X-Export-SHA256']
        expected_hash = hashlib.sha256(resp.content).hexdigest()
        self.assertEqual(sha256_header, expected_hash)

        # Verify audit record
        audit_rec = AuditEvent.objects.filter(
            action='finance.accounting.export',
            entity_id=sha256_header,
        ).first()
        self.assertIsNotNone(audit_rec)
        self.assertEqual(audit_rec.actor_id, str(self.finance_user.id))
        self.assertEqual(audit_rec.foundation_id, self.foundation.id)
        self.assertEqual(audit_rec.diff['system'], ExternalLedgerSystem.ACCURATE)
        self.assertEqual(audit_rec.diff['journal_count'], 1)

    def test_multi_tenant_isolation(self):
        """Cross-tenant boundaries must prevent leaking journals or mappings between foundations."""
        # Create journal and mapping in Foundation A
        self._create_balanced_journal(
            self.school_1,
            "JRN/FND_A/001",
            timezone.now(),
            amount=Decimal("100000.00"),
        )
        map_a = ExternalAccountMapping.all_tenants.create(
            foundation_id=self.foundation.id,
            system=ExternalLedgerSystem.ACCURATE,
            internal_code=AccountCode.CASH_BANK,
            external_code="FND-A-1100",
        )

        # Create journal and mapping in Foundation B
        self._create_balanced_journal(
            self.other_school,
            "JRN/FND_B/001",
            timezone.now(),
            amount=Decimal("200000.00"),
        )
        map_b = ExternalAccountMapping.all_tenants.create(
            foundation_id=self.other_foundation.id,
            system=ExternalLedgerSystem.ACCURATE,
            internal_code=AccountCode.CASH_BANK,
            external_code="FND-B-1100",
        )

        # Log in as Foundation B finance user
        self.client.force_authenticate(user=self.other_finance_user)

        # 1. Export Accurate for Foundation B: should only contain Foundation B journal
        resp_b = self.client.get('/api/v1/finance/accounting/export/accurate/')
        self.assertEqual(resp_b.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_b['X-Export-Journal-Count'], '1')
        content_b = resp_b.content.decode('utf-8-sig')
        self.assertIn("JRN/FND_B/001", content_b)
        self.assertNotIn("JRN/FND_A/001", content_b)
        self.assertIn("FND-B-1100", content_b)
        self.assertNotIn("FND-A-1100", content_b)

        # 2. Access Foundation A's mapping directly from Foundation B context -> 404
        resp_cross_map = self.client.get(f'/api/v1/finance/accounting/mappings/{map_a.id}/')
        self.assertEqual(resp_cross_map.status_code, status.HTTP_404_NOT_FOUND)

    def test_account_mapping_crud_api(self):
        """Test CRUD operations on /api/v1/finance/accounting/mappings/ ViewSet."""
        self.client.force_authenticate(user=self.finance_user)

        # 1. Create mapping
        payload = {
            'system': ExternalLedgerSystem.ACCURATE,
            'internal_code': AccountCode.ACCOUNTS_RECEIVABLE,
            'external_code': '1-12001',
            'external_name': 'Piutang Siswa Accurate',
            'description': 'Mapping piutang untuk Accurate Online',
        }
        create_resp = self.client.post('/api/v1/finance/accounting/mappings/', payload, format='json')
        self.assertEqual(create_resp.status_code, status.HTTP_201_CREATED)
        mapping_id = create_resp.data['id']
        self.assertEqual(create_resp.data['external_code'], '1-12001')
        self.assertEqual(create_resp.data['foundation_id'], self.foundation.id)

        # 2. List mappings
        list_resp = self.client.get('/api/v1/finance/accounting/mappings/')
        self.assertEqual(list_resp.status_code, status.HTTP_200_OK)
        results = list_resp.data.get('results', list_resp.data)
        self.assertEqual(len(results), 1)

        # 3. Update mapping
        patch_resp = self.client.patch(
            f'/api/v1/finance/accounting/mappings/{mapping_id}/',
            {'external_name': 'Piutang Siswa Updated'},
            format='json',
        )
        self.assertEqual(patch_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(patch_resp.data['external_name'], 'Piutang Siswa Updated')

        # 4. Delete mapping (soft delete)
        del_resp = self.client.delete(f'/api/v1/finance/accounting/mappings/{mapping_id}/')
        self.assertEqual(del_resp.status_code, status.HTTP_204_NO_CONTENT)

        # Assert soft deleted
        mapping_obj = ExternalAccountMapping.all_tenants.with_deleted().get(id=mapping_id)
        self.assertIsNotNone(mapping_obj.deleted_at)

        # List should now be empty
        list_after_del = self.client.get('/api/v1/finance/accounting/mappings/')
        results_after = list_after_del.data.get('results', list_after_del.data)
        self.assertEqual(len(results_after), 0)

        # 5. Invalid internal code rejection
        invalid_payload = {
            'system': ExternalLedgerSystem.ACCURATE,
            'internal_code': '9999_INVALID',
            'external_code': '9-9999',
        }
        inv_resp = self.client.post('/api/v1/finance/accounting/mappings/', invalid_payload, format='json')
        self.assertEqual(inv_resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rbac_permission_enforcement(self):
        """Users without finance permissions must be blocked with HTTP 403."""
        self.client.force_authenticate(user=self.parent_user)

        # Export endpoint blocked
        resp = self.client.get('/api/v1/finance/accounting/export/accurate/')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

        resp_jur = self.client.get('/api/v1/finance/accounting/export/jurnal/')
        self.assertEqual(resp_jur.status_code, status.HTTP_403_FORBIDDEN)

        # Mappings list endpoint blocked
        resp_map = self.client.get('/api/v1/finance/accounting/mappings/')
        self.assertEqual(resp_map.status_code, status.HTTP_403_FORBIDDEN)

        # Mappings create endpoint blocked
        resp_create = self.client.post('/api/v1/finance/accounting/mappings/', {
            'system': ExternalLedgerSystem.ACCURATE,
            'internal_code': AccountCode.CASH_BANK,
            'external_code': '1-10001',
        }, format='json')
        self.assertEqual(resp_create.status_code, status.HTTP_403_FORBIDDEN)

