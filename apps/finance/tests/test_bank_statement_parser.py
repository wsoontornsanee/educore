"""Tests for the MT940/CAMT.053 bank statement parser (spec/14 CMP-026, FIN-024)."""
import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.finance.models import (
    GatewaySettlementBatch,
    Payment,
    PaymentDiscrepancy,
    PaymentMethod,
    SettlementBatchStatus,
)
from apps.finance.services.bank_statement_parser import (
    BankStatementParseError,
    parse_bank_statement,
    parse_camt053,
    parse_mt940,
)
from apps.finance.services.reconciliation import reconcile_bank_statement_file

User = get_user_model()

DATE = datetime.date(2026, 9, 14)

MT940_SAMPLE = (
    ":20:STMT0001\n"
    ":25:1234567890/IDR\n"
    ":28C:1/1\n"
    ":60F:C260913IDR1000000,00\n"
    ":61:260914C500000,00NMSCPAY-EXT-001//BANKREF001\n"
    ":86:PAYMENT FOR INVOICE INV-2026-000123\n"
    ":61:260914D50000,00NMSCFEE001\n"
    ":86:BANK ADMIN FEE\n"
    ":62F:C260914IDR1450000,00\n"
)

CAMT053_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02">
  <BkToCstmrStmt>
    <Stmt>
      <Id>STMT001</Id>
      <Ntry>
        <Amt Ccy="IDR">500000.00</Amt>
        <CdtDbtInd>CRDT</CdtDbtInd>
        <BookgDt><Dt>2026-09-14</Dt></BookgDt>
        <NtryRef>NTRYREF001</NtryRef>
        <NtryDtls>
          <TxDtls>
            <Refs><EndToEndId>PAY-EXT-002</EndToEndId></Refs>
            <RmtInf><Ustrd>Invoice payment</Ustrd></RmtInf>
          </TxDtls>
        </NtryDtls>
      </Ntry>
      <Ntry>
        <Amt Ccy="IDR">10000.00</Amt>
        <CdtDbtInd>DBIT</CdtDbtInd>
        <BookgDt><Dt>2026-09-14</Dt></BookgDt>
        <NtryRef>FEE001</NtryRef>
      </Ntry>
    </Stmt>
  </BkToCstmrStmt>
