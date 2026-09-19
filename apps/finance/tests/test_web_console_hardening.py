"""Hardening of the Keuangan console write actions: authority edge cases, unexpected
errors, and coverage gaps deferred from the PR #217 reviews."""
import datetime
from decimal import Decimal
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError
from django.urls import reverse

from apps.core.models import AuditEvent
from apps.finance.models import (
    Discount, DiscountStatus, GatewaySettlementBatch, InvoiceWriteOffRequest, InvoiceWriteOffStatus,
    Payment, PaymentDiscrepancy, PaymentMethod, PaymentStatus, DiscrepancyResolution,
)
from apps.finance.services.invoicing import approve_discount
from apps.finance.tests.test_web_console import make_invoice, make_student, make_user
from apps.finance.tests.test_web_console_actions import ActionTestBase, flashes
from apps.identity.models import RoleAssignment


class ApproveDiscountAuthorityTests(ActionTestBase):
    def setUp(self):
        super().setUp()
        self.discount = Discount.objects.create(
            foundation_id=self.foundation.id, student=self.s1, type='PERCENT', value=Decimal('50.00'),
            reason='Beasiswa', valid_from=datetime.date(2026, 7, 1), status=DiscountStatus.PENDING_APPROVAL,
        )
        self.url = reverse('finance-console-discount-approve', args=[self.discount.id])

    def test_non_admin_cannot_reapprove_an_already_approved_discount(self):
        approve_discount(self.discount, self.admin, reason='sesuai kebijakan')
        with self.assertRaises(PermissionDenied):
            approve_discount(self.discount, self.officer, reason='x')

    def test_foundation_admin_reapproval_stays_idempotent(self):
        approve_discount(self.discount, self.admin, reason='sesuai kebijakan')
        again = approve_discount(self.discount, self.admin, reason='sesuai kebijakan')
        self.assertEqual(again.status, DiscountStatus.APPROVED)
        self.assertEqual(
            AuditEvent.objects.filter(action='finance.discount.approved', entity_id=str(self.discount.id)).count(), 1,
        )

    def test_console_shows_an_error_not_success_when_non_admin_reapproves(self):
        approve_discount(self.discount, self.admin, reason='sesuai kebijakan')
        self.client.force_login(self.officer)
        response = self.client.post(self.url, {'reason': 'ok'}, follow=True)
        messages_shown = flashes(response)
        self.assertEqual(len(messages_shown), 1)
        self.assertNotIn('disetujui', messages_shown[0].lower())


class UnexpectedErrorTests(ActionTestBase):
    def setUp(self):
        super().setUp()
        batch = GatewaySettlementBatch.objects.create(
            foundation_id=self.foundation.id, provider='XENDIT', settlement_date=datetime.date(2026, 9, 1),
        )
        self.discrepancy = PaymentDiscrepancy.objects.create(
            foundation_id=self.foundation.id, batch=batch, external_id='EXT-1',
            discrepancy_type='MISSING_IN_SYSTEM', gateway_amount=Decimal('1000.00'),
        )
        self.url = reverse('finance-console-discrepancy-resolve', args=[self.discrepancy.id])
        self.client.force_login(self.officer)

    def test_unexpected_exception_becomes_a_generic_flash_not_a_500(self):
        with mock.patch('apps.finance.web_views.resolve_discrepancy', side_effect=IntegrityError('secret-detail-123')):
            with self.assertLogs('apps.finance.web_views', level='ERROR'):
                response = self.client.post(self.url, {'resolution': 'WAIVED'}, follow=True)
        self.assertEqual(response.status_code, 200)
        shown = flashes(response)
        self.assertEqual(len(shown), 1)
        self.assertNotIn('secret-detail-123', shown[0])
        self.discrepancy.refresh_from_db()
        self.assertEqual(self.discrepancy.resolution, DiscrepancyResolution.PENDING)

    def test_http404_from_lookup_is_not_swallowed(self):
        response = self.client.post(reverse('finance-console-discrepancy-resolve', args=[999999]), {'resolution': 'WAIVED'})
        self.assertEqual(response.status_code, 404)


class DiscrepancyCoverageTests(ActionTestBase):
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

    def test_school_scoped_officer_may_resolve_foundation_level_discrepancies(self):
        self.client.force_login(self.scoped_officer)
        self.client.post(self.url, {'resolution': 'WAIVED', 'notes': 'ok'}, follow=True)
        self.discrepancy.refresh_from_db()
        self.assertEqual(self.discrepancy.resolution, DiscrepancyResolution.WAIVED)

    def test_resolve_form_hidden_from_reader_without_payment_write(self):
        school_admin = make_user(
            self.foundation, '+6281300008050', RoleAssignment.ROLE_SCHOOL_ADMIN,
            RoleAssignment.SCOPE_SCHOOL, self.school1.id, staff_school=self.school1, name='Admin Sekolah',
        )
        self.client.force_login(school_admin)
        page = self.client.get(reverse('finance-console-reconciliation'), {'batch': self.batch.id})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'EXT-1')
        self.assertNotContains(page, self.url)
        self.assertRedirects(self.client.post(self.url, {'resolution': 'WAIVED'}), reverse('web-console-home'), fetch_redirect_response=False)

    def test_manual_settle_settles_the_linked_payment_and_allocates_to_the_invoice(self):
        invoice = make_invoice(self.foundation, self.s1, 'INV/A1/2026/000001')
        payment = Payment.objects.create(
            foundation_id=self.foundation.id, school=self.school1, student=self.s1, invoice=invoice,
            amount=Decimal('500000.00'), reference='PAY/A1/2026/000001', channel='BCA_VA',
            method=PaymentMethod.VA, status=PaymentStatus.PENDING,
        )
        self.discrepancy.payment = payment
        self.discrepancy.discrepancy_type = 'AMOUNT_MISMATCH'
        self.discrepancy.save()
        self.client.force_login(self.officer)
        self.client.post(self.url, {'resolution': 'MANUAL_SETTLED', 'notes': 'cocok'}, follow=True)
        payment.refresh_from_db()
        invoice.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.SETTLED)
        self.assertEqual(invoice.paid, Decimal('500000.00'))
        event = AuditEvent.objects.get(
            action='finance.reconciliation.discrepancy_resolved', entity_id=str(self.discrepancy.id),
        )
        self.assertEqual(event.school_id, self.school1.id)


