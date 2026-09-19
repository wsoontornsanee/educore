"""Tests for QR Charge oversight: disputes, merchant flag, underpayment signals (spec 18 §7, QRS-026..028)."""
import datetime
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.identity.models import RoleAssignment, User
from apps.wallet.models import (
    POSEntryMode,
    POSTransaction,
    POSTransactionStatus,
    QRDisputeStatus,
    WalletTransactionType,
)
from apps.wallet.qr_charge import charge_qr_session, create_qr_session
from apps.wallet.qr_oversight import (
    DISPUTE_FLAG_THRESHOLD,
    QRDisputeError,
    clear_merchant_qr_flag,
    get_underpayment_signals,
    open_qr_dispute,
    resolve_qr_dispute,
)
from apps.wallet.services import process_pos_transaction, topup_wallet
from apps.wallet.tests.test_qr_charge import QRFixtureMixin, make_guardian
from apps.wallet.models import WalletTransaction


class DisputeTests(QRFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.guardian = make_guardian(self.fx)
        self.staff = self.fx['finance_user']

    def assertDisputeError(self, code, fn, *args, **kwargs):
        with self.assertRaises(QRDisputeError) as ctx:
            fn(*args, **kwargs)
        self.assertEqual(ctx.exception.code, code)

    def test_open_creates_case_and_reverses_nothing(self):
        pos_tx = self.pay('18000')
        dispute = open_qr_dispute(pos_tx, self.guardian, 'Saya hanya beli minum')
        self.assertEqual(dispute.status, QRDisputeStatus.OPEN)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('82000.00'))
        pos_tx.refresh_from_db()
        self.assertEqual(pos_tx.status, POSTransactionStatus.COMPLETED)

    def test_only_self_entered_completed_charges_can_be_disputed(self):
        operator_tx = process_pos_transaction(
            self.terminal, self.student,
            [{'sku': 'NASI-01', 'name': 'Nasi', 'qty': 1, 'unit_price': '15000.00'}], 'op-1',
        )
        self.assertDisputeError('DISPUTE_NOT_ELIGIBLE', open_qr_dispute, operator_tx, self.guardian, 'x')

    def test_dispute_window_is_seven_days(self):
        pos_tx = self.pay('18000')
        POSTransaction.objects.filter(id=pos_tx.id).update(occurred_at=timezone.now() - datetime.timedelta(days=8))
        pos_tx.refresh_from_db()
        self.assertDisputeError('DISPUTE_WINDOW_CLOSED', open_qr_dispute, pos_tx, self.guardian, 'x')

    def test_one_dispute_per_charge(self):
        pos_tx = self.pay('18000')
        open_qr_dispute(pos_tx, self.guardian, 'a')
        self.assertDisputeError('DISPUTE_EXISTS', open_qr_dispute, pos_tx, self.guardian, 'b')

    def test_upheld_full_refunds_and_voids_sale(self):
        pos_tx = self.pay('18000')
        dispute = resolve_qr_dispute(open_qr_dispute(pos_tx, self.guardian, 'x'), 'UPHELD', self.staff, 'ok')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('100000.00'))
        self.assertEqual(dispute.status, QRDisputeStatus.UPHELD)
        self.assertEqual(dispute.resolution_transaction.type, WalletTransactionType.REFUND)
        pos_tx.refresh_from_db()
        self.assertEqual(pos_tx.status, POSTransactionStatus.VOIDED)  # excluded from merchant settlement

    def test_upheld_partial_is_adjustment_and_sale_stands(self):
        pos_tx = self.pay('18000')
        dispute = resolve_qr_dispute(
            open_qr_dispute(pos_tx, self.guardian, 'x'), 'UPHELD', self.staff, refund_amount=Decimal('6000'),
        )
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('88000.00'))
        self.assertEqual(dispute.resolution_transaction.type, WalletTransactionType.ADJUSTMENT)
        pos_tx.refresh_from_db()
        self.assertEqual(pos_tx.status, POSTransactionStatus.COMPLETED)

    def test_refund_amount_bounds(self):
        pos_tx = self.pay('18000')
        dispute = open_qr_dispute(pos_tx, self.guardian, 'x')
        self.assertDisputeError('DISPUTE_INVALID_AMOUNT', resolve_qr_dispute, dispute, 'UPHELD', self.staff, '', Decimal('18001'))
        self.assertDisputeError('DISPUTE_INVALID_AMOUNT', resolve_qr_dispute, dispute, 'UPHELD', self.staff, '', Decimal('0'))

    def test_rejected_changes_nothing(self):
        pos_tx = self.pay('18000')
        dispute = resolve_qr_dispute(open_qr_dispute(pos_tx, self.guardian, 'x'), 'REJECTED', self.staff, 'sudah benar')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('82000.00'))
        self.assertEqual(dispute.status, QRDisputeStatus.REJECTED)
        self.assertIsNone(dispute.resolution_transaction)

    def test_cannot_resolve_twice_or_double_refund(self):
        pos_tx = self.pay('18000')
        dispute = resolve_qr_dispute(open_qr_dispute(pos_tx, self.guardian, 'x'), 'UPHELD', self.staff)
        self.assertDisputeError('DISPUTE_NOT_OPEN', resolve_qr_dispute, dispute, 'UPHELD', self.staff)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('100000.00'))

    def test_three_upheld_in_window_flag_merchant_and_admin_can_clear(self):
        topup_wallet(self.wallet, Decimal('500000'), 'CASH', 'seed-2')
        for i in range(DISPUTE_FLAG_THRESHOLD):
            pos_tx = self.pay('5000', key=f'k{i}')
            resolve_qr_dispute(open_qr_dispute(pos_tx, self.guardian, 'x'), 'UPHELD', self.staff)
            self.merchant.refresh_from_db()
            self.assertEqual(self.merchant.qr_dispute_flagged_at is not None, i == DISPUTE_FLAG_THRESHOLD - 1)
        clear_merchant_qr_flag(self.merchant, self.staff)
        self.merchant.refresh_from_db()
        self.assertIsNone(self.merchant.qr_dispute_flagged_at)

    def test_rejected_disputes_do_not_count_toward_flag(self):
        topup_wallet(self.wallet, Decimal('500000'), 'CASH', 'seed-2')
        for i in range(DISPUTE_FLAG_THRESHOLD):
            pos_tx = self.pay('5000', key=f'k{i}')
            resolve_qr_dispute(open_qr_dispute(pos_tx, self.guardian, 'x'), 'REJECTED', self.staff)
        self.merchant.refresh_from_db()
        self.assertIsNone(self.merchant.qr_dispute_flagged_at)


