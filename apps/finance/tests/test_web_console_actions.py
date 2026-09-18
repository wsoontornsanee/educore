"""Keuangan console write actions (POST views): gate, tenancy, scope, service wiring."""
import datetime
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.core.models import AuditEvent
from apps.finance.models import (
    DiscrepancyResolution, GatewaySettlementBatch, PaymentDiscrepancy,
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
