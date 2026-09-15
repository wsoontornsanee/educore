import datetime
from decimal import Decimal
from django.test import TestCase
from rest_framework.test import APIClient

from apps.identity.models import Foundation, Person, RoleAssignment, School, Student, User
from apps.wallet.models import Wallet, WalletStatus, WalletTransaction
from apps.wallet.services import (
    CurrencyMismatchError,
    InsufficientBalanceError,
    WalletNotActiveError,
    check_spend_allowed,
    get_or_create_wallet,
    reconcile_wallet_balances,
    record_wallet_transaction,
    set_spend_rule,
    topup_wallet,
)
from educore.middleware.tenancy import set_current_foundation_id


def build_wallet_fixture(foundation_name="Yayasan Wallet"):
    foundation = Foundation.objects.create(
        legal_name=foundation_name, brand_name=foundation_name,
        npwp="01.222.333.4-000.000", address="Bandung",
    )
    set_current_foundation_id(foundation.id)

    npsn_suffix = str(abs(hash(foundation_name)) % 100000).zfill(5)
    school = School.all_tenants.create(
        foundation_id=foundation.id, name="SD Wallet Test", npsn=f"301{npsn_suffix}", level=School.LEVEL_SD,
    )

    finance_person = Person.all_tenants.create(foundation_id=foundation.id, nik="3471010101011010", full_name="Bu Rina")
    finance_user = User.objects.create(
        foundation_id=foundation.id, phone_e164=f"+6281{npsn_suffix}", email=f"rina.{npsn_suffix}@school.id", full_name="Bu Rina",
    )
    RoleAssignment.all_tenants.create(
        foundation_id=foundation.id, user=finance_user, role='finance_officer',
        scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=school.id,
    )

    student_person = Person.all_tenants.create(foundation_id=foundation.id, nik="3471010101011020", full_name="Dimas")
    student = Student.all_tenants.create(
        foundation_id=foundation.id, school=school, person=student_person,
        nisn="1234567890", nis="SD-001", status=Student.STATUS_ACTIVE,
    )

    return {'foundation': foundation, 'school': school, 'finance_user': finance_user, 'student': student}


class WalletCoreTests(TestCase):
    def setUp(self):
        self.fx = build_wallet_fixture()
        self.wallet = get_or_create_wallet(self.fx['student'])

    def test_topup_increases_balance(self):
        record = topup_wallet(self.wallet, Decimal('100000'), 'CASH', 'idem-1')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('100000.00'))
        self.assertEqual(record.balance_after, Decimal('100000.00'))

    def test_idempotent_replay_is_noop(self):
        r1 = topup_wallet(self.wallet, Decimal('50000'), 'CASH', 'idem-2')
        r2 = topup_wallet(self.wallet, Decimal('50000'), 'CASH', 'idem-2')
        self.assertEqual(r1.id, r2.id)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('50000.00'))

    def test_negative_balance_rejected(self):
        with self.assertRaises(InsufficientBalanceError):
            record_wallet_transaction(self.wallet, 'PURCHASE', Decimal('-1000'), 'idem-3')

    def test_frozen_wallet_rejects_transaction(self):
        self.wallet.status = WalletStatus.FROZEN
        self.wallet.save()
        with self.assertRaises(WalletNotActiveError):
            topup_wallet(self.wallet, Decimal('10000'), 'CASH', 'idem-4')

    def test_currency_mismatch_rejected(self):
        with self.assertRaises(CurrencyMismatchError):
            record_wallet_transaction(self.wallet, 'TOPUP', Decimal('10000'), 'idem-5', currency='USD')

    def test_reconcile_detects_manual_drift(self):
        topup_wallet(self.wallet, Decimal('20000'), 'CASH', 'idem-6')
        Wallet.objects.filter(id=self.wallet.id).update(balance=Decimal('999999.00'))
        report = reconcile_wallet_balances(foundation_id=self.fx['foundation'].id)
        self.assertEqual(len(report['mismatches']), 1)
        self.assertEqual(report['mismatches'][0]['wallet_id'], self.wallet.id)

    def test_reconcile_reports_zero_drift_when_consistent(self):
        topup_wallet(self.wallet, Decimal('20000'), 'CASH', 'idem-7')
        report = reconcile_wallet_balances(foundation_id=self.fx['foundation'].id)
        self.assertEqual(report['mismatches'], [])