class UnderpaymentSignalsTests(QRFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        topup_wallet(self.wallet, Decimal('5000000'), 'CASH', 'seed-big')

    def _seed_baskets(self, n, unit_price='15000.00'):
        yesterday = timezone.now() - datetime.timedelta(days=1)
        for i in range(n):
            process_pos_transaction(
                self.terminal, self.student,
                [{'sku': 'NASI-01', 'name': 'Nasi', 'qty': 1, 'unit_price': unit_price}], f'basket-{i}',
                occurred_at=yesterday,
            )

    def test_insufficient_baseline_yields_no_signals(self):
        self._seed_baskets(3)
        self.pay('1000')
        report = get_underpayment_signals(self.merchant)
        self.assertTrue(report['insufficient_baseline'])
        self.assertEqual(report['students'], [])

    def test_small_qr_charge_flagged_and_grouped_by_student(self):
        self._seed_baskets(10)  # p10 = Rp 15.000
        self.pay('3000', key='a')
        self.pay('4000', key='b')
        self.pay('15000', key='c')  # not below p10
        report = get_underpayment_signals(self.merchant)
        self.assertFalse(report['insufficient_baseline'])
        self.assertEqual(report['p10'], Decimal('15000.00'))
        self.assertEqual(len(report['students']), 1)
        row = report['students'][0]
        self.assertEqual(row['count'], 2)
        self.assertEqual(row['total'], Decimal('7000.00'))

    def test_operator_entered_sales_never_flagged(self):
        self._seed_baskets(10)
        process_pos_transaction(
            self.terminal, self.student, [{'sku': 'X', 'name': 'Kerupuk', 'qty': 1, 'unit_price': '1000.00'}], 'small-op',
        )
        self.assertEqual(get_underpayment_signals(self.merchant)['students'], [])

    def test_other_day_not_included(self):
        self._seed_baskets(10)
        self.pay('3000')
        tomorrow = timezone.localdate() + datetime.timedelta(days=1)
        self.assertEqual(get_underpayment_signals(self.merchant, tomorrow)['students'], [])


class OversightApiTests(QRFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.guardian = make_guardian(self.fx)
        self.admin = User.objects.create(
            foundation_id=self.fx['foundation'].id, phone_e164="+6281100000009", email="adm@school.id", full_name="Admin",
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.fx['foundation'].id, user=self.admin, role='school_admin',
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.fx['school'].id,
        )
        self.pos_tx = self.pay('18000')
        self.wallet_tx_id = self.pos_tx.wallet_transaction_id

    def _dispute_url(self, wid=None):
        return f'/api/v1/wallet/transactions/{wid or self.wallet_tx_id}/dispute/'

    def test_guardian_disputes_from_history_and_staff_resolve(self):
        self.client.force_authenticate(user=self.guardian)
        res = self.client.post(self._dispute_url(), {'reason': 'Salah nominal'}, format='json')
        self.assertEqual(res.status_code, 201, res.content)
        dispute_id = res.json()['id']

        self.client.force_authenticate(user=self.fx['finance_user'])
        res = self.client.get(f'/api/v1/merchants/{self.merchant.id}/qr-disputes/?status=OPEN')
        self.assertEqual([d['id'] for d in res.json()], [dispute_id])

        self.client.force_authenticate(user=self.admin)
        res = self.client.post(f'/api/v1/wallet/qr-disputes/{dispute_id}/resolve/', {'outcome': 'UPHELD'}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['status'], 'UPHELD')
        self.assertEqual(res.json()['refund_amount'], '18000.00')
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('100000.00'))

    def test_guardian_cannot_resolve_and_operator_cannot_dispute(self):
        self.client.force_authenticate(user=self.guardian)
        dispute_id = self.client.post(self._dispute_url(), {'reason': 'x'}, format='json').json()['id']
        res = self.client.post(f'/api/v1/wallet/qr-disputes/{dispute_id}/resolve/', {'outcome': 'UPHELD'}, format='json')
        self.assertEqual(res.status_code, 403)

        self.client.force_authenticate(user=self.fx['finance_user'])
        res = self.client.post(self._dispute_url(), {'reason': 'x'}, format='json')
        self.assertEqual(res.status_code, 404)

    def test_unlinked_guardian_gets_404(self):
        other = make_guardian(self.fx, nik='3471010101015555')
        from apps.identity.models import GuardianLink
        GuardianLink.all_tenants.filter(guardian__user=other).delete()
        self.client.force_authenticate(user=other)
        self.assertEqual(self.client.post(self._dispute_url(), {'reason': 'x'}, format='json').status_code, 404)

    def test_topup_row_is_not_disputable(self):
        topup_id = WalletTransaction.objects.filter(type='TOPUP').first().id
        self.client.force_authenticate(user=self.guardian)
        self.assertEqual(self.client.post(self._dispute_url(topup_id), {'reason': 'x'}, format='json').status_code, 404)

    def test_duplicate_dispute_returns_code(self):
        self.client.force_authenticate(user=self.guardian)
        self.client.post(self._dispute_url(), {'reason': 'x'}, format='json')
        res = self.client.post(self._dispute_url(), {'reason': 'y'}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()['error'], 'DISPUTE_EXISTS')

    def test_signals_endpoint_and_bad_date(self):
        self.client.force_authenticate(user=self.fx['finance_user'])
        res = self.client.get(f'/api/v1/merchants/{self.merchant.id}/underpayment-signals/')
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()['insufficient_baseline'])
        res = self.client.get(f'/api/v1/merchants/{self.merchant.id}/underpayment-signals/?date=nope')
        self.assertEqual(res.status_code, 400)

    def test_flag_clear_endpoint_requires_school_config_write(self):
        self.client.force_authenticate(user=self.fx['finance_user'])
        res = self.client.post(f'/api/v1/merchants/{self.merchant.id}/qr-flag/clear/')
        self.assertEqual(res.status_code, 403)
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(f'/api/v1/merchants/{self.merchant.id}/qr-flag/clear/')
        self.assertEqual(res.status_code, 200)
        self.assertIsNone(res.json()['qr_dispute_flagged_at'])

    def test_cross_tenant_dispute_resolve_is_404(self):
        self.client.force_authenticate(user=self.guardian)
        dispute_id = self.client.post(self._dispute_url(), {'reason': 'x'}, format='json').json()['id']
        from apps.wallet.tests.test_wallet_core import build_wallet_fixture
        from apps.identity.models import RoleAssignment as RA
        other = build_wallet_fixture(foundation_name="Yayasan Asing 2")
        admin = User.objects.create(
            foundation_id=other['foundation'].id, phone_e164="+6281100000077", email="x@asing.id", full_name="Admin Asing",
        )
        RA.all_tenants.create(
            foundation_id=other['foundation'].id, user=admin, role='school_admin',
            scope_type=RA.SCOPE_SCHOOL, scope_id=other['school'].id,
        )
        self.client.force_authenticate(user=admin)
        res = self.client.post(f'/api/v1/wallet/qr-disputes/{dispute_id}/resolve/', {'outcome': 'UPHELD'}, format='json')
        self.assertEqual(res.status_code, 404)
