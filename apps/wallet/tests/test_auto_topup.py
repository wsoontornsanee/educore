"""Tests for wallet auto-top-up (spec/07 §3, WAL-005, WAL-006)."""
from decimal import Decimal
from django.test import TestCase
from rest_framework.test import APIClient

from apps.identity.models import Guardian, GuardianLink, Person, User
from apps.wallet.models import (
    WalletAutoTopupConfig,
    WalletTopupIntent,
    WalletTopupIntentStatus,
    WalletTopupMethod,
)
from apps.wallet.services import (
    WalletAutoTopupError,
    get_or_create_wallet,
    process_wallet_auto_topups,
    set_wallet_auto_topup_config,
    topup_wallet,
)
from apps.wallet.tests.test_wallet_core import build_wallet_fixture


def attach_financial_guardian(fx, nik='3471010101019999'):
    person = Person.all_tenants.create(foundation_id=fx['foundation'].id, nik=nik, full_name="Bu Guardian")
    user = User.objects.create(
        foundation_id=fx['foundation'].id, phone_e164=f"+62819{nik[-7:]}", email=f"{nik}@parent.id", full_name="Bu Guardian",
    )
    guardian = Guardian.all_tenants.create(foundation_id=fx['foundation'].id, person=person, user=user)
    GuardianLink.all_tenants.create(
        foundation_id=fx['foundation'].id, guardian=guardian, student=fx['student'],
        relation=GuardianLink.RELATION_MOTHER, financial_responsible=True,
    )
    return guardian


class SetWalletAutoTopupConfigTests(TestCase):
    def setUp(self):
        self.fx = build_wallet_fixture()
        self.wallet = get_or_create_wallet(self.fx['student'])

    def test_enable_creates_config(self):
        config = set_wallet_auto_topup_config(
            self.wallet, is_active=True, threshold_amount=Decimal('20000'), topup_amount=Decimal('100000'), method='VA', bank='BCA',
        )
        self.assertTrue(config.is_active)
        self.assertEqual(config.threshold_amount, Decimal('20000.00'))
        self.assertEqual(config.topup_amount, Decimal('100000.00'))
        self.assertEqual(config.bank, 'BCA')

    def test_disable_is_the_cancel_path(self):
        set_wallet_auto_topup_config(self.wallet, is_active=True, threshold_amount=Decimal('20000'), topup_amount=Decimal('100000'))
        config = set_wallet_auto_topup_config(self.wallet, is_active=False, threshold_amount=Decimal('20000'), topup_amount=Decimal('100000'))
        self.assertFalse(config.is_active)

    def test_only_one_config_per_wallet(self):
        set_wallet_auto_topup_config(self.wallet, is_active=True, threshold_amount=Decimal('20000'), topup_amount=Decimal('100000'))
        set_wallet_auto_topup_config(self.wallet, is_active=True, threshold_amount=Decimal('30000'), topup_amount=Decimal('150000'))
        self.assertEqual(WalletAutoTopupConfig.all_tenants.filter(wallet=self.wallet).count(), 1)

    def test_negative_threshold_rejected(self):
        with self.assertRaises(WalletAutoTopupError):
            set_wallet_auto_topup_config(self.wallet, is_active=True, threshold_amount=Decimal('-1'), topup_amount=Decimal('100000'))

    def test_zero_topup_amount_rejected(self):
        with self.assertRaises(WalletAutoTopupError):
            set_wallet_auto_topup_config(self.wallet, is_active=True, threshold_amount=Decimal('20000'), topup_amount=Decimal('0'))

    def test_unsupported_method_rejected(self):
        with self.assertRaises(WalletAutoTopupError):
            set_wallet_auto_topup_config(self.wallet, is_active=True, threshold_amount=Decimal('20000'), topup_amount=Decimal('100000'), method='CASH')


class ProcessWalletAutoTopupsTests(TestCase):
    def setUp(self):
        self.fx = build_wallet_fixture()
        self.wallet = get_or_create_wallet(self.fx['student'])
        self.guardian = attach_financial_guardian(self.fx)

    def test_triggers_when_below_threshold(self):
        topup_wallet(self.wallet, Decimal('10000'), 'CASH', 'idem-below')
        set_wallet_auto_topup_config(self.wallet, is_active=True, threshold_amount=Decimal('20000'), topup_amount=Decimal('100000'))

        result = process_wallet_auto_topups()
        self.assertEqual(result['triggered'], 1)
        intent = WalletTopupIntent.all_tenants.get(wallet=self.wallet)
        self.assertEqual(intent.amount, Decimal('100000.00'))
        self.assertEqual(intent.method, WalletTopupMethod.VA)

    def test_no_trigger_when_above_threshold(self):
        topup_wallet(self.wallet, Decimal('50000'), 'CASH', 'idem-above')
        set_wallet_auto_topup_config(self.wallet, is_active=True, threshold_amount=Decimal('20000'), topup_amount=Decimal('100000'))

        result = process_wallet_auto_topups()
        self.assertEqual(result['triggered'], 0)
        self.assertEqual(WalletTopupIntent.all_tenants.filter(wallet=self.wallet).count(), 0)

    def test_no_trigger_when_inactive(self):
        topup_wallet(self.wallet, Decimal('10000'), 'CASH', 'idem-inactive')
        set_wallet_auto_topup_config(self.wallet, is_active=False, threshold_amount=Decimal('20000'), topup_amount=Decimal('100000'))

        result = process_wallet_auto_topups()
        self.assertEqual(result['triggered'], 0)

    def test_skips_when_pending_intent_already_exists(self):
        topup_wallet(self.wallet, Decimal('10000'), 'CASH', 'idem-pending')
        set_wallet_auto_topup_config(self.wallet, is_active=True, threshold_amount=Decimal('20000'), topup_amount=Decimal('100000'))

        first = process_wallet_auto_topups()
        self.assertEqual(first['triggered'], 1)

        second = process_wallet_auto_topups()
        self.assertEqual(second['triggered'], 0)
        self.assertEqual(second['skipped_pending'], 1)
        self.assertEqual(WalletTopupIntent.all_tenants.filter(wallet=self.wallet).count(), 1)


class WalletAutoTopupConfigViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.fx = build_wallet_fixture()

    def test_get_default_when_unset(self):
        self.client.force_authenticate(user=self.fx['finance_user'])
        res = self.client.get(f'/api/v1/wallets/{self.fx["student"].id}/auto-topup-config/')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertFalse(res.json()['is_active'])

    def test_put_then_get_via_api(self):
        self.client.force_authenticate(user=self.fx['finance_user'])
        res = self.client.put(
            f'/api/v1/wallets/{self.fx["student"].id}/auto-topup-config/',
            {'is_active': True, 'threshold_amount': '20000', 'topup_amount': '100000', 'method': 'VA', 'bank': 'BCA'},
            format='json',
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(res.json()['is_active'])

        res2 = self.client.get(f'/api/v1/wallets/{self.fx["student"].id}/auto-topup-config/')
        self.assertEqual(res2.json()['threshold_amount'], '20000.00')

    def test_invalid_config_returns_400(self):
        self.client.force_authenticate(user=self.fx['finance_user'])
        res = self.client.put(
            f'/api/v1/wallets/{self.fx["student"].id}/auto-topup-config/',
            {'is_active': True, 'threshold_amount': '20000', 'topup_amount': '0'},
            format='json',
        )
        self.assertEqual(res.status_code, 400)
