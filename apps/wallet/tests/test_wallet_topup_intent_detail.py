from decimal import Decimal
from django.test import TestCase
from rest_framework.test import APIClient

from apps.identity.models import Guardian, GuardianLink, Person, RoleAssignment, User
from apps.wallet.models import WalletTopupIntentStatus
from apps.wallet.services import create_wallet_topup_intent, get_or_create_wallet
from apps.wallet.tests.test_wallet_core import build_wallet_fixture


class WalletTopupIntentDetailTests(TestCase):
    def setUp(self):
        self.fx = build_wallet_fixture()
        self.student = self.fx['student']
        self.wallet = get_or_create_wallet(self.student)
        self.client = APIClient()

        # Create linked parent user
        self.parent_user = User.objects.create(
            foundation_id=self.fx['foundation'].id,
            phone_e164='+6281200000001',
            email='parent_topup_poll@school.id',
            full_name='Bapak Topup',
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.fx['foundation'].id,
            user=self.parent_user,
            role='parent',
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=self.fx['school'].id,
        )
        parent_person = Person.all_tenants.create(
            foundation_id=self.fx['foundation'].id,
            nik='3471010101019001',
            full_name='Bapak Topup',
        )
        guardian = Guardian.all_tenants.create(
            foundation_id=self.fx['foundation'].id,
            person=parent_person,
            user=self.parent_user,
        )
        GuardianLink.all_tenants.create(
            foundation_id=self.fx['foundation'].id,
            student=self.student,
            guardian=guardian,
            relation=GuardianLink.RELATION_FATHER,
            is_primary=True,
            financial_responsible=True,
        )

        # Create unlinked parent user
        self.unlinked_parent = User.objects.create(
            foundation_id=self.fx['foundation'].id,
            phone_e164='+6281200000002',
            email='unlinked_parent_topup@school.id',
            full_name='Orang Tua Lain',
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.fx['foundation'].id,
            user=self.unlinked_parent,
            role='parent',
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=self.fx['school'].id,
        )

        # Create intent
        self.intent = create_wallet_topup_intent(
            self.wallet, self.student, self.fx['school'], 'VA', Decimal('50000.00'), bank='BCA',
        )

    def test_get_wallet_topup_intent_detail_as_linked_parent(self):
        self.client.force_authenticate(user=self.parent_user)
        res = self.client.get(
            f'/api/v1/wallets/{self.student.id}/topup-intents/{self.intent.id}/',
            HTTP_X_FOUNDATION_ID=str(self.fx['foundation'].id),
        )
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()
        self.assertEqual(data['id'], self.intent.id)
        self.assertEqual(data['method'], 'VA')
        self.assertEqual(data['status'], WalletTopupIntentStatus.PENDING)
        self.assertEqual(data['amount'], '50000.00')
        self.assertEqual(data['va_bank'], 'BCA')
        self.assertTrue(data['va_number'])

    def test_unlinked_parent_gets_404(self):
        self.client.force_authenticate(user=self.unlinked_parent)
        res = self.client.get(
            f'/api/v1/wallets/{self.student.id}/topup-intents/{self.intent.id}/',
            HTTP_X_FOUNDATION_ID=str(self.fx['foundation'].id),
        )
        self.assertEqual(res.status_code, 404)

    def test_cross_tenant_gets_404(self):
        fx_b = build_wallet_fixture(foundation_name="Yayasan Topup B Detail")
        self.client.force_authenticate(user=fx_b['finance_user'])
        res = self.client.get(
            f'/api/v1/wallets/{self.student.id}/topup-intents/{self.intent.id}/',
            HTTP_X_FOUNDATION_ID=str(fx_b['foundation'].id),
        )
        self.assertEqual(res.status_code, 404)

    def test_nonexistent_intent_returns_404(self):
        self.client.force_authenticate(user=self.parent_user)
        res = self.client.get(
            f'/api/v1/wallets/{self.student.id}/topup-intents/999999/',
            HTTP_X_FOUNDATION_ID=str(self.fx['foundation'].id),
        )
        self.assertEqual(res.status_code, 404)
