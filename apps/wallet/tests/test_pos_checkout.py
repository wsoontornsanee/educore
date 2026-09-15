import datetime
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.identity.models import RoleAssignment
from apps.wallet.models import Merchant, MerchantType, POSTerminal, POSTransactionStatus, Product
from apps.wallet.services import (
    InsufficientBalanceError,
    SpendNotAllowedError,
    VoidWindowExpiredError,
    get_or_create_wallet,
    process_pos_transaction,
    set_spend_rule,
    topup_wallet,
    void_pos_transaction,
)
from apps.wallet.tests.test_wallet_core import build_wallet_fixture


def build_pos_fixture(fx):
    merchant = Merchant.objects.create(
        foundation_id=fx['foundation'].id, school=fx['school'], name="Kantin Sehat",
        type=MerchantType.CANTEEN, commission_bps=200,  # 2%
    )
    product = Product.objects.create(
        foundation_id=fx['foundation'].id, merchant=merchant, sku="NASI-01", name="Nasi Goreng",
        price=Decimal('15000.00'), category="FOOD",
    )
    terminal = POSTerminal.objects.create(
        foundation_id=fx['foundation'].id, merchant=merchant, device_id="TERM-001", name="Kasir 1",
    )
    return merchant, product, terminal


class ProcessPOSTransactionTests(TestCase):
    def setUp(self):
        self.fx = build_wallet_fixture()
        self.merchant, self.product, self.terminal = build_pos_fixture(self.fx)
        self.wallet = get_or_create_wallet(self.fx['student'])
        topup_wallet(self.wallet, Decimal('100000'), 'CASH', 'seed-topup')

    def _items(self, qty=1):
        return [{'sku': self.product.sku, 'name': self.product.name, 'qty': qty, 'unit_price': '15000.00', 'category': 'FOOD'}]

    def test_successful_checkout_debits_wallet_and_stores_commission(self):
        pos_tx = process_pos_transaction(self.terminal, self.fx['student'], self._items(), 'client-tx-1')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('85000.00'))
        self.assertEqual(pos_tx.total, Decimal('15000.00'))
        self.assertEqual(pos_tx.commission, Decimal('300.00'))  # 2% of 15000
        self.assertEqual(pos_tx.status, POSTransactionStatus.COMPLETED)

    def test_blocked_category_rejected(self):
        set_spend_rule(self.fx['student'], blocked_categories=['FOOD'])
        with self.assertRaises(SpendNotAllowedError):
            process_pos_transaction(self.terminal, self.fx['student'], self._items(), 'client-tx-2')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('100000.00'))

    def test_daily_limit_rejected(self):
        set_spend_rule(self.fx['student'], daily_limit=Decimal('10000'))
        with self.assertRaises(SpendNotAllowedError):
            process_pos_transaction(self.terminal, self.fx['student'], self._items(), 'client-tx-3')

    def test_insufficient_balance_rejected_and_logged(self):
        with self.assertRaises(InsufficientBalanceError):
            process_pos_transaction(self.terminal, self.fx['student'], self._items(qty=10), 'client-tx-4')
        from apps.wallet.models import POSTransaction
        logged = POSTransaction.all_tenants.get(client_transaction_id='client-tx-4')
        self.assertEqual(logged.status, POSTransactionStatus.REJECTED)

    def test_idempotent_replay_returns_same_transaction(self):
        tx1 = process_pos_transaction(self.terminal, self.fx['student'], self._items(), 'client-tx-5')
        tx2 = process_pos_transaction(self.terminal, self.fx['student'], self._items(), 'client-tx-5')
        self.assertEqual(tx1.id, tx2.id)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('85000.00'))


class VoidPOSTransactionTests(TestCase):
    def setUp(self):
        self.fx = build_wallet_fixture()
        self.merchant, self.product, self.terminal = build_pos_fixture(self.fx)
        self.wallet = get_or_create_wallet(self.fx['student'])
        topup_wallet(self.wallet, Decimal('100000'), 'CASH', 'seed-topup')
        self.pos_tx = process_pos_transaction(
            self.terminal, self.fx['student'],
            [{'sku': self.product.sku, 'name': self.product.name, 'qty': 1, 'unit_price': '15000.00'}],
            'client-tx-void-1',
        )

    def test_void_within_window_restores_balance(self):
        voided = void_pos_transaction(self.pos_tx, "Salah pesan")
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('100000.00'))
        self.assertEqual(voided.status, POSTransactionStatus.VOIDED)

    def test_void_outside_window_rejected(self):
        self.pos_tx.occurred_at = timezone.now() - datetime.timedelta(minutes=30)
        self.pos_tx.save()
        with self.assertRaises(VoidWindowExpiredError):
            void_pos_transaction(self.pos_tx, "Terlambat")


class POSViewsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.fx = build_wallet_fixture()
        self.merchant, self.product, self.terminal = build_pos_fixture(self.fx)
        self.wallet = get_or_create_wallet(self.fx['student'])
        topup_wallet(self.wallet, Decimal('100000'), 'CASH', 'seed-topup')

    def test_checkout_via_api(self):
        self.client.force_authenticate(user=self.fx['finance_user'])
        res = self.client.post('/api/v1/pos/transactions/', {
            'terminal_id': self.terminal.id,
            'student_id': self.fx['student'].id,
            'items': [{'sku': self.product.sku, 'name': self.product.name, 'qty': 1, 'unit_price': '15000.00'}],
            'client_transaction_id': 'api-client-tx-1',
        }, format='json')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()['total'], '15000.00')

    def test_void_via_api(self):
        self.client.force_authenticate(user=self.fx['finance_user'])
        res_create = self.client.post('/api/v1/pos/transactions/', {
            'terminal_id': self.terminal.id,
            'student_id': self.fx['student'].id,
            'items': [{'sku': self.product.sku, 'name': self.product.name, 'qty': 1, 'unit_price': '15000.00'}],
            'client_transaction_id': 'api-client-tx-2',
        }, format='json')
        tx_id = res_create.json()['id']

        res_void = self.client.post(f'/api/v1/pos/transactions/{tx_id}/void/', {'reason': 'test'}, format='json')
        self.assertEqual(res_void.status_code, 200, res_void.content)
        self.assertEqual(res_void.json()['status'], POSTransactionStatus.VOIDED)

    def test_cross_tenant_terminal_returns_404(self):
        fx_b = build_wallet_fixture(foundation_name="Yayasan Wallet POS B")
        self.client.force_authenticate(user=fx_b['finance_user'])
        res = self.client.post('/api/v1/pos/transactions/', {
            'terminal_id': self.terminal.id,
            'student_id': self.fx['student'].id,
            'items': [{'sku': self.product.sku, 'name': self.product.name, 'qty': 1, 'unit_price': '15000.00'}],
            'client_transaction_id': 'api-client-tx-3',
        }, format='json')
        self.assertEqual(res.status_code, 404)
