"""A reconciliation case can only be paid in cash while it is OPEN.

settle_reconciliation_with_cash used to top the wallet up whatever the case's
status, so a settled, invoiced or written-off case could be credited again
through the API or the web console.
"""
import datetime
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.identity.models import Person, RoleAssignment, Staff, User
from apps.wallet.models import WalletReconciliationStatus
from apps.wallet.services import settle_reconciliation_with_cash, write_off_reconciliation_case
from apps.wallet.tests.test_pos_checkout import build_pos_fixture
from apps.wallet.tests.test_reconciliation import attach_financial_guardian
from apps.wallet.tests.test_reconciliation_followup import make_open_case
from apps.wallet.tests.test_wallet_core import build_wallet_fixture
from educore.middleware.tenancy import clear_current_foundation_id


class CashOnlyWhileOpenTests(TestCase):
    def setUp(self):
        self.fx = build_wallet_fixture()
        self.merchant, self.product, self.terminal = build_pos_fixture(self.fx)
        attach_financial_guardian(self.fx)
        self.case = make_open_case(self.fx, self.merchant, self.product, self.terminal)
        self.officer = self.fx['finance_user']  # finance_officer at the school (finance.payment.write)

    def tearDown(self):
        clear_current_foundation_id()

    def _balance(self):
        self.case.wallet.refresh_from_db()
        return self.case.wallet.balance

    def test_service_refuses_a_case_that_is_no_longer_open(self):
        write_off_reconciliation_case(self.case, self.officer, 'Siswa pindah')
        before = self._balance()
        with self.assertRaisesRegex(ValueError, 'INVALID_STATE'):
            settle_reconciliation_with_cash(self.case, Decimal('5000'), 'Tunai')
        self.assertEqual(self._balance(), before)

    def test_a_case_cannot_be_settled_twice(self):
        settle_reconciliation_with_cash(self.case, self.case.shortfall, 'Tunai')
        self.case.refresh_from_db()
        self.assertEqual(self.case.status, WalletReconciliationStatus.SETTLED)
        before = self._balance()
        with self.assertRaisesRegex(ValueError, 'INVALID_STATE'):
            settle_reconciliation_with_cash(self.case, Decimal('5000'), 'Tunai lagi')
        self.assertEqual(self._balance(), before)

    def test_json_api_answers_400_and_does_not_credit(self):
        write_off_reconciliation_case(self.case, self.officer, 'Siswa pindah')
        before = self._balance()
        api = APIClient()
        api.force_authenticate(user=self.officer)
        response = api.post(
            f'/api/v1/wallet-reconciliations/{self.case.pk}/settle-cash/', {'amount': '5000'}, format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._balance(), before)

    def test_web_console_flashes_already_processed_and_does_not_credit(self):
        person = Person.all_tenants.create(foundation_id=self.fx['foundation'].id, nik='3171010101010601', full_name='Bendahara')
        Staff.all_tenants.create(
            foundation_id=self.fx['foundation'].id, person=person, user=self.officer, school=self.fx['school'],
            join_date=datetime.date(2021, 1, 1), status=Staff.STATUS_ACTIVE,
        )
        write_off_reconciliation_case(self.case, self.officer, 'Siswa pindah')
        before = self._balance()
        client = Client()  # not APIClient: its force-auth handler hides session users from TenancyMiddleware
        client.force_login(self.officer)
        response = client.post(
            f"{reverse('canteen-recon-cash', kwargs={'pk': self.case.pk})}?school_id={self.fx['school'].id}",
            {'amount': '5000'}, follow=True,
        )
        self.assertContains(response, 'sudah diproses')
        self.assertEqual(self._balance(), before)
