"""Keuangan console write actions (POST views): gate, tenancy, scope, service wiring."""
import datetime
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.core.models import AuditEvent
from apps.finance.models import (
    DiscrepancyResolution, Discount, DiscountStatus, GatewaySettlementBatch, InvoiceStatus,
    InvoiceWriteOffRequest, InvoiceWriteOffStatus, Payment, PaymentDiscrepancy, PaymentMethod,
)
from apps.finance.tests.test_web_console import make_foundation, make_invoice, make_student, make_user
from apps.identity.models import RoleAssignment
from educore.middleware.tenancy import clear_current_foundation_id


def flashes(response):
    """Flash message texts on a followed response."""
    return [str(message) for message in response.context['messages']]


class ActionTestBase(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation, (self.school1, self.school2) = make_foundation('A')
        self.s1 = make_student(self.foundation, self.school1, 'Budi Satu', '0001')
        self.s2 = make_student(self.foundation, self.school2, 'Sari Dua', '0002')
        self.admin = make_user(
            self.foundation, '+6281300008001', RoleAssignment.ROLE_FOUNDATION_ADMIN,
            RoleAssignment.SCOPE_FOUNDATION, self.foundation.id, staff_school=self.school1, name='Admin',
        )
        self.officer = make_user(
            self.foundation, '+6281300008002', RoleAssignment.ROLE_FINANCE_OFFICER,
            RoleAssignment.SCOPE_FOUNDATION, self.foundation.id, staff_school=self.school1, name='Officer',
        )
        self.scoped_officer = make_user(
            self.foundation, '+6281300008003', RoleAssignment.ROLE_FINANCE_OFFICER,
            RoleAssignment.SCOPE_SCHOOL, self.school1.id, staff_school=self.school1, name='Scoped',
        )


class DiscrepancyResolveTests(ActionTestBase):
    def setUp(self):
        super().setUp()
        self.batch = GatewaySettlementBatch.objects.create(
            foundation_id=self.foundation.id, provider='XENDIT', settlement_date=datetime.date(2026, 9, 1),
        )
        self.discrepancy = PaymentDiscrepancy.objects.create(
            foundation_id=self.foundation.id, batch=self.batch, external_id='EXT-1',
            discrepancy_type='MISSING_IN_SYSTEM', gateway_amount=Decimal('1000.00'),
        )
        self.url = reverse('finance-console-discrepancy-resolve', args=[self.discrepancy.id])

    def _post(self, **data):
        data.setdefault('resolution', 'WAIVED')
        return self.client.post(self.url, data, follow=True)

    def test_resolves_and_redirects_back_to_the_batch(self):
        self.client.force_login(self.officer)
        response = self._post(notes='tidak material')
        self.discrepancy.refresh_from_db()
        self.assertEqual(self.discrepancy.resolution, DiscrepancyResolution.WAIVED)
        self.assertEqual(self.discrepancy.resolution_notes, 'tidak material')
        self.assertEqual(response.redirect_chain[-1][0].split('#')[0],
                         f"{reverse('finance-console-reconciliation')}?batch={self.batch.id}")
        self.assertEqual(len(flashes(response)), 1)
        self.assertContains(response, 'fin-msg')  # messages partial rendered on the page
        self.assertTrue(AuditEvent.objects.filter(
            action='finance.reconciliation.discrepancy_resolved', entity_id=str(self.discrepancy.id)).exists())

    def test_invalid_resolution_rejected_before_service(self):
        self.client.force_login(self.officer)
        response = self._post(resolution='AUTO_SETTLED')
        self.discrepancy.refresh_from_db()
        self.assertEqual(self.discrepancy.resolution, DiscrepancyResolution.PENDING)
        self.assertEqual(len(flashes(response)), 1)

    def test_already_resolved_shows_error_and_keeps_first_resolution(self):
        self.client.force_login(self.officer)
        self._post(resolution='WAIVED')
        response = self._post(resolution='ESCALATED')
        self.discrepancy.refresh_from_db()
        self.assertEqual(self.discrepancy.resolution, DiscrepancyResolution.WAIVED)
        self.assertIn('already', flashes(response)[0])

    def test_get_is_405(self):
        self.client.force_login(self.officer)
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_anonymous_redirects_to_login(self):
        response = self.client.post(self.url, {'resolution': 'WAIVED'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('/web/login/', response['Location'])

    def test_user_without_payment_write_gets_403(self):
        teacher = make_user(
            self.foundation, '+6281300008010', RoleAssignment.ROLE_TEACHER,
            RoleAssignment.SCOPE_SCHOOL, self.school1.id, staff_school=self.school1, name='Guru',
        )
        self.client.force_login(teacher)
        self.assertEqual(self.client.post(self.url, {'resolution': 'WAIVED'}).status_code, 403)

    def test_no_staff_profile_gets_403(self):
        no_staff = make_user(
            self.foundation, '+6281300008011', RoleAssignment.ROLE_FINANCE_OFFICER,
            RoleAssignment.SCOPE_FOUNDATION, self.foundation.id, name='NoStaff',
        )
        self.client.force_login(no_staff)
        self.assertEqual(self.client.post(self.url, {'resolution': 'WAIVED'}).status_code, 403)

    def test_other_tenants_discrepancy_is_404(self):
        other, (other_school, _) = make_foundation('B')
        foreign_batch = GatewaySettlementBatch.objects.create(
            foundation_id=other.id, provider='MIDTRANS', settlement_date=datetime.date(2026, 9, 1),
        )
        foreign = PaymentDiscrepancy.objects.create(
            foundation_id=other.id, batch=foreign_batch, external_id='EXT-9',
            discrepancy_type='MISSING_IN_SYSTEM', gateway_amount=Decimal('5.00'),
        )
        self.client.force_login(self.officer)
        response = self.client.post(
            reverse('finance-console-discrepancy-resolve', args=[foreign.id]), {'resolution': 'WAIVED'},
        )
        self.assertEqual(response.status_code, 404)
        foreign.refresh_from_db()
        self.assertEqual(foreign.resolution, DiscrepancyResolution.PENDING)

    def test_resolve_form_shown_only_for_pending_rows_with_write_permission(self):
        self.client.force_login(self.officer)
        page = self.client.get(reverse('finance-console-reconciliation'), {'batch': self.batch.id})
        self.assertContains(page, self.url)
        self._post(resolution='WAIVED')
        page = self.client.get(reverse('finance-console-reconciliation'), {'batch': self.batch.id})
        self.assertNotContains(page, self.url)


class DiscountDecisionTests(ActionTestBase):
    def setUp(self):
        super().setUp()
        self.discount = Discount.objects.create(
            foundation_id=self.foundation.id, student=self.s1, type='PERCENT', value=Decimal('50.00'),
            reason='Beasiswa', valid_from=datetime.date(2026, 7, 1), status=DiscountStatus.PENDING_APPROVAL,
        )
        self.approve_url = reverse('finance-console-discount-approve', args=[self.discount.id])
        self.reject_url = reverse('finance-console-discount-reject', args=[self.discount.id])

    def test_foundation_admin_approves(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.approve_url, {'reason': 'sesuai kebijakan'}, follow=True)
        self.discount.refresh_from_db()
        self.assertEqual(self.discount.status, DiscountStatus.APPROVED)
        self.assertEqual(response.redirect_chain[-1][0], reverse('finance-console-receivables'))
        self.assertEqual(len(flashes(response)), 1)
        self.assertTrue(AuditEvent.objects.filter(
            action='finance.discount.approved', entity_id=str(self.discount.id)).exists())

    def test_foundation_admin_rejects(self):
        self.client.force_login(self.admin)
        self.client.post(self.reject_url, {'reason': 'tidak memenuhi syarat'}, follow=True)
        self.discount.refresh_from_db()
        self.assertEqual(self.discount.status, DiscountStatus.REJECTED)

    def test_reject_without_reason_shows_error_and_changes_nothing(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.reject_url, {'reason': '   '}, follow=True)
        self.discount.refresh_from_db()
        self.assertEqual(self.discount.status, DiscountStatus.PENDING_APPROVAL)
        self.assertEqual(len(flashes(response)), 1)

    def test_second_reject_shows_error(self):
        self.client.force_login(self.admin)
        self.client.post(self.reject_url, {'reason': 'x'}, follow=True)
        response = self.client.post(self.reject_url, {'reason': 'x'}, follow=True)
        self.assertEqual(len(flashes(response)), 1)
        self.discount.refresh_from_db()
        self.assertEqual(self.discount.status, DiscountStatus.REJECTED)

    def test_finance_officer_who_is_not_foundation_admin_gets_error_and_no_change(self):
        self.client.force_login(self.officer)
        response = self.client.post(self.approve_url, {'reason': 'ok'}, follow=True)
        self.discount.refresh_from_db()
        self.assertEqual(self.discount.status, DiscountStatus.PENDING_APPROVAL)
        self.assertEqual(len(flashes(response)), 1)

    def test_buttons_only_rendered_for_foundation_admin(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('finance-console-receivables')), self.approve_url)
        self.client.force_login(self.officer)
        self.assertNotContains(self.client.get(reverse('finance-console-receivables')), self.approve_url)

    def test_out_of_scope_school_discount_is_404(self):
        self.client.force_login(self.scoped_officer)  # school1 only
        other = Discount.objects.create(
            foundation_id=self.foundation.id, student=self.s2, type='PERCENT', value=Decimal('10.00'),
            reason='Sekolah dua', valid_from=datetime.date(2026, 7, 1), status=DiscountStatus.PENDING_APPROVAL,
        )
        response = self.client.post(
            reverse('finance-console-discount-approve', args=[other.id]), {'reason': 'x'},
        )
        self.assertEqual(response.status_code, 404)

    def test_other_tenant_discount_is_404(self):
        other, (other_school, _) = make_foundation('B')
        student = make_student(other, other_school, 'Orang Lain', '9001')
        foreign = Discount.objects.create(
            foundation_id=other.id, student=student, type='PERCENT', value=Decimal('10.00'),
            reason='Asing', valid_from=datetime.date(2026, 7, 1), status=DiscountStatus.PENDING_APPROVAL,
        )
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse('finance-console-discount-approve', args=[foreign.id]), {'reason': 'x'},
        )
        self.assertEqual(response.status_code, 404)

    def test_get_is_405_and_anonymous_redirects(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.approve_url).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.post(self.approve_url, {'reason': 'x'}).status_code, 302)

    def test_user_without_invoice_write_gets_403(self):
        teacher = make_user(
            self.foundation, '+6281300008020', RoleAssignment.ROLE_TEACHER,
            RoleAssignment.SCOPE_SCHOOL, self.school1.id, staff_school=self.school1, name='Guru',
        )
        self.client.force_login(teacher)
        self.assertEqual(self.client.post(self.approve_url, {'reason': 'x'}).status_code, 403)


