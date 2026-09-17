import datetime
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.identity.models import Foundation, Guardian, GuardianLink, Person, RoleAssignment, School, Student, User
from apps.identity.rbac import ROLE_CANTEEN_OPERATOR, ROLE_PARENT, SCOPE_SCHOOL, assign_role
from apps.wallet.models import Merchant, MerchantType, POSTerminal, POSTransaction, POSTransactionStatus, Product
from apps.wallet.tests.test_wallet_core import build_wallet_fixture
from educore.middleware.tenancy import set_current_foundation_id


class StudentNutritionSummaryTests(TestCase):
    def setUp(self):
        self.fx = build_wallet_fixture("Yayasan Gizi Anak")
        self.foundation = self.fx['foundation']
        self.school = self.fx['school']
        self.student = self.fx['student']
        set_current_foundation_id(self.foundation.id)

        # Parent setup
        self.parent_user = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6281987654321",
            email="parent@school.id",
            full_name="Ibu Siti",
        )
        self.parent_person = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            nik="3471010101019999",
            full_name="Ibu Siti",
        )
        self.guardian = Guardian.all_tenants.create(
            foundation_id=self.foundation.id,
            person=self.parent_person,
            user=self.parent_user,
        )
        GuardianLink.all_tenants.create(
            foundation_id=self.foundation.id,
            student=self.student,
            guardian=self.guardian,
            relation=GuardianLink.RELATION_MOTHER,
            is_primary=True,
            financial_responsible=True,
        )
        assign_role(
            user=self.parent_user,
            role=ROLE_PARENT,
            scope_type=SCOPE_SCHOOL,
            scope_id=self.school.id,
            foundation_id=self.foundation.id,
        )

        # Canteen operator setup
        self.canteen_user = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6281987654322",
            email="canteen@school.id",
            full_name="Pak Joko Canteen",
        )
        assign_role(
            user=self.canteen_user,
            role=ROLE_CANTEEN_OPERATOR,
            scope_type=SCOPE_SCHOOL,
            scope_id=self.school.id,
            foundation_id=self.foundation.id,
        )

        # Unlinked parent in same foundation
        self.unlinked_parent = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6281987654323",
            email="unlinked@school.id",
            full_name="Pak Bambang",
        )
        assign_role(
            user=self.unlinked_parent,
            role=ROLE_PARENT,
            scope_type=SCOPE_SCHOOL,
            scope_id=self.school.id,
            foundation_id=self.foundation.id,
        )

        # Setup Merchant and Products
        self.merchant = Merchant.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            name="Kantin Sehat Edu",
            type=MerchantType.CANTEEN,
            commission_bps=200,
        )
        self.terminal = POSTerminal.objects.create(
            foundation_id=self.foundation.id,
            merchant=self.merchant,
            device_id="KIOSK-01",
            name="Kiosk 1",
        )
        self.prod_apple = Product.objects.create(
            foundation_id=self.foundation.id,
            merchant=self.merchant,
            sku="FRUIT-01",
            name="Apel Segar",
            price=Decimal('5000.00'),
            category="FRUIT",
            nutrition={'calories': 95, 'sugar_g': 19, 'is_healthy': True},
            allergens=[],
        )
        self.prod_donut = Product.objects.create(
            foundation_id=self.foundation.id,
            merchant=self.merchant,
            sku="SNACK-01",
            name="Donat Cokelat",
            price=Decimal('8000.00'),
            category="SNACK",
            nutrition={'calories': 250, 'sugar_g': 15, 'is_healthy': False},
            allergens=['gluten', 'dairy'],
        )
        self.prod_milk = Product.objects.create(
            foundation_id=self.foundation.id,
            merchant=self.merchant,
            sku="BEV-01",
            name="Susu UHT",
            price=Decimal('6000.00'),
            category="BEVERAGE",
            nutrition={'calories': 130, 'sugar_g': 10, 'is_healthy': True},
            allergens=['dairy'],
        )

        self.client = APIClient()

    def test_nutrition_summary_success_as_parent(self):
        now = timezone.now()
        POSTransaction.objects.create(
            foundation_id=self.foundation.id,
            merchant=self.merchant,
            terminal=self.terminal,
            student=self.student,
            items=[
                {'sku': self.prod_apple.sku, 'name': self.prod_apple.name, 'qty': 2, 'unit_price': '5000.00'},
                {'sku': self.prod_donut.sku, 'name': self.prod_donut.name, 'qty': 1, 'unit_price': '8000.00'},
            ],
            subtotal=Decimal('18000.00'),
            commission=Decimal('360.00'),
            total=Decimal('18000.00'),
            occurred_at=now,
            status=POSTransactionStatus.COMPLETED,
            client_transaction_id="tx-nutr-1",
        )

        self.client.force_authenticate(user=self.parent_user)
        date_str = timezone.localdate().isoformat()
        resp = self.client.get(
            f"/api/v1/students/{self.student.id}/nutrition-summary/?from={date_str}&to={date_str}",
            HTTP_X_FOUNDATION_ID=str(self.foundation.id),
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        # Calories: (95 * 2) + (250 * 1) = 190 + 250 = 440
        self.assertEqual(data['total_calories'], 440)
        # Sugar: (19 * 2) + (15 * 1) = 38 + 15 = 53.00
        self.assertEqual(Decimal(data['total_sugar_g']), Decimal('53.00'))
        self.assertEqual(data['total_items'], 3)
        self.assertEqual(data['healthy_items_count'], 2)  # 2 apples
        self.assertIn('dairy', data['allergens'])
        self.assertIn('gluten', data['allergens'])
        self.assertEqual(len(data['daily_breakdown']), 1)
        self.assertEqual(len(data['items']), 2)

    def test_nutrition_summary_canteen_operator_access(self):
        now = timezone.now()
        POSTransaction.objects.create(
            foundation_id=self.foundation.id,
            merchant=self.merchant,
            terminal=self.terminal,
            student=self.student,
            items=[{'sku': self.prod_milk.sku, 'name': self.prod_milk.name, 'qty': 1, 'unit_price': '6000.00'}],
            subtotal=Decimal('6000.00'),
            commission=Decimal('120.00'),
            total=Decimal('6000.00'),
            occurred_at=now,
            status=POSTransactionStatus.COMPLETED,
            client_transaction_id="tx-nutr-canteen",
        )

        self.client.force_authenticate(user=self.canteen_user)
        resp = self.client.get(
            f"/api/v1/students/{self.student.id}/nutrition-summary/",
            HTTP_X_FOUNDATION_ID=str(self.foundation.id),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['total_calories'], 130)

    def test_nutrition_summary_unlinked_parent_gets_404(self):
        self.client.force_authenticate(user=self.unlinked_parent)
        resp = self.client.get(
            f"/api/v1/students/{self.student.id}/nutrition-summary/",
            HTTP_X_FOUNDATION_ID=str(self.foundation.id),
        )
        self.assertEqual(resp.status_code, 404)

    def test_nutrition_summary_cross_tenant_404(self):
        fx_b = build_wallet_fixture(foundation_name="Yayasan Wallet B")
        self.client.force_authenticate(user=fx_b['finance_user'])
        resp = self.client.get(
            f"/api/v1/students/{self.student.id}/nutrition-summary/",
            HTTP_X_FOUNDATION_ID=str(fx_b['foundation'].id),
        )
        self.assertEqual(resp.status_code, 404)

    def test_nutrition_summary_ignores_voided_and_rejected(self):
        now = timezone.now()
        # Completed
        POSTransaction.objects.create(
            foundation_id=self.foundation.id,
            merchant=self.merchant,
            terminal=self.terminal,
            student=self.student,
            items=[{'sku': self.prod_apple.sku, 'name': self.prod_apple.name, 'qty': 1, 'unit_price': '5000.00'}],
            subtotal=Decimal('5000.00'),
            commission=Decimal('100.00'),
            total=Decimal('5000.00'),
            occurred_at=now,
            status=POSTransactionStatus.COMPLETED,
            client_transaction_id="tx-completed",
        )
        # Voided
        POSTransaction.objects.create(
            foundation_id=self.foundation.id,
            merchant=self.merchant,
            terminal=self.terminal,
            student=self.student,
            items=[{'sku': self.prod_donut.sku, 'name': self.prod_donut.name, 'qty': 10, 'unit_price': '8000.00'}],
            subtotal=Decimal('80000.00'),
            commission=Decimal('1600.00'),
            total=Decimal('80000.00'),
            occurred_at=now,
            status=POSTransactionStatus.VOIDED,
            client_transaction_id="tx-voided",
        )
        # Rejected
        POSTransaction.objects.create(
            foundation_id=self.foundation.id,
            merchant=self.merchant,
            terminal=self.terminal,
            student=self.student,
            items=[{'sku': self.prod_donut.sku, 'name': self.prod_donut.name, 'qty': 10, 'unit_price': '8000.00'}],
            subtotal=Decimal('80000.00'),
            commission=Decimal('1600.00'),
            total=Decimal('80000.00'),
            occurred_at=now,
            status=POSTransactionStatus.REJECTED,
            client_transaction_id="tx-rejected",
        )

        self.client.force_authenticate(user=self.parent_user)
        resp = self.client.get(
            f"/api/v1/students/{self.student.id}/nutrition-summary/",
            HTTP_X_FOUNDATION_ID=str(self.foundation.id),
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['total_calories'], 95)  # only the 1 apple
        self.assertEqual(data['total_items'], 1)

    def test_nutrition_summary_date_validation(self):
        self.client.force_authenticate(user=self.parent_user)
        # Invalid date format
        resp = self.client.get(
            f"/api/v1/students/{self.student.id}/nutrition-summary/?from=invalid-date",
            HTTP_X_FOUNDATION_ID=str(self.foundation.id),
        )
        self.assertEqual(resp.status_code, 400)

        # from > to
        resp = self.client.get(
            f"/api/v1/students/{self.student.id}/nutrition-summary/?from=2026-09-20&to=2026-09-10",
            HTTP_X_FOUNDATION_ID=str(self.foundation.id),
        )
        self.assertEqual(resp.status_code, 400)
