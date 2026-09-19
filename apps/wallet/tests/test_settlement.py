import datetime
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.wallet.models import MerchantSettlementStatus
from apps.wallet.services import (
    SettlementStateError,
    generate_settlement_statement_pdf,
    get_or_create_wallet,
    mark_settlement_paid,
    process_pos_transaction,
    run_merchant_settlement,
    topup_wallet,
    void_pos_transaction,
)
from apps.wallet.tests.test_wallet_core import build_wallet_fixture
from apps.wallet.tests.test_pos_checkout import build_pos_fixture


class RunSettlementTests(TestCase):
    def setUp(self):
        self.fx = build_wallet_fixture()
        self.merchant, self.product, self.terminal = build_pos_fixture(self.fx)
        self.wallet = get_or_create_wallet(self.fx['student'])
        topup_wallet(self.wallet, Decimal('100000'), 'CASH', 'seed-1')
        self.today = timezone.localdate()
        patcher = mock.patch('apps.core.storage._client')
        self.mock_gcs_client = patcher.start()
        self.addCleanup(patcher.stop)

    def _checkout(self, client_id, qty=1):
        return process_pos_transaction(
            self.terminal, self.fx['student'],
            [{'sku': self.product.sku, 'name': self.product.name, 'qty': qty, 'unit_price': '15000.00'}],
            client_id,
        )

    def test_settlement_computes_gross_commission_net(self):
        self._checkout('tx-1')
        self._checkout('tx-2')
        settlement = run_merchant_settlement(self.merchant, self.today, self.today)
        self.assertEqual(settlement.gross, Decimal('30000.00'))
        self.assertEqual(settlement.commission, Decimal('600.00'))  # 2% of 30000
        self.assertEqual(settlement.net, Decimal('29400.00'))

    def test_voided_transaction_excluded(self):
        tx = self._checkout('tx-3')
        void_pos_transaction(tx, "batal")
        settlement = run_merchant_settlement(self.merchant, self.today, self.today)
        self.assertEqual(settlement.gross, Decimal('0.00'))

    def test_rerun_while_pending_updates_in_place(self):
        self._checkout('tx-4')
        s1 = run_merchant_settlement(self.merchant, self.today, self.today)
        self._checkout('tx-5')
        s2 = run_merchant_settlement(self.merchant, self.today, self.today)
        self.assertEqual(s1.id, s2.id)
        self.assertEqual(s2.gross, Decimal('30000.00'))

    def test_rerun_after_paid_rejected(self):
        self._checkout('tx-6')
        settlement = run_merchant_settlement(self.merchant, self.today, self.today)
        mark_settlement_paid(settlement)
        with self.assertRaises(SettlementStateError):
            run_merchant_settlement(self.merchant, self.today, self.today)

    def test_marking_paid_from_a_stale_copy_keeps_the_first_paid_at_and_audits_once(self):
        from apps.core.models import AuditEvent
        from apps.wallet.models import MerchantSettlement
        self._checkout('tx-6b')
        settlement = run_merchant_settlement(self.merchant, self.today, self.today)
        stale = MerchantSettlement.objects.get(id=settlement.id)  # still says PENDING
        paid_at = mark_settlement_paid(settlement).paid_at
        again = mark_settlement_paid(stale)
        self.assertEqual(again.status, MerchantSettlementStatus.PAID)
        self.assertEqual(again.paid_at, paid_at)
        events = AuditEvent.objects.filter(action='wallet.merchant_settlement.paid', entity_id=str(settlement.id))
        self.assertEqual(events.count(), 1)

    def test_generate_statement_produces_pdf_key(self):
        self._checkout('tx-7')
        settlement = run_merchant_settlement(self.merchant, self.today, self.today)
        key = generate_settlement_statement_pdf(settlement)
        self.assertTrue(key)
        settlement.refresh_from_db()
        self.assertEqual(settlement.statement_pdf_key, key)

    def test_re_generating_statement_overwrites_in_place(self):
        from apps.core.models import StoredFile

        self._checkout('tx-8')
        settlement = run_merchant_settlement(self.merchant, self.today, self.today)

        first_key = generate_settlement_statement_pdf(settlement)
        second_key = generate_settlement_statement_pdf(settlement)

        self.assertEqual(first_key, second_key, "re-generating the same settlement must reuse the same key")
        self.assertEqual(StoredFile.all_tenants.filter(key=first_key).count(), 1)


class SettlementViewsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.fx = build_wallet_fixture()
        self.merchant, self.product, self.terminal = build_pos_fixture(self.fx)
        self.wallet = get_or_create_wallet(self.fx['student'])
        topup_wallet(self.wallet, Decimal('100000'), 'CASH', 'seed-1')
        self.today = timezone.localdate()
        patcher = mock.patch('apps.core.storage._client')
        self.mock_gcs_client = patcher.start()
        self.addCleanup(patcher.stop)
        process_pos_transaction(
            self.terminal, self.fx['student'],
            [{'sku': self.product.sku, 'name': self.product.name, 'qty': 1, 'unit_price': '15000.00'}],
            'api-tx-1',
        )

    def test_run_settlement_via_api(self):
        self.client.force_authenticate(user=self.fx['finance_user'])
        res = self.client.post(f'/api/v1/merchants/{self.merchant.id}/settlements/run/', {
            'period_start': self.today.isoformat(), 'period_end': self.today.isoformat(),
        }, format='json')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()['gross'], '15000.00')

    def test_sales_endpoint_via_api(self):
        self.client.force_authenticate(user=self.fx['finance_user'])
        res = self.client.get(f'/api/v1/merchants/{self.merchant.id}/sales/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.json()), 1)

    def test_cross_tenant_merchant_returns_404(self):
        fx_b = build_wallet_fixture(foundation_name="Yayasan Settlement B")
        self.client.force_authenticate(user=fx_b['finance_user'])
        res = self.client.get(f'/api/v1/merchants/{self.merchant.id}/sales/')
        self.assertEqual(res.status_code, 404)

    def test_download_statement_returns_signed_url(self):
        self.mock_gcs_client.return_value.bucket.return_value.blob.return_value.generate_signed_url.return_value = (
            'https://signed.example/statement.pdf'
        )
        self.client.force_authenticate(user=self.fx['finance_user'])
        res_run = self.client.post(f'/api/v1/merchants/{self.merchant.id}/settlements/run/', {
            'period_start': self.today.isoformat(), 'period_end': self.today.isoformat(),
        }, format='json')
        settlement_id = res_run.json()['id']

        res = self.client.get(f'/api/v1/settlements/{settlement_id}/download/')

        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['download_url'], 'https://signed.example/statement.pdf')
        self.assertIn('expires_at', res.json())

    def test_download_statement_cross_tenant_returns_404(self):
        self.client.force_authenticate(user=self.fx['finance_user'])
        res_run = self.client.post(f'/api/v1/merchants/{self.merchant.id}/settlements/run/', {
            'period_start': self.today.isoformat(), 'period_end': self.today.isoformat(),
        }, format='json')
        settlement_id = res_run.json()['id']

        fx_b = build_wallet_fixture(foundation_name="Yayasan Settlement Download B")
        self.client.force_authenticate(user=fx_b['finance_user'])
        res = self.client.get(f'/api/v1/settlements/{settlement_id}/download/')
        self.assertEqual(res.status_code, 404)