class SpendRuleTests(TestCase):
    def setUp(self):
        self.fx = build_wallet_fixture()
        self.wallet = get_or_create_wallet(self.fx['student'])
        topup_wallet(self.wallet, Decimal('100000'), 'CASH', 'seed-1')

    def test_no_rule_allows_everything(self):
        result = check_spend_allowed(self.wallet, Decimal('50000'))
        self.assertTrue(result['allowed'])

    def test_blocked_category_rejected(self):
        set_spend_rule(self.fx['student'], blocked_categories=['SUGARY_DRINKS'])
        result = check_spend_allowed(self.wallet, Decimal('5000'), category='SUGARY_DRINKS')
        self.assertFalse(result['allowed'])
        self.assertEqual(result['reason'], 'CATEGORY_BLOCKED')

    def test_blocked_product_rejected(self):
        set_spend_rule(self.fx['student'], blocked_products=['SODA-001'])
        result = check_spend_allowed(self.wallet, Decimal('5000'), product_sku='SODA-001')
        self.assertFalse(result['allowed'])
        self.assertEqual(result['reason'], 'PRODUCT_BLOCKED')

    def test_daily_limit_exceeded(self):
        set_spend_rule(self.fx['student'], daily_limit=Decimal('20000'))
        record_wallet_transaction(self.wallet, 'PURCHASE', Decimal('-15000'), 'purchase-1')
        result = check_spend_allowed(self.wallet, Decimal('6000'))
        self.assertFalse(result['allowed'])
        self.assertEqual(result['reason'], 'DAILY_LIMIT_EXCEEDED')

    def test_daily_limit_within_bounds_allowed(self):
        set_spend_rule(self.fx['student'], daily_limit=Decimal('20000'))
        record_wallet_transaction(self.wallet, 'PURCHASE', Decimal('-15000'), 'purchase-2')
        result = check_spend_allowed(self.wallet, Decimal('5000'))
        self.assertTrue(result['allowed'])

    def test_outside_allowed_window_rejected(self):
        set_spend_rule(
            self.fx['student'],
            allowed_window_start=datetime.time(9, 30), allowed_window_end=datetime.time(10, 0),
        )
        result = check_spend_allowed(self.wallet, Decimal('5000'), at_time=datetime.time(14, 0))
        self.assertFalse(result['allowed'])
        self.assertEqual(result['reason'], 'OUTSIDE_ALLOWED_WINDOW')


class WalletViewsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.fx = build_wallet_fixture()

    def test_topup_and_view_history_via_api(self):
        self.client.force_authenticate(user=self.fx['finance_user'])
        res_topup = self.client.post(f'/api/v1/wallets/{self.fx["student"].id}/topup/', {
            'amount': '75000', 'method': 'CASH', 'idempotency_key': 'api-idem-1',
        }, format='json')
        self.assertEqual(res_topup.status_code, 201, res_topup.content)

        res_wallet = self.client.get(f'/api/v1/wallets/{self.fx["student"].id}/')
        self.assertEqual(res_wallet.status_code, 200)
        self.assertEqual(res_wallet.json()['balance'], '75000.00')

        res_history = self.client.get(f'/api/v1/wallets/{self.fx["student"].id}/transactions/')
        self.assertEqual(res_history.status_code, 200)
        self.assertEqual(len(res_history.json()['results']), 1)

    def test_set_spend_rule_via_api(self):
        self.client.force_authenticate(user=self.fx['finance_user'])
        res = self.client.put(f'/api/v1/wallets/{self.fx["student"].id}/rules/', {
            'daily_limit': '20000', 'blocked_categories': ['SUGARY_DRINKS'],
        }, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['daily_limit'], '20000.00')

    def test_cross_tenant_wallet_returns_404(self):
        fx_b = build_wallet_fixture(foundation_name="Yayasan Wallet B")
        self.client.force_authenticate(user=fx_b['finance_user'])
        res = self.client.get(f'/api/v1/wallets/{self.fx["student"].id}/')
        self.assertEqual(res.status_code, 404)
