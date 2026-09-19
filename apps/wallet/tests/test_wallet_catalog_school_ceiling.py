"""School ceiling on the wallet catalog JSON API (merchants, products, POS terminals,
POS transactions, settlements): a school-scoped admin or officer must not read or
change another school's rows in the same foundation (tenant isolation already held)."""
import datetime
from decimal import Decimal

from rest_framework.test import APIClient, APITestCase

from apps.identity.models import RoleAssignment, School
from apps.wallet.models import Merchant, MerchantType, Product, POSTerminal
from apps.wallet.services import get_or_create_wallet, process_pos_transaction, run_merchant_settlement, topup_wallet
from apps.wallet.tests.test_pos_checkout import build_pos_fixture
from apps.wallet.tests.test_wallet_api_school_ceiling import _user
from apps.wallet.tests.test_wallet_core import build_wallet_fixture


class WalletCatalogSchoolCeilingTests(APITestCase):
    def setUp(self):
        self.fx = build_wallet_fixture()
        self.foundation = self.fx['foundation']
        self.school_a = self.fx['school']
        self.school_b = School.all_tenants.create(
            foundation_id=self.foundation.id, name='SD B', npsn='39999992', level=School.LEVEL_SD,
        )
        self.merchant_a, self.product_a, self.terminal_a = build_pos_fixture(self.fx)
        self.merchant_b = Merchant.objects.create(
            foundation_id=self.foundation.id, school=self.school_b, name='Kantin B', type=MerchantType.CANTEEN,
        )
        self.finance_a = self.fx['finance_user']
        self.admin_a = _user(self.foundation, '+6281600000001', 'Admin A', 'school_admin', RoleAssignment.SCOPE_SCHOOL, self.school_a.id)
        self.admin_b = _user(self.foundation, '+6281600000002', 'Admin B', 'school_admin', RoleAssignment.SCOPE_SCHOOL, self.school_b.id)
        self.finance_b = _user(self.foundation, '+6281600000003', 'Bendahara B', 'finance_officer', RoleAssignment.SCOPE_SCHOOL, self.school_b.id)
        self.chair = _user(self.foundation, '+6281600000004', 'Ketua', 'foundation_admin', RoleAssignment.SCOPE_FOUNDATION, self.foundation.id)
        # A completed sale and a settlement at school A.
        wallet = get_or_create_wallet(self.fx['student'])
        topup_wallet(wallet, Decimal('100000'), 'CASH', 'seed-catalog')
        items = [{'sku': 'NASI-01', 'name': 'Nasi Goreng', 'qty': 1, 'unit_price': '15000.00', 'category': 'FOOD'}]
        self.tx_a = process_pos_transaction(self.terminal_a, self.fx['student'], items, 'catalog-tx-1')
        today = datetime.date.today()
        self.settlement_a = run_merchant_settlement(self.merchant_a, today, today)

    def as_user(self, user):
        self.client = APIClient()
        self.client.force_authenticate(user=user)

    # --- merchants ---------------------------------------------------------
    def test_school_admin_lists_only_own_schools_merchants(self):
        self.as_user(self.admin_b)
        names = {row['name'] for row in self.client.get('/api/v1/merchants/').data['results']}
        self.assertEqual(names, {'Kantin B'})

    def test_school_admin_cannot_read_change_or_delete_another_schools_merchant(self):
        self.as_user(self.admin_b)
        url = f'/api/v1/merchants/{self.merchant_a.id}/'
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.patch(url, {'name': 'Hacked'}, format='json').status_code, 404)
        self.assertEqual(self.client.delete(url).status_code, 404)
        self.merchant_a.refresh_from_db()
        self.assertEqual(self.merchant_a.name, 'Kantin Sehat')
        self.assertIsNone(self.merchant_a.deleted_at)

    def test_school_admin_cannot_create_a_merchant_in_another_school(self):
        self.as_user(self.admin_b)
        body = {'school': self.school_a.id, 'name': 'Palsu', 'type': MerchantType.CANTEEN}
        self.assertEqual(self.client.post('/api/v1/merchants/', body, format='json').status_code, 404)
        self.assertFalse(Merchant.objects.filter(name='Palsu').exists())
        body['school'] = self.school_b.id
        self.assertEqual(self.client.post('/api/v1/merchants/', body, format='json').status_code, 201)

    def test_school_admin_cannot_move_a_merchant_into_another_school(self):
        self.as_user(self.admin_b)
        url = f'/api/v1/merchants/{self.merchant_b.id}/'
        self.assertEqual(self.client.patch(url, {'school': self.school_a.id}, format='json').status_code, 404)
        self.merchant_b.refresh_from_db()
        self.assertEqual(self.merchant_b.school_id, self.school_b.id)

    def test_own_school_and_foundation_admin_keep_working(self):
        self.as_user(self.admin_a)
        self.assertEqual(self.client.patch(f'/api/v1/merchants/{self.merchant_a.id}/', {'name': 'Kantin A2'}, format='json').status_code, 200)
        self.as_user(self.chair)
        names = {row['name'] for row in self.client.get('/api/v1/merchants/').data['results']}
        self.assertEqual(names, {'Kantin A2', 'Kantin B'})

    # --- products / terminals ---------------------------------------------
    def test_products_and_terminals_are_school_scoped(self):
        self.as_user(self.admin_b)
        self.assertEqual(self.client.get('/api/v1/products/').data['results'], [])
        self.assertEqual(self.client.get(f'/api/v1/products/{self.product_a.id}/').status_code, 404)
        self.assertEqual(self.client.get(f'/api/v1/pos/terminals/{self.terminal_a.id}/').status_code, 404)

    def test_cannot_create_a_product_or_terminal_under_another_schools_merchant(self):
        self.as_user(self.admin_b)
        product = {'merchant': self.merchant_a.id, 'sku': 'X-1', 'name': 'X', 'price': '1000.00', 'category': 'FOOD'}
        self.assertEqual(self.client.post('/api/v1/products/', product, format='json').status_code, 404)
        terminal = {'merchant': self.merchant_a.id, 'device_id': 'TERM-X', 'name': 'X'}
        self.assertEqual(self.client.post('/api/v1/pos/terminals/', terminal, format='json').status_code, 404)
        self.assertFalse(Product.objects.filter(sku='X-1').exists())
        self.assertFalse(POSTerminal.objects.filter(device_id='TERM-X').exists())
        product['merchant'] = self.merchant_b.id
        self.assertEqual(self.client.post('/api/v1/products/', product, format='json').status_code, 201)

    # --- merchant actions --------------------------------------------------
    def test_merchant_sales_settlements_and_run_are_school_scoped(self):
        self.as_user(self.finance_b)
        base = f'/api/v1/merchants/{self.merchant_a.id}'
        self.assertEqual(self.client.get(f'{base}/sales/').status_code, 404)
        self.assertEqual(self.client.get(f'{base}/settlements/').status_code, 404)
        body = {'period_start': '2026-01-01', 'period_end': '2026-01-31'}
        self.assertEqual(self.client.post(f'{base}/settlements/run/', body, format='json').status_code, 404)

    def test_finance_officer_reads_own_merchant_sales(self):
        self.as_user(self.finance_a)
        self.assertEqual(self.client.get(f'/api/v1/merchants/{self.merchant_a.id}/sales/').status_code, 200)

    def test_settlement_download_is_school_scoped(self):
        from unittest import mock

        type(self.settlement_a).objects.filter(id=self.settlement_a.id).update(statement_pdf_key='statements/a.pdf')
        url = f'/api/v1/settlements/{self.settlement_a.id}/download/'
        with mock.patch('apps.wallet.views.build_signed_download', return_value={'url': 'signed'}):
            self.as_user(self.finance_b)
            self.assertEqual(self.client.get(url).status_code, 404)
            self.as_user(self.finance_a)
            self.assertEqual(self.client.get(url).status_code, 200)

    # --- POS transactions / terminals -------------------------------------
    def test_pos_transactions_are_school_scoped(self):
        self.as_user(self.finance_b)
        self.assertEqual(self.client.get('/api/v1/pos/transactions/').data['results'], [])
        self.assertEqual(self.client.get(f'/api/v1/pos/transactions/{self.tx_a.id}/').status_code, 404)
        self.assertEqual(self.client.get(f'/api/v1/pos/transactions/{self.tx_a.id}/receipt/').status_code, 404)
        self.assertEqual(self.client.post(f'/api/v1/pos/transactions/{self.tx_a.id}/void/', {'reason': 'x'}, format='json').status_code, 404)

    def test_cannot_charge_or_sync_through_another_schools_terminal(self):
        self.as_user(self.finance_b)
        charge = {
            'terminal_id': self.terminal_a.id, 'student_id': self.fx['student'].id, 'client_transaction_id': 'x-1',
            'items': [{'sku': 'NASI-01', 'name': 'Nasi Goreng', 'qty': 1, 'unit_price': '15000.00', 'category': 'FOOD'}],
        }
        self.assertEqual(self.client.post('/api/v1/pos/transactions/', charge, format='json').status_code, 404)
        self.assertEqual(self.client.post('/api/v1/pos/transactions/batch/', {'terminal_id': self.terminal_a.id, 'transactions': []}, format='json').status_code, 404)
        self.assertEqual(self.client.post('/api/v1/pos/sessions/', {'terminal_id': self.terminal_a.id}, format='json').status_code, 404)
        self.assertEqual(self.client.get('/api/v1/pos/sync/', {'terminal_id': self.terminal_a.id}).status_code, 404)

    def test_own_school_officer_keeps_pos_access(self):
        self.as_user(self.finance_a)
        self.assertEqual(len(self.client.get('/api/v1/pos/transactions/').data['results']), 1)
        self.assertEqual(self.client.post('/api/v1/pos/sessions/', {'terminal_id': self.terminal_a.id}, format='json').status_code, 200)