</Document>
"""


class ParseMt940Tests(TestCase):
    def test_credit_entry_parsed(self):
        records = parse_mt940(MT940_SAMPLE, bank_code='BCA')
        self.assertEqual(len(records), 1)
        r = records[0]
        self.assertEqual(r['external_id'], 'PAY-EXT-001')
        self.assertEqual(r['amount'], Decimal('500000.00'))
        self.assertEqual(r['fee'], Decimal('0.00'))
        self.assertEqual(r['net'], Decimal('500000.00'))
        self.assertEqual(r['bank'], 'BCA')
        self.assertEqual(r['settled_at'].date(), datetime.date(2026, 9, 14))

    def test_debit_entry_excluded(self):
        records = parse_mt940(MT940_SAMPLE, bank_code='BCA')
        external_ids = [r['external_id'] for r in records]
        self.assertNotIn('FEE001', external_ids)

    def test_no_statement_lines_raises(self):
        with self.assertRaises(BankStatementParseError):
            parse_mt940("not an mt940 file at all")

    def test_malformed_line61_raises(self):
        bad = ":20:STMT0001\n:61:GARBAGE\n"
        with self.assertRaises(BankStatementParseError):
            parse_mt940(bad)


class ParseCamt053Tests(TestCase):
    def test_credit_entry_parsed(self):
        records = parse_camt053(CAMT053_SAMPLE, bank_code='MANDIRI')
        self.assertEqual(len(records), 1)
        r = records[0]
        self.assertEqual(r['external_id'], 'PAY-EXT-002')
        self.assertEqual(r['amount'], Decimal('500000.00'))
        self.assertEqual(r['bank'], 'MANDIRI')
        self.assertEqual(r['settled_at'].date(), datetime.date(2026, 9, 14))

    def test_debit_entry_excluded(self):
        records = parse_camt053(CAMT053_SAMPLE)
        external_ids = [r['external_id'] for r in records]
        self.assertNotIn('FEE001', external_ids)

    def test_invalid_xml_raises(self):
        with self.assertRaises(BankStatementParseError):
            parse_camt053("<not><valid</xml>")

    def test_missing_stmt_element_raises(self):
        with self.assertRaises(BankStatementParseError):
            parse_camt053("<Document xmlns='urn:x'><Other/></Document>")

    def test_reference_falls_back_to_ntry_ref_when_no_end_to_end_id(self):
        xml = CAMT053_SAMPLE.replace(
            "<Refs><EndToEndId>PAY-EXT-002</EndToEndId></Refs>", ""
        )
        records = parse_camt053(xml)
        self.assertEqual(records[0]['external_id'], 'NTRYREF001')


class ParseBankStatementDispatcherTests(TestCase):
    def test_dispatches_mt940(self):
        records = parse_bank_statement(MT940_SAMPLE, 'MT940')
        self.assertEqual(len(records), 1)

    def test_dispatches_camt053(self):
        records = parse_bank_statement(CAMT053_SAMPLE, 'CAMT053')
        self.assertEqual(len(records), 1)

    def test_unsupported_format_raises(self):
        with self.assertRaises(BankStatementParseError):
            parse_bank_statement(MT940_SAMPLE, 'MT942')


class ReconcileBankStatementFileTests(TestCase):
    def setUp(self):
        from apps.identity.models import Foundation, Person, School, Student
        from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id

        clear_current_foundation_id()
        foundation = Foundation.objects.create(
            legal_name="Yayasan Recon Bank Test", brand_name="Recon Bank Test",
            npwp="01.111.222.3-444.000", address="Semarang",
        )
        set_current_foundation_id(foundation.id)
        self.school = School.all_tenants.create(
            foundation_id=foundation.id, name="SD Recon Bank Test", npsn="60100010",
            level=School.LEVEL_SD, base_currency="IDR",
        )
        person = Person.all_tenants.create(foundation_id=foundation.id, nik="3473010199990001", full_name="Siswa Recon Bank")
        self.student = Student.all_tenants.create(
            foundation_id=foundation.id, school=self.school, person=person, nisn="99990001",
            nis="RB-99990001", status=Student.STATUS_ACTIVE,
        )
        self.foundation = foundation

    def _discrepancies(self):
        return PaymentDiscrepancy.all_tenants.filter(foundation_id=self.foundation.id)

    def test_matched_payment_auto_settled(self):
        Payment.all_tenants.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            reference='PAY/TEST/2026/000001',
            external_id='PAY-EXT-001',
            amount=Decimal('500000.00'),
            method=PaymentMethod.MANUAL,
        )
        result = reconcile_bank_statement_file(
            file_content=MT940_SAMPLE, file_format='MT940', bank_code='BCA',
            settlement_date=DATE, foundation_id=self.foundation.id,
        )
        self.assertEqual(result['matched'], 1)
        self.assertEqual(result['missing'], 0)
        batch = GatewaySettlementBatch.all_tenants.get(
            foundation_id=self.foundation.id, provider='BANK_BCA', settlement_date=DATE,
        )
        self.assertEqual(batch.status, SettlementBatchStatus.COMPLETED)

    def test_missing_payment_creates_discrepancy(self):
        result = reconcile_bank_statement_file(
            file_content=MT940_SAMPLE, file_format='MT940', bank_code='BCA',
            settlement_date=DATE, foundation_id=self.foundation.id,
        )
        self.assertEqual(result['missing'], 1)
        self.assertEqual(self._discrepancies().count(), 1)

    def test_parse_error_marks_batch_failed(self):
        result = reconcile_bank_statement_file(
            file_content="garbage", file_format='MT940', bank_code='BCA',
            settlement_date=DATE, foundation_id=self.foundation.id,
        )
        self.assertIn('error', result)
        batch = GatewaySettlementBatch.all_tenants.get(
            foundation_id=self.foundation.id, provider='BANK_BCA', settlement_date=DATE,
        )
        self.assertEqual(batch.status, SettlementBatchStatus.FAILED)

    def test_dry_run_creates_no_discrepancies(self):
        result = reconcile_bank_statement_file(
            file_content=MT940_SAMPLE, file_format='MT940', bank_code='BCA',
            settlement_date=DATE, foundation_id=self.foundation.id, dry_run=True,
        )
        self.assertTrue(result['dry_run'])
        self.assertEqual(self._discrepancies().count(), 0)


class ReconciliationBankStatementUploadViewTests(TestCase):
    def setUp(self):
        from apps.identity.models import Foundation, RoleAssignment, School
        from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id

        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Bank Statement Test", brand_name="Bank Statement Test",
            npwp="01.999.888.7-666.000", address="Medan",
        )
        set_current_foundation_id(self.foundation.id)
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SD Bank Statement Test", npsn="50100010",
            level=School.LEVEL_SD, base_currency="IDR",
        )
        self.finance_user = User.objects.create(
            foundation_id=self.foundation.id, phone_e164="+6283000000001",
            email="bendahara@bankstmt.school.id", full_name="Bendahara Bank Statement",
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id, user=self.finance_user, role=RoleAssignment.ROLE_FINANCE_OFFICER,
            scope_type=RoleAssignment.SCOPE_FOUNDATION, scope_id=self.foundation.id,
        )
        self.client = APIClient()

    def test_upload_mt940_via_api(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.client.force_authenticate(user=self.finance_user)
        upload = SimpleUploadedFile('statement.sta', MT940_SAMPLE.encode('utf-8'), content_type='text/plain')
        res = self.client.post(
            '/api/v1/finance/reconciliation/bank-statements/upload/',
            {'file': upload, 'format': 'MT940', 'bank_code': 'BCA', 'settlement_date': '2026-09-14'},
            format='multipart',
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['missing'], 1)

    def test_upload_missing_file_returns_400(self):
        self.client.force_authenticate(user=self.finance_user)
        res = self.client.post(
            '/api/v1/finance/reconciliation/bank-statements/upload/',
            {'format': 'MT940', 'bank_code': 'BCA', 'settlement_date': '2026-09-14'},
            format='multipart',
        )
        self.assertEqual(res.status_code, 400)

    def test_upload_bad_date_returns_400(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.client.force_authenticate(user=self.finance_user)
        upload = SimpleUploadedFile('statement.sta', MT940_SAMPLE.encode('utf-8'), content_type='text/plain')
        res = self.client.post(
            '/api/v1/finance/reconciliation/bank-statements/upload/',
            {'file': upload, 'format': 'MT940', 'bank_code': 'BCA', 'settlement_date': 'not-a-date'},
            format='multipart',
        )
        self.assertEqual(res.status_code, 400)
