"""Kantin & dompet console: refund and reconciliation work queues and their POST actions."""
import datetime
from decimal import Decimal

from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import AuditEvent
from apps.identity.models import Person, RoleAssignment, School, Staff, Student, User
from apps.wallet.models import (
    WalletReconciliation, WalletReconciliationStatus, WalletReconciliationTrigger, WalletRefundRequest, WalletRefundStatus, WalletStatus,
)
from apps.wallet.services import get_or_create_wallet, topup_wallet
from apps.wallet.tests.test_pos_checkout import build_pos_fixture
from apps.wallet.tests.test_reconciliation import attach_financial_guardian
from apps.wallet.tests.test_reconciliation_followup import make_open_case
from apps.wallet.tests.test_wallet_core import build_wallet_fixture


def _staff_user(fx, phone, name, role, school=None, foundation_scope=False):
    user = User.objects.create(foundation_id=fx['foundation'].id, phone_e164=phone, full_name=name)
    person = Person.all_tenants.create(foundation_id=fx['foundation'].id, full_name=name)
    Staff.all_tenants.create(
        foundation_id=fx['foundation'].id, person=person, user=user, school=school or fx['school'],
        join_date=datetime.date(2020, 1, 1),
    )
    RoleAssignment.all_tenants.create(
        foundation_id=fx['foundation'].id, user=user, role=role,
        scope_type=RoleAssignment.SCOPE_FOUNDATION if foundation_scope else RoleAssignment.SCOPE_SCHOOL,
        scope_id=fx['foundation'].id if foundation_scope else (school or fx['school']).id,
    )
    return user


class CanteenActionTestBase(TestCase):
    def setUp(self):
        self.fx = build_wallet_fixture()
        self.foundation = self.fx['foundation']
        self.school = self.fx['school']
        self.finance = _staff_user(self.fx, '+6281400000001', 'Bendahara', 'finance_officer')
        self.canteen_op = _staff_user(self.fx, '+6281400000002', 'Kasir', 'canteen_operator')
        self.page = reverse('canteen-console-page')

    def flashed(self, response):
        return [str(m) for m in get_messages(response.wsgi_request)]

    def post(self, name, pk, data=None, user=None):
        self.client.force_login(user or self.finance)
        return self.client.post(f"{reverse(name, args=[pk])}?school_id={self.school.id}", data or {})

    def back(self):
        return f"{self.page}?school_id={self.school.id}"