class WriteOffDecisionTests(ActionTestBase):
    def setUp(self):
        super().setUp()
        self.invoice = make_invoice(self.foundation, self.s1, 'INV/A1/2026/000001')
        self.write_off = InvoiceWriteOffRequest.objects.create(
            foundation_id=self.foundation.id, invoice=self.invoice, school=self.school1,
            amount=Decimal('1500000.00'), reason='Siswa pindah', requested_by=self.officer,
        )
        self.approve_url = reverse('finance-console-writeoff-approve', args=[self.write_off.id])
        self.reject_url = reverse('finance-console-writeoff-reject', args=[self.write_off.id])

    def test_foundation_admin_approves_and_invoice_is_written_off(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.approve_url, {'notes': 'disetujui'}, follow=True)
        self.write_off.refresh_from_db()
        self.invoice.refresh_from_db()
        self.assertEqual(self.write_off.status, InvoiceWriteOffStatus.APPROVED)
        self.assertEqual(self.invoice.status, InvoiceStatus.WRITTEN_OFF)
        self.assertEqual(len(flashes(response)), 1)

    def test_foundation_admin_rejects_with_notes(self):
        self.client.force_login(self.admin)
        self.client.post(self.reject_url, {'notes': 'masih bisa ditagih'}, follow=True)
        self.write_off.refresh_from_db()
        self.assertEqual(self.write_off.status, InvoiceWriteOffStatus.REJECTED)
        self.assertEqual(self.write_off.rejection_reason, 'masih bisa ditagih')

    def test_second_decision_shows_error(self):
        self.client.force_login(self.admin)
        self.client.post(self.reject_url, {'notes': 'x'}, follow=True)
        response = self.client.post(self.approve_url, {'notes': 'y'}, follow=True)
        self.assertEqual(len(flashes(response)), 1)
        self.write_off.refresh_from_db()
        self.assertEqual(self.write_off.status, InvoiceWriteOffStatus.REJECTED)

    def test_non_foundation_admin_gets_error_and_no_change(self):
        self.client.force_login(self.officer)
        response = self.client.post(self.approve_url, {'notes': 'x'}, follow=True)
        self.write_off.refresh_from_db()
        self.assertEqual(self.write_off.status, InvoiceWriteOffStatus.PENDING)
        self.assertEqual(len(flashes(response)), 1)

    def test_out_of_scope_and_other_tenant_are_404(self):
        s2_invoice = make_invoice(self.foundation, self.s2, 'INV/A2/2026/000001')
        other_school_request = InvoiceWriteOffRequest.objects.create(
            foundation_id=self.foundation.id, invoice=s2_invoice, school=self.school2,
            amount=Decimal('1500000.00'), reason='x', requested_by=self.officer,
        )
        self.client.force_login(self.scoped_officer)
        self.assertEqual(self.client.post(
            reverse('finance-console-writeoff-approve', args=[other_school_request.id]), {}).status_code, 404)

        other, (other_school, _) = make_foundation('B')
        student = make_student(other, other_school, 'Orang Lain', '9001')
        foreign_invoice = make_invoice(other, student, 'INV/B1/2026/000001')
        foreign_user = make_user(
            other, '+6281300008030', RoleAssignment.ROLE_FINANCE_OFFICER,
            RoleAssignment.SCOPE_FOUNDATION, other.id, staff_school=other_school, name='B',
        )
        foreign = InvoiceWriteOffRequest.objects.create(
            foundation_id=other.id, invoice=foreign_invoice, school=other_school,
            amount=Decimal('1500000.00'), reason='x', requested_by=foreign_user,
        )
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(
            reverse('finance-console-writeoff-approve', args=[foreign.id]), {}).status_code, 404)

    def test_buttons_only_rendered_for_foundation_admin(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('finance-console-receivables')), self.approve_url)
        self.client.force_login(self.officer)
        self.assertNotContains(self.client.get(reverse('finance-console-receivables')), self.approve_url)


