"""Kantin & dompet finance queues: wallet refunds and reconciliation cases (web console)."""
import datetime
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.models import AuditEvent
from apps.identity.models import Foundation, Person, RoleAssignment, School, Staff, Student, User
from apps.wallet.models import (
    WalletReconciliation, WalletReconciliationStatus, WalletRefundRequest, WalletRefundStatus, WalletStatus,
)
from apps.wallet.services import get_or_create_wallet, topup_wallet
from apps.wallet.tests.test_pos_checkout import build_pos_fixture
from apps.wallet.tests.test_reconciliation import attach_financial_guardian
from apps.wallet.tests.test_reconciliation_followup import make_open_case
from apps.wallet.tests.test_wallet_core import build_wallet_fixture
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id


def _staff_user(fx, school, phone, name, role, nik):
    user = User.all_tenants.create_user(foundation_id=fx['foundation'].id, phone_e164=phone, full_name=name)
    RoleAssignment.all_tenants.create(
        foundation_id=fx['foundation'].id, user=user, role=role,
        scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=school.id,
    )
    person = Person.all_tenants.create(foundation_id=fx['foundation'].id, nik=nik, full_name=name)
    Staff.all_tenants.create(
        foundation_id=fx['foundation'].id, person=person, user=user, school=school,
        join_date=datetime.date(2021, 1, 1), status=Staff.STATUS_ACTIVE,
    )
    return user


class WalletFinanceBase(TestCase):
    def setUp(self):
        self.fx = build_wallet_fixture()
        self.foundation = self.fx['foundation']
        self.school = self.fx['school']
        self.student = self.fx['student']
        set_current_foundation_id(self.foundation.id)
        self.finance = _staff_user(
            self.fx, self.school, '+6281500000001', 'Bendahara', RoleAssignment.ROLE_FINANCE_OFFICER, '3171010101010501',
        )
        self.school_admin = _staff_user(  # finance.payment.read, but not .write
            self.fx, self.school, '+6281500000002', 'Admin', RoleAssignment.ROLE_SCHOOL_ADMIN, '3171010101010502',
        )
        self.canteen_op = _staff_user(  # wallet.topup.*, no finance.payment.*
            self.fx, self.school, '+6281500000003', 'Kasir', RoleAssignment.ROLE_CANTEEN_OPERATOR, '3171010101010503',
        )
        self.school_b = School.all_tenants.create(
            foundation_id=self.foundation.id, name='SD B', npsn='30199999', level=School.LEVEL_SD,
        )
        self.finance_b = _staff_user(
            self.fx, self.school_b, '+6281500000004', 'Bendahara B', RoleAssignment.ROLE_FINANCE_OFFICER, '3171010101010504',
        )
        # A plain Django client: DRF's APIClient marks every request force-authenticated
        # (as no user), which makes TenancyMiddleware ignore the session login.
        self.client = Client()

    def tearDown(self):
        clear_current_foundation_id()

    def login(self, user):
        self.client.force_login(user)

    def flashed(self, response):
        return [str(m) for m in response.wsgi_request._messages]