class ReconciliationActionTests(CanteenActionTestBase):
    def setUp(self):
        super().setUp()
        self.merchant, self.product, self.terminal = build_pos_fixture(self.fx)
        attach_financial_guardian(self.fx)
        self.case = make_open_case(self.fx, self.merchant, self.product, self.terminal)

    def case_status(self):
        return WalletReconciliation.all_tenants.get(id=self.case.id).status

    def test_page_lists_the_case_for_a_finance_officer_only(self):
        self.client.force_login(self.finance)
        response = self.client.get(self.page)
        self.assertEqual(len(response.context['reconciliations']), 1)
        self.assertContains(response, reverse('canteen-recon-cash', args=[self.case.id]))
        self.client.force_login(self.canteen_op)  # wallet.topup.read but no finance.payment.write
        response = self.client.get(self.page)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('reconciliations', response.context)
        self.assertNotContains(response, reverse('canteen-recon-cash', args=[self.case.id]))

    def test_cash_settlement_settles_the_case(self):
        response = self.post('canteen-recon-cash', self.case.id, {'amount': str(self.case.shortfall), 'reference': 'Tunai'})
        self.assertRedirects(response, self.back(), fetch_redirect_response=False)
        self.assertEqual(self.case_status(), WalletReconciliationStatus.SETTLED)

    def test_cash_settlement_rejects_bad_amounts_and_changes_nothing(self):
        for bad in ('', 'abc', '0', '-5', '10.999', 'NaN', 'Infinity'):
            response = self.post('canteen-recon-cash', self.case.id, {'amount': bad})
            self.assertRedirects(response, self.back(), fetch_redirect_response=False)
            self.assertEqual(self.case_status(), WalletReconciliationStatus.OPEN, bad)

    def test_write_off_requires_a_reason(self):
        self.post('canteen-recon-writeoff', self.case.id, {'reason': '  '})
        self.assertEqual(self.case_status(), WalletReconciliationStatus.OPEN)
        self.post('canteen-recon-writeoff', self.case.id, {'reason': 'Beasiswa kantin'})
        self.assertEqual(self.case_status(), WalletReconciliationStatus.WRITTEN_OFF)
        self.assertTrue(AuditEvent.objects.filter(action='wallet.reconciliation.written_off').exists())

    def test_second_write_off_reports_already_processed(self):
        self.post('canteen-recon-writeoff', self.case.id, {'reason': 'x'})
        response = self.post('canteen-recon-writeoff', self.case.id, {'reason': 'x'})
        self.assertIn('sudah diproses', ' '.join(self.flashed(response)))

    def test_invoice_now_invoices_the_case(self):
        self.post('canteen-recon-invoice', self.case.id)
        self.assertEqual(self.case_status(), WalletReconciliationStatus.INVOICED)

    def test_invoice_now_on_a_non_open_case_is_an_error(self):
        self.post('canteen-recon-writeoff', self.case.id, {'reason': 'x'})
        response = self.post('canteen-recon-invoice', self.case.id)
        self.assertIn('sudah diproses', ' '.join(self.flashed(response)))

    def test_resend_notice_redirects_back(self):
        response = self.post('canteen-recon-resend', self.case.id)
        self.assertRedirects(response, self.back(), fetch_redirect_response=False)

    def test_user_without_finance_payment_write_is_denied_and_nothing_changes(self):
        response = self.post('canteen-recon-writeoff', self.case.id, {'reason': 'x'}, user=self.canteen_op)
        self.assertRedirects(response, reverse('web-console-home'), fetch_redirect_response=False)
        self.assertEqual(self.case_status(), WalletReconciliationStatus.OPEN)

    def test_anonymous_is_denied(self):
        url = f"{reverse('canteen-recon-writeoff', args=[self.case.id])}?school_id={self.school.id}"
        self.assertIn(self.client.post(url, {'reason': 'x'}).status_code, (401, 403))
        self.assertEqual(self.case_status(), WalletReconciliationStatus.OPEN)

    def test_get_is_not_allowed(self):
        self.client.force_login(self.finance)
        url = f"{reverse('canteen-recon-writeoff', args=[self.case.id])}?school_id={self.school.id}"
        self.assertEqual(self.client.get(url).status_code, 405)

    def test_another_schools_case_is_a_404(self):
        """A finance officer scoped to school B must not act on school A's case,
        even by passing school B as ?school_id=."""
        school_b = School.all_tenants.create(
            foundation_id=self.foundation.id, name='SD B', npsn='39999999', level=School.LEVEL_SD,
        )
        finance_b = _staff_user(self.fx, '+6281400000003', 'Bendahara B', 'finance_officer', school=school_b)
        self.client.force_login(finance_b)
        url = f"{reverse('canteen-recon-writeoff', args=[self.case.id])}?school_id={school_b.id}"
        self.assertEqual(self.client.post(url, {'reason': 'x'}).status_code, 404)
        url = f"{reverse('canteen-recon-writeoff', args=[self.case.id])}?school_id={self.school.id}"
        self.assertIn(self.client.post(url, {'reason': 'x'}).status_code, (302, 404))
        self.assertEqual(self.case_status(), WalletReconciliationStatus.OPEN)

    def test_another_foundations_case_is_a_404(self):
        other = build_wallet_fixture('Yayasan Lain')
        other_finance = _staff_user(other, '+6281400000009', 'Bendahara Lain', 'finance_officer')
        other_case = WalletReconciliation.all_tenants.create(
            foundation_id=other['foundation'].id, wallet=get_or_create_wallet(other['student']),
            student=other['student'], trigger=WalletReconciliationTrigger.OFFLINE_OVERSPEND,
            shortfall=Decimal('5000.00'), currency='IDR', balance_at_detection=Decimal('-5000.00'),
            detected_at=timezone.now(), status=WalletReconciliationStatus.OPEN,
        )
        self.client.force_login(self.finance)
        url = f"{reverse('canteen-recon-writeoff', args=[other_case.id])}?school_id={self.school.id}"
        self.assertEqual(self.client.post(url, {'reason': 'x'}).status_code, 404)
        self.assertEqual(WalletReconciliation.all_tenants.get(id=other_case.id).status, WalletReconciliationStatus.OPEN)
        self.assertIsNotNone(other_finance)