class CashPaymentTests(ActionTestBase):
    def setUp(self):
        super().setUp()
        self.url = reverse('finance-console-cash-payment')
        self.invoice = make_invoice(self.foundation, self.s1, 'INV/A1/2026/000001')

    def _post(self, **data):
        data.setdefault('nis', '0001')
        data.setdefault('amount', '500000')
        return self.client.post(self.url, data, follow=True)

    def test_records_cash_payment_and_allocates_to_open_invoice(self):
        self.client.force_login(self.officer)
        response = self._post(notes='setoran tunai')
        payment = Payment.all_tenants.get(student=self.s1)
        self.assertEqual(payment.method, PaymentMethod.CASH)
        self.assertEqual(payment.amount, Decimal('500000.00'))
        self.assertEqual(payment.received_by, self.officer)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid, Decimal('500000.00'))
        self.assertEqual(response.redirect_chain[-1][0], reverse('finance-console-billing'))
        self.assertIn(payment.receipt_number, flashes(response)[0])

    def test_bad_amounts_create_nothing(self):
        self.client.force_login(self.officer)
        for bad in ('0', '-5', 'abc', '10.999', 'NaN', 'Infinity', ''):
            response = self._post(amount=bad)
            self.assertEqual(len(flashes(response)), 1, bad)
        self.assertFalse(Payment.all_tenants.exists())

    def test_unknown_nis_creates_nothing(self):
        self.client.force_login(self.officer)
        response = self._post(nis='9999')
        self.assertEqual(len(flashes(response)), 1)
        self.assertFalse(Payment.all_tenants.exists())

    def test_school_scoped_officer_cannot_pay_for_another_school(self):
        self.client.force_login(self.scoped_officer)  # school1 only; s2 is school2
        response = self._post(nis='0002')
        self.assertEqual(len(flashes(response)), 1)
        self.assertFalse(Payment.all_tenants.exists())

    def test_other_tenants_nis_is_not_found(self):
        other, (other_school, _) = make_foundation('B')
        make_student(other, other_school, 'Orang Lain', '7777')
        self.client.force_login(self.admin)
        response = self._post(nis='7777')
        self.assertEqual(len(flashes(response)), 1)
        self.assertFalse(Payment.all_tenants.exists())

    def test_ambiguous_nis_across_schools_is_refused(self):
        make_student(self.foundation, self.school2, 'Kembar NIS', '0001')  # same NIS, other school
        self.client.force_login(self.officer)  # foundation-wide: sees both
        response = self._post(nis='0001')
        self.assertEqual(len(flashes(response)), 1)
        self.assertFalse(Payment.all_tenants.exists())

    def test_get_is_405_anonymous_redirects_no_permission_403(self):
        self.client.force_login(self.officer)
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.post(self.url, {}).status_code, 302)
        teacher = make_user(
            self.foundation, '+6281300008040', RoleAssignment.ROLE_TEACHER,
            RoleAssignment.SCOPE_SCHOOL, self.school1.id, staff_school=self.school1, name='Guru',
        )
        self.client.force_login(teacher)
        self.assertEqual(self.client.post(self.url, {'nis': '0001', 'amount': '1'}).status_code, 403)

    def test_cash_form_shown_on_billing_page_for_writers_only(self):
        self.client.force_login(self.officer)
        self.assertContains(self.client.get(reverse('finance-console-billing')), self.url)