class RefundQueueTests(WalletFinanceBase):
    def setUp(self):
        super().setUp()
        self.wallet = get_or_create_wallet(self.student)
        topup_wallet(self.wallet, Decimal('60000'), 'CASH', 'seed-refund-web')
        self.student.transition_status(Student.STATUS_GRADUATED)
        self.refund = WalletRefundRequest.all_tenants.get(wallet=self.wallet)
        self.url = reverse('wallet-refund-queue')

    def test_page_lists_pending_refund_with_action_forms(self):
        self.login(self.finance)
        response = self.client.get(self.url)
        self.assertContains(response, 'Dimas')
        self.assertContains(response, 'Rp 60.000')
        self.assertContains(response, reverse('wallet-refund-paid', kwargs={'pk': self.refund.pk}))
        self.assertContains(response, reverse('wallet-refund-donated', kwargs={'pk': self.refund.pk}))

    def test_read_only_holder_sees_the_queue_without_action_forms(self):
        self.login(self.school_admin)
        response = self.client.get(self.url)
        self.assertContains(response, 'Rp 60.000')
        self.assertNotContains(response, reverse('wallet-refund-paid', kwargs={'pk': self.refund.pk}))

    def test_mark_paid_closes_the_wallet_and_records_the_payout(self):
        self.login(self.finance)
        response = self.client.post(
            reverse('wallet-refund-paid', kwargs={'pk': self.refund.pk}),
            {'bank_name': 'BCA', 'account_number': '1234567890', 'account_holder_name': 'Bu Rahma', 'reference': 'TRF-1'},
        )
        self.assertRedirects(response, f"{self.url}?school_id={self.school.id}", fetch_redirect_response=False)
        self.refund.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertEqual(self.refund.status, WalletRefundStatus.PAID)
        self.assertEqual(self.refund.resolved_by, self.finance)
        self.assertEqual(self.refund.guardian_bank_name, 'BCA')
        self.assertEqual((self.wallet.status, self.wallet.balance), (WalletStatus.CLOSED, Decimal('0.00')))
        self.assertTrue(AuditEvent.objects.filter(action='wallet.refund_request.paid', entity_id=str(self.refund.id)).exists())

    def test_success_and_stale_messages_are_flashed_on_the_queue(self):
        self.login(self.finance)
        paid = reverse('wallet-refund-paid', kwargs={'pk': self.refund.pk})
        first = self.client.post(paid, follow=True)
        self.assertContains(first, 'Pengembalian dana dicatat sebagai dibayar.')
        second = self.client.post(paid, follow=True)
        self.assertContains(second, 'sudah diproses')

    def test_donation_requires_explicit_consent(self):
        self.login(self.finance)
        donated = reverse('wallet-refund-donated', kwargs={'pk': self.refund.pk})
        response = self.client.post(donated, follow=True)
        self.assertContains(response, 'Persetujuan wali wajib')
        self.refund.refresh_from_db()
        self.assertEqual(self.refund.status, WalletRefundStatus.PENDING)
        self.client.post(donated, {'donation_consent': 'on'})
        self.refund.refresh_from_db()
        self.assertEqual(self.refund.status, WalletRefundStatus.DONATED)

    def test_another_schools_finance_officer_cannot_act_or_view(self):
        self.login(self.finance_b)
        self.assertEqual(self.client.post(reverse('wallet-refund-paid', kwargs={'pk': self.refund.pk})).status_code, 404)
        self.assertEqual(self.client.get(self.url, {'school_id': self.school.id}).status_code, 404)
        self.refund.refresh_from_db()
        self.assertEqual(self.refund.status, WalletRefundStatus.PENDING)

    def test_read_only_holder_cannot_post(self):
        self.login(self.school_admin)
        response = self.client.post(reverse('wallet-refund-paid', kwargs={'pk': self.refund.pk}))
        self.assertRedirects(response, reverse('web-console-home'), fetch_redirect_response=False)
        self.refund.refresh_from_db()
        self.assertEqual(self.refund.status, WalletRefundStatus.PENDING)

    def test_canteen_operator_without_finance_keys_is_bounced(self):
        self.login(self.canteen_op)
        self.assertRedirects(self.client.get(self.url), reverse('web-console-home'), fetch_redirect_response=False)

    def test_guardian_without_staff_profile_is_bounced(self):
        guardian_user = User.all_tenants.create_user(foundation_id=self.foundation.id, phone_e164='+6281500000009', full_name='Wali')
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id, user=guardian_user, role=RoleAssignment.ROLE_PARENT,
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.school.id,
        )
        self.login(guardian_user)
        self.assertRedirects(self.client.get(self.url), reverse('web-console-home'), fetch_redirect_response=False)

    def test_anonymous_is_sent_to_login(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.assertEqual(self.client.post(reverse('wallet-refund-paid', kwargs={'pk': self.refund.pk})).status_code, 302)
        self.refund.refresh_from_db()
        self.assertEqual(self.refund.status, WalletRefundStatus.PENDING)

    def test_get_is_not_allowed_on_action_urls(self):
        self.login(self.finance)
        self.assertEqual(self.client.get(reverse('wallet-refund-paid', kwargs={'pk': self.refund.pk})).status_code, 405)

    def test_other_tenants_refund_is_404(self):
        other = build_wallet_fixture('Yayasan Wallet Lain')
        set_current_foundation_id(other['foundation'].id)
        other_wallet = get_or_create_wallet(other['student'])
        topup_wallet(other_wallet, Decimal('10000'), 'CASH', 'seed-other-tenant')
        other['student'].transition_status(Student.STATUS_GRADUATED)
        foreign = WalletRefundRequest.all_tenants.get(wallet=other_wallet)
        set_current_foundation_id(self.foundation.id)
        self.login(self.finance)
        self.assertEqual(self.client.post(reverse('wallet-refund-paid', kwargs={'pk': foreign.pk})).status_code, 404)


class ReconciliationQueueTests(WalletFinanceBase):
    def setUp(self):
        super().setUp()
        self.merchant, self.product, self.terminal = build_pos_fixture(self.fx)
        attach_financial_guardian(self.fx)
        self.case = make_open_case(self.fx, self.merchant, self.product, self.terminal)
        self.url = reverse('wallet-reconciliation-queue')

    def action(self, name):
        return reverse(f'wallet-reconciliation-{name}', kwargs={'pk': self.case.pk})

    def test_page_shows_exposure_and_case_with_actions(self):
        self.login(self.finance)
        response = self.client.get(self.url)
        self.assertContains(response, 'Total eksposur terbuka')
        self.assertContains(response, 'Dimas')
        for name in ('cash', 'write-off', 'invoice', 'resend'):
            self.assertContains(response, self.action(name))

    def test_cash_settlement_settles_the_case(self):
        self.login(self.finance)
        response = self.client.post(self.action('cash'), {'amount': str(self.case.shortfall), 'reference': 'Kuitansi 7'}, follow=True)
        self.assertContains(response, 'Pelunasan tunai dicatat.')
        self.case.refresh_from_db()
        self.assertEqual(self.case.status, WalletReconciliationStatus.SETTLED)

    def test_invalid_cash_amounts_are_refused_without_touching_the_wallet(self):
        self.login(self.finance)
        balance = self.case.wallet.balance
        for bad in ('', '0', '-5', 'abc', '1.234', 'NaN', 'Infinity'):
            response = self.client.post(self.action('cash'), {'amount': bad}, follow=True)
            self.assertContains(response, 'Jumlah harus angka positif', msg_prefix=repr(bad))
        self.case.wallet.refresh_from_db()
        self.case.refresh_from_db()
        self.assertEqual(self.case.wallet.balance, balance)
        self.assertEqual(self.case.status, WalletReconciliationStatus.OPEN)

    def test_a_settled_case_cannot_be_credited_twice(self):
        self.login(self.finance)
        self.client.post(self.action('cash'), {'amount': str(self.case.shortfall)})
        self.case.wallet.refresh_from_db()
        balance = self.case.wallet.balance
        response = self.client.post(self.action('cash'), {'amount': '5000'}, follow=True)
        self.assertContains(response, 'sudah diproses')
        self.case.wallet.refresh_from_db()
        self.assertEqual(self.case.wallet.balance, balance)

    def test_write_off_needs_a_reason_and_restores_the_balance(self):
        self.login(self.finance)
        response = self.client.post(self.action('write-off'), {'reason': '  '}, follow=True)
        self.assertContains(response, 'Alasan wajib diisi.')
        self.case.refresh_from_db()
        self.assertEqual(self.case.status, WalletReconciliationStatus.OPEN)
        self.client.post(self.action('write-off'), {'reason': 'Siswa pindah'})
        self.case.refresh_from_db()
        self.assertEqual(self.case.status, WalletReconciliationStatus.WRITTEN_OFF)
        self.assertEqual(self.case.written_off_by, self.finance)
        self.assertTrue(AuditEvent.objects.filter(action='wallet.reconciliation.written_off', entity_id=str(self.case.id)).exists())

    def test_invoice_now_hands_the_case_to_billing(self):
        self.login(self.finance)
        self.client.post(self.action('invoice'))
        self.case.refresh_from_db()
        self.assertEqual(self.case.status, WalletReconciliationStatus.INVOICED)
        self.assertIsNotNone(self.case.invoice_id)

    def test_resend_notice_succeeds_for_an_open_case(self):
        self.login(self.finance)
        response = self.client.post(self.action('resend'), follow=True)
        self.assertContains(response, 'Pemberitahuan dikirim ulang.')

    def test_actions_on_a_closed_case_are_refused(self):
        self.login(self.finance)
        self.client.post(self.action('write-off'), {'reason': 'x'})
        for name, data in (('invoice', {}), ('resend', {}), ('write-off', {'reason': 'y'})):
            response = self.client.post(self.action(name), data, follow=True)
            self.assertContains(response, 'sudah diproses', msg_prefix=name)

    def test_other_school_read_only_and_non_finance_users_cannot_act(self):
        self.login(self.finance_b)
        self.assertEqual(self.client.post(self.action('write-off'), {'reason': 'x'}).status_code, 404)
        for user in (self.school_admin, self.canteen_op):
            self.login(user)
            response = self.client.post(self.action('write-off'), {'reason': 'x'})
            self.assertRedirects(response, reverse('web-console-home'), fetch_redirect_response=False)
        self.case.refresh_from_db()
        self.assertEqual(self.case.status, WalletReconciliationStatus.OPEN)

    def test_status_filter_shows_history_without_actions(self):
        self.login(self.finance)
        self.client.post(self.action('write-off'), {'reason': 'x'})
        response = self.client.get(self.url, {'status': 'WRITTEN_OFF'})
        self.assertContains(response, 'Dimas')
        self.assertNotContains(response, self.action('cash'))

    def test_json_api_cash_endpoint_now_refuses_a_non_open_case(self):
        self.login(self.finance)
        self.client.post(self.action('write-off'), {'reason': 'x'})
        api = APIClient()
        api.force_authenticate(user=self.finance)
        response = api.post(
            f'/api/v1/wallet-reconciliations/{self.case.pk}/settle-cash/', {'amount': '1000'}, format='json',
        )
        self.assertEqual(response.status_code, 400)


class CanteenPageLinksTests(WalletFinanceBase):
    def test_finance_holder_sees_queue_links_and_canteen_operator_does_not(self):
        self.login(self.finance)
        self.assertContains(self.client.get(reverse('canteen-console-page')), reverse('wallet-refund-queue'))
        self.login(self.canteen_op)
        self.assertNotContains(self.client.get(reverse('canteen-console-page')), reverse('wallet-refund-queue'))