class WriteOffCoverageTests(ActionTestBase):
    def setUp(self):
        super().setUp()
        self.invoice = make_invoice(self.foundation, self.s1, 'INV/A1/2026/000001')
        self.write_off = InvoiceWriteOffRequest.objects.create(
            foundation_id=self.foundation.id, invoice=self.invoice, school=self.school1,
            amount=Decimal('1500000.00'), reason='Siswa pindah', requested_by=self.officer,
        )
        self.approve_url = reverse('finance-console-writeoff-approve', args=[self.write_off.id])
        self.reject_url = reverse('finance-console-writeoff-reject', args=[self.write_off.id])

    def test_approve_writes_a_write_off_audit_event(self):
        self.client.force_login(self.admin)
        self.client.post(self.approve_url, {}, follow=True)
        self.assertTrue(AuditEvent.objects.filter(
            action='finance.invoice.written_off', entity_id=str(self.invoice.id)).exists())

    def test_reject_writes_a_rejection_audit_event(self):
        self.client.force_login(self.admin)
        self.client.post(self.reject_url, {'notes': 'masih bisa ditagih'}, follow=True)
        self.assertTrue(AuditEvent.objects.filter(
            action='finance.invoice.write_off_rejected', entity_id=str(self.write_off.id)).exists())

    def test_non_admin_reject_shows_error_and_changes_nothing(self):
        self.client.force_login(self.officer)
        response = self.client.post(self.reject_url, {'notes': 'x'}, follow=True)
        self.write_off.refresh_from_db()
        self.assertEqual(self.write_off.status, InvoiceWriteOffStatus.PENDING)
        self.assertEqual(len(flashes(response)), 1)

    def test_out_of_scope_write_off_is_404_and_left_untouched(self):
        s2_invoice = make_invoice(self.foundation, self.s2, 'INV/A2/2026/000001')
        other = InvoiceWriteOffRequest.objects.create(
            foundation_id=self.foundation.id, invoice=s2_invoice, school=self.school2,
            amount=Decimal('1500000.00'), reason='x', requested_by=self.officer,
        )
        self.client.force_login(self.scoped_officer)
        for name in ('finance-console-writeoff-approve', 'finance-console-writeoff-reject'):
            self.assertEqual(self.client.post(reverse(name, args=[other.id]), {}).status_code, 404)
        other.refresh_from_db()
        self.assertEqual(other.status, InvoiceWriteOffStatus.PENDING)

    def test_reject_controls_rendered_for_foundation_admin_only(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('finance-console-receivables')), self.reject_url)
        self.client.force_login(self.officer)
        self.assertNotContains(self.client.get(reverse('finance-console-receivables')), self.reject_url)


class DiscountCoverageTests(ActionTestBase):
    def setUp(self):
        super().setUp()
        self.discount = Discount.objects.create(
            foundation_id=self.foundation.id, student=self.s2, type='PERCENT', value=Decimal('10.00'),
            reason='Sekolah dua', valid_from=datetime.date(2026, 7, 1), status=DiscountStatus.PENDING_APPROVAL,
        )

    def test_out_of_scope_discount_is_404_and_left_untouched(self):
        self.client.force_login(self.scoped_officer)  # school1 only
        for name in ('finance-console-discount-approve', 'finance-console-discount-reject'):
            self.assertEqual(self.client.post(reverse(name, args=[self.discount.id]), {'reason': 'x'}).status_code, 404)
        self.discount.refresh_from_db()
        self.assertEqual(self.discount.status, DiscountStatus.PENDING_APPROVAL)

    def test_decision_buttons_need_invoice_write_as_well_as_foundation_admin(self):
        from apps.finance import web_views
        real = web_views.has_permission_in_any_scope

        def without_invoice_write(user, key, foundation_id):
            return False if key == 'finance.invoice.write' else real(user, key, foundation_id)

        self.client.force_login(self.admin)
        url = reverse('finance-console-discount-approve', args=[self.discount.id])
        self.assertContains(self.client.get(reverse('finance-console-receivables')), url)
        with mock.patch.object(web_views, 'has_permission_in_any_scope', side_effect=without_invoice_write):
            page = self.client.get(reverse('finance-console-receivables'))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, url)
