"""School ceiling on the wallet refund/reconciliation JSON endpoints: a
finance officer scoped to one school must not read or act on another
school's refunds or reconciliation cases (tenant isolation already held;
the gap was across schools within one foundation)."""
from decimal import Decimal

from rest_framework.test import APIClient, APITestCase

from apps.identity.models import RoleAssignment, School, Student, User
from apps.wallet.models import (
    WalletReconciliation, WalletReconciliationStatus, WalletRefundRequest, WalletRefundStatus,
)
from apps.wallet.services import get_or_create_wallet, topup_wallet
from apps.wallet.tests.test_pos_checkout import build_pos_fixture
from apps.wallet.tests.test_reconciliation import attach_financial_guardian
from apps.wallet.tests.test_reconciliation_followup import make_open_case
from apps.wallet.tests.test_wallet_core import build_wallet_fixture


def _user(foundation, phone, name, role, scope_type, scope_id):
    user = User.objects.create(foundation_id=foundation.id, phone_e164=phone, full_name=name)
    RoleAssignment.all_tenants.create(
        foundation_id=foundation.id, user=user, role=role, scope_type=scope_type, scope_id=scope_id,
    )
    return user


class WalletApiSchoolCeilingTests(APITestCase):
    def setUp(self):
        self.fx = build_wallet_fixture()
        self.foundation = self.fx['foundation']
        self.school_a = self.fx['school']
        self.school_b = School.all_tenants.create(
            foundation_id=self.foundation.id, name='SD B', npsn='39999991', level=School.LEVEL_SD,
        )
        self.finance_a = self.fx['finance_user']  # finance_officer scoped to school A
        self.finance_b = _user(
            self.foundation, '+6281500000002', 'Bendahara B', 'finance_officer',
            RoleAssignment.SCOPE_SCHOOL, self.school_b.id,
        )
        self.chair = _user(
            self.foundation, '+6281500000003', 'Ketua', 'foundation_admin',
            RoleAssignment.SCOPE_FOUNDATION, self.foundation.id,
        )
        # School A: an open reconciliation case and a pending refund (two separate students).
        merchant, product, terminal = build_pos_fixture(self.fx)
        attach_financial_guardian(self.fx)
        self.case = make_open_case(self.fx, merchant, product, terminal)
        from apps.identity.models import Person
        person = Person.all_tenants.create(foundation_id=self.foundation.id, nik='3471010101011099', full_name='Rani')
        self.exit_student = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school_a, person=person, nisn='9988776655',
            nis='SD-002', status=Student.STATUS_ACTIVE,
        )
        wallet = get_or_create_wallet(self.exit_student)
        topup_wallet(wallet, Decimal('60000'), 'CASH', 'seed-api-refund')
        self.exit_student.transition_status(Student.STATUS_GRADUATED)
        self.refund = WalletRefundRequest.all_tenants.get(wallet=wallet)

    def as_user(self, user):
        self.client = APIClient()
        self.client.force_authenticate(user=user)

    def case_status(self):
        return WalletReconciliation.all_tenants.get(id=self.case.id).status

    def refund_status(self):
        return WalletRefundRequest.all_tenants.get(id=self.refund.id).status

    # --- actions on another school's rows --------------------------------
    def test_other_school_officer_cannot_act_on_a_refund(self):
        self.as_user(self.finance_b)
        for name, body in (('mark-paid', {}), ('mark-donated', {'donation_consent': True})):
            response = self.client.post(f'/api/v1/wallet-refunds/{self.refund.id}/{name}/', body, format='json')
            self.assertEqual(response.status_code, 404, name)
        self.assertEqual(self.refund_status(), WalletRefundStatus.PENDING)

    def test_other_school_officer_cannot_act_on_a_reconciliation_case(self):
        self.as_user(self.finance_b)
        for name, body in (
            ('settle-cash', {'amount': '1000'}), ('invoice-now', {}),
            ('write-off', {'reason': 'x'}), ('resend-notice', {}),
        ):
            response = self.client.post(f'/api/v1/wallet-reconciliations/{self.case.id}/{name}/', body, format='json')
            self.assertEqual(response.status_code, 404, name)
        self.assertEqual(self.case_status(), WalletReconciliationStatus.OPEN)

    # --- reads of another school's queues --------------------------------
    def test_other_school_officer_cannot_read_the_queues(self):
        self.as_user(self.finance_b)
        for url in ('/api/v1/wallet-refunds/', '/api/v1/wallet-reconciliations/'):
            response = self.client.get(url, {'school_id': self.school_a.id})
            self.assertIn(response.status_code, (403, 404), url)
            self.assertNotIn('requests', getattr(response, 'data', {}) or {})
            self.assertNotIn('cases', getattr(response, 'data', {}) or {})

    # --- own school and foundation scope keep working --------------------
    def test_officer_acts_within_their_own_school(self):
        self.as_user(self.finance_a)
        response = self.client.post(
            f'/api/v1/wallet-refunds/{self.refund.id}/mark-donated/', {'donation_consent': True}, format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.refund_status(), WalletRefundStatus.DONATED)
        response = self.client.post(
            f'/api/v1/wallet-reconciliations/{self.case.id}/write-off/', {'reason': 'x'}, format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.case_status(), WalletReconciliationStatus.WRITTEN_OFF)

    def test_officer_reads_their_own_queues(self):
        self.as_user(self.finance_a)
        self.assertEqual(len(self.client.get('/api/v1/wallet-refunds/', {'school_id': self.school_a.id}).data['requests']), 1)
        self.assertEqual(len(self.client.get('/api/v1/wallet-reconciliations/', {'school_id': self.school_a.id}).data['cases']), 1)

    def test_foundation_admin_acts_in_any_school(self):
        self.as_user(self.chair)
        response = self.client.post(f'/api/v1/wallet-reconciliations/{self.case.id}/write-off/', {'reason': 'x'}, format='json')
        self.assertEqual(response.status_code, 200)
        response = self.client.post(f'/api/v1/wallet-refunds/{self.refund.id}/mark-donated/', {'donation_consent': True}, format='json')
        self.assertEqual(response.status_code, 200)

    def test_unknown_id_is_a_404(self):
        self.as_user(self.finance_a)
        self.assertEqual(self.client.post('/api/v1/wallet-refunds/999999/mark-paid/', {}, format='json').status_code, 404)
