"""Tests for the Bank SFTP pull interface/stub (spec/14 CMP-024, CMP-026)."""
import datetime
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from apps.finance.models import (
    BankSftpConfig,
    BankStatementFileFormat,
    GatewaySettlementBatch,
    SettlementBatchStatus,
)
from apps.finance.services import get_bank_sftp_configs, set_bank_sftp_config
from apps.finance.services.bank_sftp_pull import BankSftpPullError, fetch_bank_statement_via_sftp
from apps.finance.services.reconciliation import record_bank_sftp_pull_failure
from apps.identity.models import Foundation, RoleAssignment, School
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id

User = get_user_model()

DATE = datetime.date(2026, 9, 14)


def build_fixture(foundation_name="Yayasan Bank SFTP Test"):
    clear_current_foundation_id()
    foundation = Foundation.objects.create(
        legal_name=foundation_name, brand_name=foundation_name, npwp="01.777.666.5-444.000", address="Yogyakarta",
    )
    set_current_foundation_id(foundation.id)
    npsn_suffix = str(abs(hash(foundation_name)) % 100000).zfill(5)
    school = School.all_tenants.create(
        foundation_id=foundation.id, name="SD Bank SFTP Test", npsn=f"701{npsn_suffix}", level=School.LEVEL_SD, base_currency="IDR",
    )
    finance_user = User.objects.create(
        foundation_id=foundation.id, phone_e164=f"+6284{npsn_suffix}", email=f"finance.{npsn_suffix}@sftp.school.id", full_name="Bendahara SFTP",
    )
    RoleAssignment.all_tenants.create(
        foundation_id=foundation.id, user=finance_user, role=RoleAssignment.ROLE_FINANCE_OFFICER,
        scope_type=RoleAssignment.SCOPE_FOUNDATION, scope_id=foundation.id,
    )
    return {'foundation': foundation, 'school': school, 'finance_user': finance_user}


class SetBankSftpConfigTests(TestCase):
    def setUp(self):
        self.fx = build_fixture()

    def test_set_config(self):
        config = set_bank_sftp_config(
            self.fx['school'], bank_code='bca', host='sftp.bca.co.id', port=22,
            username='educore_sd001', remote_directory='/statements', file_format=BankStatementFileFormat.MT940,
        )
        self.assertEqual(config.bank_code, 'BCA')
        self.assertEqual(config.host, 'sftp.bca.co.id')

    def test_upsert_keeps_one_row_per_bank(self):
        set_bank_sftp_config(self.fx['school'], bank_code='BCA', host='sftp.bca.co.id')
        set_bank_sftp_config(self.fx['school'], bank_code='BCA', host='sftp2.bca.co.id')
        self.assertEqual(BankSftpConfig.all_tenants.filter(school=self.fx['school'], bank_code='BCA').count(), 1)

    def test_multiple_banks_per_school_allowed(self):
        set_bank_sftp_config(self.fx['school'], bank_code='BCA', host='sftp.bca.co.id')
        set_bank_sftp_config(self.fx['school'], bank_code='MANDIRI', host='sftp.mandiri.co.id')
        self.assertEqual(get_bank_sftp_configs(self.fx['school']).count(), 2)

    def test_no_password_field_exists_on_model(self):
        """CMP-024/026 note: BankSftpConfig stores connection metadata only, never secrets."""
        field_names = {f.name for f in BankSftpConfig._meta.get_fields()}
        self.assertNotIn('password', field_names)
        self.assertNotIn('private_key', field_names)
        self.assertNotIn('secret', field_names)


class FetchBankStatementViaSftpStubTests(TestCase):
    def setUp(self):
        self.fx = build_fixture()
        self.config = set_bank_sftp_config(self.fx['school'], bank_code='BCA', host='sftp.bca.co.id')

    def test_always_raises_without_env_credential(self):
        with self.assertRaises(BankSftpPullError):
            fetch_bank_statement_via_sftp(self.config, DATE)

    def test_error_message_names_the_bank_and_points_to_manual_upload(self):
        with self.assertRaises(BankSftpPullError) as ctx:
            fetch_bank_statement_via_sftp(self.config, DATE)
        self.assertIn('BCA', str(ctx.exception))
        self.assertIn('upload', str(ctx.exception).lower())


