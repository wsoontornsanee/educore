import hashlib
from decimal import Decimal
from django.test import TestCase
from rest_framework.test import APIClient

from apps.wallet.models import WalletTopupIntent, WalletTopupIntentStatus
from apps.wallet.services import (
    InvalidWebhookError,
    create_wallet_topup_intent,
    get_or_create_wallet,
    process_wallet_topup_webhook,
)
from apps.wallet.tests.test_wallet_core import build_wallet_fixture


class CreateTopupIntentTests(TestCase):
    def setUp(self):
        self.fx = build_wallet_fixture()
        self.wallet = get_or_create_wallet(self.fx['student'])

    def test_va_intent_reuses_stable_student_va(self):
        intent1 = create_wallet_topup_intent(self.wallet, self.fx['student'], self.fx['school'], 'VA', Decimal('100000'), bank='BCA')
        self.assertEqual(intent1.va_bank, 'BCA')
        self.assertTrue(intent1.va_number)

        intent2 = create_wallet_topup_intent(self.wallet, self.fx['student'], self.fx['school'], 'VA', Decimal('50000'), bank='BCA')
        self.assertEqual(intent1.va_number, intent2.va_number)
        self.assertNotEqual(intent1.external_id, intent2.external_id)

    def test_qris_intent_produces_payload(self):
        intent = create_wallet_topup_intent(self.wallet, self.fx['student'], self.fx['school'], 'QRIS', Decimal('25000'))
        self.assertTrue(intent.qris_payload)
        self.assertEqual(intent.status, WalletTopupIntentStatus.PENDING)

    def test_zero_amount_rejected(self):
        with self.assertRaises(ValueError):
            create_wallet_topup_intent(self.wallet, self.fx['student'], self.fx['school'], 'QRIS', Decimal('0.00'))

    def test_unsupported_method_rejected(self):
        with self.assertRaises(ValueError):
            create_wallet_topup_intent(self.wallet, self.fx['student'], self.fx['school'], 'CASH', Decimal('1000'))


def _signed_payload(order_id, amount, secret='mock-secret'):
    signature = hashlib.sha256(f"{order_id}{amount}{secret}".encode('utf-8')).hexdigest()
    return {'order_id': order_id, 'amount': amount, 'status': 'SETTLED', 'signature': signature, 'channel': 'MOCK_VA'}


class WebhookTests(TestCase):
    def setUp(self):
        self.fx = build_wallet_fixture()
        self.wallet = get_or_create_wallet(self.fx['student'])
        self.intent = create_wallet_topup_intent(self.wallet, self.fx['student'], self.fx['school'], 'QRIS', Decimal('75000'))

    def test_settled_webhook_credits_wallet_once(self):
        payload = _signed_payload(self.intent.external_id, '75000')
        result = process_wallet_topup_webhook('MOCK', payload)
        self.assertEqual(result['status'], 'settled')

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('75000.00'))

        self.intent.refresh_from_db()
        self.assertEqual(self.intent.status, WalletTopupIntentStatus.SETTLED)
        self.assertIsNotNone(self.intent.wallet_transaction_id)

    def test_replay_is_idempotent(self):
        payload = _signed_payload(self.intent.external_id, '75000')
        process_wallet_topup_webhook('MOCK', payload)
        result = process_wallet_topup_webhook('MOCK', payload)
        self.assertEqual(result['status'], 'already_settled')

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('75000.00'))

    def test_invalid_signature_rejected(self):
        payload = {'order_id': self.intent.external_id, 'amount': '75000', 'status': 'SETTLED'}
        with self.assertRaises(InvalidWebhookError):
            process_wallet_topup_webhook('MOCK', payload)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('0.00'))

    def test_unknown_external_id_rejected(self):
        payload = _signed_payload('WALLET-999-nonexistent', '75000')
        with self.assertRaises(InvalidWebhookError):
            process_wallet_topup_webhook('MOCK', payload)

    def test_pending_status_webhook_does_not_credit(self):
        payload = _signed_payload(self.intent.external_id, '75000')
        payload['status'] = 'PENDING'
        result = process_wallet_topup_webhook('MOCK', payload)
        self.assertEqual(result['status'], WalletTopupIntentStatus.PENDING)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('0.00'))


class TopupIntentViewsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.fx = build_wallet_fixture()
        self.wallet = get_or_create_wallet(self.fx['student'])

    def test_create_va_intent_via_api(self):
        self.client.force_authenticate(user=self.fx['finance_user'])
        res = self.client.post(
            f'/api/v1/wallets/{self.fx["student"].id}/topup-intents/',
            {'method': 'VA', 'amount': '100000', 'bank': 'BCA'}, format='json',
        )
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()['va_bank'], 'BCA')

    def test_webhook_settles_and_credits_via_api(self):
        self.client.force_authenticate(user=self.fx['finance_user'])
        res = self.client.post(
            f'/api/v1/wallets/{self.fx["student"].id}/topup-intents/',
            {'method': 'QRIS', 'amount': '50000'}, format='json',
        )
        external_id = res.json()['external_id']

        unauth_client = APIClient()
        payload = _signed_payload(external_id, '50000')
        res2 = unauth_client.post(f'/api/v1/webhooks/wallet-topup/MOCK/', payload, format='json')
        self.assertEqual(res2.status_code, 200, res2.content)
        self.assertEqual(res2.json()['status'], 'settled')

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('50000.00'))

    def test_cross_tenant_intent_creation_returns_404(self):
        fx_b = build_wallet_fixture(foundation_name="Yayasan Topup B")
        self.client.force_authenticate(user=fx_b['finance_user'])
        res = self.client.post(
            f'/api/v1/wallets/{self.fx["student"].id}/topup-intents/',
            {'method': 'QRIS', 'amount': '10000'}, format='json',
        )
        self.assertEqual(res.status_code, 404)
