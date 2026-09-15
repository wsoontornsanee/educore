from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.wallet.models import POSTransactionStatus, WalletTransactionStatus
from apps.wallet.services import (
    check_offline_floor,
    get_or_create_wallet,
    pos_session,
    pos_sync,
    process_offline_pos_batch,
    process_pos_transaction,
    topup_wallet,
)
from apps.wallet.tests.test_wallet_core import build_wallet_fixture
from apps.wallet.tests.test_pos_checkout import build_pos_fixture


def batch_item(sku, name, client_id, qty=1, unit_price='15000.00', student_id=None):
    return {
        'client_transaction_id': client_id,
        'student_id': student_id,
        'items': [{'sku': sku, 'name': name, 'qty': qty, 'unit_price': unit_price}],
    }


class POSSessionAndSyncTests(TestCase):
    def setUp(self):
        self.fx = build_wallet_fixture()
        self.merchant, self.product, self.terminal = build_pos_fixture(self.fx)
        self.wallet = get_or_create_wallet(self.fx['student'])
        topup_wallet(self.wallet, Decimal('50000'), 'CASH', 'seed-1')

    def test_session_returns_full_snapshot(self):
        snapshot = pos_session(self.terminal)
        self.assertEqual(len(snapshot['roster']), 1)
        self.assertEqual(snapshot['roster'][0]['wallet_balance'], '50000.00')
        self.assertEqual(len(snapshot['catalog']), 1)
        self.assertIn('cursor', snapshot)

    def test_sync_with_future_cursor_returns_empty_deltas(self):
        future_cursor = timezone.now() + timezone.timedelta(days=1)
        delta = pos_sync(self.terminal, since_cursor=future_cursor)
        self.assertEqual(delta['roster_delta'], [])
        self.assertEqual(delta['catalog_delta'], [])

    def test_sync_without_cursor_returns_everything(self):
        delta = pos_sync(self.terminal, since_cursor=None)
        self.assertEqual(len(delta['roster_delta']), 1)
        self.assertEqual(len(delta['catalog_delta']), 1)


class OfflineBatchTests(TestCase):
    def setUp(self):
        self.fx = build_wallet_fixture()
        self.merchant, self.product, self.terminal = build_pos_fixture(self.fx)
        self.wallet = get_or_create_wallet(self.fx['student'])
        topup_wallet(self.wallet, Decimal('100000'), 'CASH', 'seed-1')

    def test_batch_processes_multiple_transactions(self):
        batch = [
            batch_item(self.product.sku, self.product.name, 'off-1', student_id=self.fx['student'].id),
            batch_item(self.product.sku, self.product.name, 'off-2', student_id=self.fx['student'].id),
        ]
        report = process_offline_pos_batch(self.terminal, batch)
        self.assertEqual(len(report['results']), 2)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('70000.00'))

    def test_batch_replay_is_idempotent(self):
        batch = [batch_item(self.product.sku, self.product.name, 'off-3', student_id=self.fx['student'].id)]
        process_offline_pos_batch(self.terminal, batch)
        process_offline_pos_batch(self.terminal, batch)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('85000.00'))

    def test_offline_floor_limit_rejects_transaction(self):
        batch = [batch_item(self.product.sku, self.product.name, 'off-4', qty=5, student_id=self.fx['student'].id)]  # 75000 > 50000 floor
        report = process_offline_pos_batch(self.terminal, batch)
        self.assertEqual(report['results'][0]['status'], 'OFFLINE_FLOOR_EXCEEDED')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('100000.00'))

    def test_offline_overspend_accepted_and_flags_reconciliation(self):
        # Deplete most of the balance first via online purchase, leaving little.
        process_pos_transaction(
            self.terminal, self.fx['student'],
            [{'sku': self.product.sku, 'name': self.product.name, 'qty': 6, 'unit_price': '15000.00'}],  # 90000
            'online-drain',
        )
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('10000.00'))

        batch = [batch_item(self.product.sku, self.product.name, 'off-5', qty=1, student_id=self.fx['student'].id)]  # 15000 > 10000
        report = process_offline_pos_batch(self.terminal, batch)
        self.assertEqual(report['results'][0]['status'], WalletTransactionStatus.RECONCILE_REQUIRED)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('-5000.00'))
        self.assertTrue(self.wallet.requires_reconciliation)


class OfflineSyncViewsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.fx = build_wallet_fixture()
        self.merchant, self.product, self.terminal = build_pos_fixture(self.fx)
        self.wallet = get_or_create_wallet(self.fx['student'])
        topup_wallet(self.wallet, Decimal('100000'), 'CASH', 'seed-1')

    def test_session_via_api(self):
        self.client.force_authenticate(user=self.fx['finance_user'])
        res = self.client.post('/api/v1/pos/sessions/', {'terminal_id': self.terminal.id}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(len(res.json()['roster']), 1)

    def test_batch_via_api(self):
        self.client.force_authenticate(user=self.fx['finance_user'])
        res = self.client.post('/api/v1/pos/transactions/batch/', {
            'terminal_id': self.terminal.id,
            'transactions': [batch_item(self.product.sku, self.product.name, 'api-off-1', student_id=self.fx['student'].id)],
        }, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(len(res.json()['results']), 1)

    def test_cross_tenant_session_returns_404(self):
        fx_b = build_wallet_fixture(foundation_name="Yayasan Offline Sync B")
        self.client.force_authenticate(user=fx_b['finance_user'])
        res = self.client.post('/api/v1/pos/sessions/', {'terminal_id': self.terminal.id}, format='json')
        self.assertEqual(res.status_code, 404)
