from decimal import Decimal
from django.test import TestCase
from rest_framework.test import APIClient

from apps.identity.models import Student
from apps.wallet.models import WalletRefundRequest, WalletRefundStatus, WalletStatus
from apps.wallet.services import (
    get_or_create_wallet,
    get_refund_queue,
    mark_refund_donated,
    mark_refund_paid,
    queue_wallet_refund_on_exit,
    topup_wallet,
)
from apps.wallet.tests.test_wallet_core import build_wallet_fixture


class QueueOnExitTests(TestCase):
    def setUp(self):
        self.fx = build_wallet_fixture()
        self.wallet = get_or_create_wallet(self.fx['student'])

    def test_positive_balance_queues_refund_on_graduation(self):
        topup_wallet(self.wallet, Decimal('75000'), 'CASH', 'seed-1')
        request = queue_wallet_refund_on_exit(self.fx['student'], Student.STATUS_GRADUATED)
        self.assertIsNotNone(request)
        self.assertEqual(request.amount, Decimal('75000.00'))
        self.assertEqual(request.status, WalletRefundStatus.PENDING)

    def test_zero_balance_queues_nothing(self):
        request = queue_wallet_refund_on_exit(self.fx['student'], Student.STATUS_GRADUATED)
        self.assertIsNone(request)

    def test_non_exit_status_queues_nothing(self):
        topup_wallet(self.wallet, Decimal('50000'), 'CASH', 'seed-2')
        request = queue_wallet_refund_on_exit(self.fx['student'], Student.STATUS_INACTIVE)
        self.assertIsNone(request)

    def test_repeated_exit_does_not_duplicate_pending_request(self):
        topup_wallet(self.wallet, Decimal('50000'), 'CASH', 'seed-3')
        r1 = queue_wallet_refund_on_exit(self.fx['student'], Student.STATUS_GRADUATED)
        r2 = queue_wallet_refund_on_exit(self.fx['student'], Student.STATUS_GRADUATED)
        self.assertEqual(r1.id, r2.id)
        self.assertEqual(WalletRefundRequest.all_tenants.filter(wallet=self.wallet).count(), 1)


class TransitionStatusIntegrationTests(TestCase):
    def setUp(self):
        self.fx = build_wallet_fixture()
        self.wallet = get_or_create_wallet(self.fx['student'])
        topup_wallet(self.wallet, Decimal('60000'), 'CASH', 'seed-4')

    def test_transition_to_graduated_queues_refund_and_freezes(self):
        self.fx['student'].transition_status(Student.STATUS_GRADUATED)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.status, WalletStatus.FROZEN)

        request = WalletRefundRequest.all_tenants.get(wallet=self.wallet)
        self.assertEqual(request.status, WalletRefundStatus.PENDING)
        self.assertEqual(request.amount, Decimal('60000.00'))


class ResolveRefundTests(TestCase):
    def setUp(self):
        self.fx = build_wallet_fixture()
        self.wallet = get_or_create_wallet(self.fx['student'])
        topup_wallet(self.wallet, Decimal('60000'), 'CASH', 'seed-5')
        self.fx['student'].transition_status(Student.STATUS_GRADUATED)
        self.request = WalletRefundRequest.all_tenants.get(wallet=self.wallet)

    def test_mark_paid_zeroes_balance_and_closes_even_though_frozen(self):
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.status, WalletStatus.FROZEN)

        mark_refund_paid(self.request, 'BCA', '1234567890', 'Bu Rahmawati', 'Transfer', actor=None)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('0.00'))
        self.assertEqual(self.wallet.status, WalletStatus.CLOSED)

        self.request.refresh_from_db()
        self.assertEqual(self.request.status, WalletRefundStatus.PAID)
        self.assertEqual(self.request.guardian_bank_name, 'BCA')

    def test_mark_donated_requires_explicit_consent(self):
        with self.assertRaises(ValueError):
            mark_refund_donated(self.request, None, False)

        self.request.refresh_from_db()
        self.assertEqual(self.request.status, WalletRefundStatus.PENDING)

    def test_mark_donated_with_consent_closes_wallet(self):
        mark_refund_donated(self.request, None, True)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('0.00'))
        self.assertEqual(self.wallet.status, WalletStatus.CLOSED)

        self.request.refresh_from_db()
        self.assertEqual(self.request.status, WalletRefundStatus.DONATED)
        self.assertTrue(self.request.donation_consent)

    def test_pending_request_never_auto_resolved(self):
        # Nothing in this codebase touches a PENDING request without an explicit action.
        rows = get_refund_queue(self.fx['school'])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['status'], WalletRefundStatus.PENDING)


class RefundViewsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.fx = build_wallet_fixture()
        self.wallet = get_or_create_wallet(self.fx['student'])
        topup_wallet(self.wallet, Decimal('60000'), 'CASH', 'seed-6')
        self.fx['student'].transition_status(Student.STATUS_GRADUATED)
        self.request = WalletRefundRequest.all_tenants.get(wallet=self.wallet)

    def test_queue_via_api(self):
        self.client.force_authenticate(user=self.fx['finance_user'])
        res = self.client.get(f'/api/v1/wallet-refunds/?school_id={self.fx["school"].id}')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(len(res.json()['requests']), 1)

    def test_mark_paid_via_api(self):
        self.client.force_authenticate(user=self.fx['finance_user'])
        res = self.client.post(f'/api/v1/wallet-refunds/{self.request.id}/mark-paid/', {
            'bank_name': 'BCA', 'account_number': '1234567890', 'account_holder_name': 'Bu Rahmawati',
        }, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['status'], WalletRefundStatus.PAID)

    def test_mark_donated_without_consent_rejected_via_api(self):
        self.client.force_authenticate(user=self.fx['finance_user'])
        res = self.client.post(f'/api/v1/wallet-refunds/{self.request.id}/mark-donated/', {
            'donation_consent': False,
        }, format='json')
        self.assertEqual(res.status_code, 400)

    def test_cross_tenant_refund_returns_404(self):
        fx_b = build_wallet_fixture(foundation_name="Yayasan Refund B")
        self.client.force_authenticate(user=fx_b['finance_user'])
        res = self.client.post(f'/api/v1/wallet-refunds/{self.request.id}/mark-paid/', {}, format='json')
        self.assertEqual(res.status_code, 404)