class RecordBankSftpPullFailureTests(TestCase):
    def setUp(self):
        self.fx = build_fixture()

    def test_records_failed_batch(self):
        result = record_bank_sftp_pull_failure(
            bank_code='BCA', settlement_date=DATE, foundation_id=self.fx['foundation'].id, error='stub not implemented',
        )
        self.assertIn('error', result)
        batch = GatewaySettlementBatch.all_tenants.get(
            foundation_id=self.fx['foundation'].id, provider='BANK_SFTP_BCA', settlement_date=DATE,
        )
        self.assertEqual(batch.status, SettlementBatchStatus.FAILED)
        self.assertIn('stub not implemented', batch.error_message)


class PullBankStatementsCommandTests(TestCase):
    def setUp(self):
        self.fx = build_fixture()
        set_bank_sftp_config(self.fx['school'], bank_code='BCA', host='sftp.bca.co.id')
        set_bank_sftp_config(self.fx['school'], bank_code='MANDIRI', host='sftp.mandiri.co.id')

    def test_command_marks_all_configs_failed_and_continues(self):
        out = StringIO()
        call_command('pull_bank_statements', '--date=2026-09-14', '--force', stdout=out)
        output = out.getvalue()
        self.assertIn('Failed: 2', output)

        failed_batches = GatewaySettlementBatch.all_tenants.filter(
            foundation_id=self.fx['foundation'].id, status=SettlementBatchStatus.FAILED,
        )
        self.assertEqual(failed_batches.count(), 2)

    def test_inactive_config_skipped(self):
        set_bank_sftp_config(self.fx['school'], bank_code='BCA', host='sftp.bca.co.id', is_active=False)
        out = StringIO()
        call_command('pull_bank_statements', '--date=2026-09-14', '--force', stdout=out)
        self.assertIn('Failed: 1', out.getvalue())

    def test_school_id_filter(self):
        other_fx = build_fixture(foundation_name="Yayasan Bank SFTP Other")
        set_bank_sftp_config(other_fx['school'], bank_code='BRI', host='sftp.bri.co.id')

        out = StringIO()
        call_command('pull_bank_statements', '--date=2026-09-14', f'--school-id={self.fx["school"].id}', '--force', stdout=out)
        self.assertIn('configs=2', out.getvalue())


class BankSftpConfigListViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.fx = build_fixture()

    def test_put_then_get_via_api(self):
        self.client.force_authenticate(user=self.fx['finance_user'])
        res = self.client.put(
            f'/api/v1/finance/schools/{self.fx["school"].id}/bank-sftp-configs/',
            {'bank_code': 'BCA', 'host': 'sftp.bca.co.id', 'port': 22, 'username': 'educore', 'file_format': 'MT940'},
            format='json',
        )
        self.assertEqual(res.status_code, 200, res.content)

        res2 = self.client.get(f'/api/v1/finance/schools/{self.fx["school"].id}/bank-sftp-configs/')
        self.assertEqual(res2.status_code, 200, res2.content)
        self.assertEqual(len(res2.json()), 1)
        self.assertEqual(res2.json()[0]['bank_code'], 'BCA')
        self.assertNotIn('password', res2.json()[0])

    def test_cross_tenant_school_404(self):
        other_fx = build_fixture(foundation_name="Yayasan Bank SFTP Other View")
        self.client.force_authenticate(user=self.fx['finance_user'])
        set_current_foundation_id(self.fx['foundation'].id)
        res = self.client.get(f'/api/v1/finance/schools/{other_fx["school"].id}/bank-sftp-configs/')
        self.assertEqual(res.status_code, 404)