class RefundActionTests(CanteenActionTestBase):
    def setUp(self):
        super().setUp()
        self.wallet = get_or_create_wallet(self.fx['student'])
        topup_wallet(self.wallet, Decimal('60000'), 'CASH', 'seed-refund')
        self.fx['student'].transition_status(Student.STATUS_GRADUATED)
        self.refund = WalletRefundRequest.all_tenants.get(wallet=self.wallet)

    def refund_row(self):
        return WalletRefundRequest.all_tenants.get(id=self.refund.id)

    def test_page_lists_pending_refund(self):
        self.client.force_login(self.finance)
        response = self.client.get(self.page)
        self.assertEqual([r['id'] for r in response.context['refunds']], [self.refund.id])
        self.assertContains(response, reverse('canteen-refund-paid', args=[self.refund.id]))

    def test_mark_paid_closes_the_wallet_and_records_the_bank_details(self):
        data = {'bank_name': 'BCA', 'account_number': '123456', 'account_holder_name': 'Bu Rina', 'reference': 'TRF-1'}
        response = self.post('canteen-refund-paid', self.refund.id, data)
        self.assertRedirects(response, self.back(), fetch_redirect_response=False)
        row = self.refund_row()
        self.assertEqual(row.status, WalletRefundStatus.PAID)
        self.assertEqual((row.guardian_bank_name, row.resolved_by_id), ('BCA', self.finance.id))
        self.wallet.refresh_from_db()
        self.assertEqual((self.wallet.status, self.wallet.balance), (WalletStatus.CLOSED, Decimal('0.00')))
        self.assertTrue(AuditEvent.objects.filter(action='wallet.refund_request.paid').exists())

    def test_double_submit_is_reported_and_does_not_pay_twice(self):
        data = {'bank_name': 'BCA', 'account_number': '1', 'account_holder_name': 'X'}
        self.post('canteen-refund-paid', self.refund.id, data)
        response = self.post('canteen-refund-paid', self.refund.id, data)
        self.assertIn('sudah diproses', ' '.join(self.flashed(response)))
        self.assertEqual(AuditEvent.objects.filter(action='wallet.refund_request.paid').count(), 1)

    def test_donation_requires_explicit_consent(self):
        response = self.post('canteen-refund-donated', self.refund.id, {})
        self.assertIn('Persetujuan', ' '.join(self.flashed(response)))
        self.assertEqual(self.refund_row().status, WalletRefundStatus.PENDING)
        self.post('canteen-refund-donated', self.refund.id, {'donation_consent': 'on'})
        row = self.refund_row()
        self.assertEqual((row.status, row.donation_consent), (WalletRefundStatus.DONATED, True))

    def test_user_without_finance_payment_write_cannot_act(self):
        response = self.post('canteen-refund-paid', self.refund.id, {'bank_name': 'BCA'}, user=self.canteen_op)
        self.assertRedirects(response, reverse('web-console-home'), fetch_redirect_response=False)
        self.assertEqual(self.refund_row().status, WalletRefundStatus.PENDING)

    def test_another_schools_refund_is_a_404(self):
        school_b = School.all_tenants.create(
            foundation_id=self.foundation.id, name='SD B', npsn='39999998', level=School.LEVEL_SD,
        )
        finance_b = _staff_user(self.fx, '+6281400000004', 'Bendahara B', 'finance_officer', school=school_b)
        self.client.force_login(finance_b)
        url = f"{reverse('canteen-refund-paid', args=[self.refund.id])}?school_id={school_b.id}"
        self.assertEqual(self.client.post(url, {'bank_name': 'BCA'}).status_code, 404)
        url = f"{reverse('canteen-refund-paid', args=[self.refund.id])}?school_id={self.school.id}"
        self.assertIn(self.client.post(url, {'bank_name': 'BCA'}).status_code, (302, 404))
        self.assertEqual(self.refund_row().status, WalletRefundStatus.PENDING)

    def test_foundation_admin_can_act_in_any_school(self):
        chair = _staff_user(self.fx, '+6281400000005', 'Ketua', 'foundation_admin', foundation_scope=True)
        self.post('canteen-refund-donated', self.refund.id, {'donation_consent': 'on'}, user=chair)
        self.assertEqual(self.refund_row().status, WalletRefundStatus.DONATED)


class CanteenActionsEnglishTests(CanteenActionTestBase):
    def test_action_flash_translates(self):
        self.client.force_login(self.finance)
        self.client.cookies['django_language'] = 'en'
        wallet = get_or_create_wallet(self.fx['student'])
        topup_wallet(wallet, Decimal('1000'), 'CASH', 'seed-en')
        self.fx['student'].transition_status(Student.STATUS_GRADUATED)
        refund = WalletRefundRequest.all_tenants.get(wallet=wallet)
        url = f"{reverse('canteen-refund-donated', args=[refund.id])}?school_id={self.school.id}"
        response = self.client.post(url, {})
        self.assertIn('Guardian consent is required', ' '.join(self.flashed(response)))
