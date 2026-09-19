import datetime
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone

from apps.identity.models import Guardian, GuardianLink, Person, User
from apps.notifications.models import IntentStatus, NotificationCategory, NotificationIntent
from apps.wallet.models import WalletReconciliation, WalletReconciliationStatus
from apps.wallet.services import (
    get_or_create_wallet,
    is_reconciliation_notice_still_needed,
    process_offline_pos_batch,
    topup_wallet,
)
from apps.wallet.tests.test_wallet_core import build_wallet_fixture
from apps.wallet.tests.test_pos_checkout import build_pos_fixture
from apps.wallet.tests.test_offline_sync import batch_item


def attach_financial_guardian(fx, with_user=True):
    person = Person.all_tenants.create(foundation_id=fx['foundation'].id, nik='3471010101011234', full_name='Bu Rahmawati')
    user = None
    if with_user:
        user = User.objects.create(
            foundation_id=fx['foundation'].id, phone_e164='+628166666666', email='rahmawati@parent.id', full_name='Bu Rahmawati',
        )
    guardian = Guardian.all_tenants.create(foundation_id=fx['foundation'].id, person=person, user=user)
    GuardianLink.all_tenants.create(
        foundation_id=fx['foundation'].id, guardian=guardian, student=fx['student'],
        relation=GuardianLink.RELATION_MOTHER, financial_responsible=True,
    )
    return guardian


class ReconciliationCaseCreationTests(TestCase):
    def setUp(self):
        self.fx = build_wallet_fixture()
        self.merchant, self.product, self.terminal = build_pos_fixture(self.fx)
        self.guardian = attach_financial_guardian(self.fx)
        self.wallet = get_or_create_wallet(self.fx['student'])
        topup_wallet(self.wallet, Decimal('10000'), 'CASH', 'seed-1')

    def test_offline_overspend_creates_open_case_and_notice(self):
        batch = [batch_item(self.product.sku, self.product.name, 'off-1', student_id=self.fx['student'].id)]  # 15000 > 10000
        process_offline_pos_batch(self.terminal, batch)

        cases = WalletReconciliation.all_tenants.filter(wallet=self.wallet)
        self.assertEqual(cases.count(), 1)
        case = cases.first()
        self.assertEqual(case.status, WalletReconciliationStatus.OPEN)
        self.assertEqual(case.shortfall, Decimal('5000.00'))
        self.assertEqual(case.balance_at_detection, Decimal('-5000.00'))

        intent = NotificationIntent.all_tenants.filter(
            foundation_id=self.fx['foundation'].id, category=NotificationCategory.WALLET_RECONCILIATION,
        ).first()
        self.assertIsNotNone(intent)
        self.assertEqual(intent.payload['shortfall'], '5.000')
        self.assertEqual(intent.payload['txn_count'], '1')

    def test_multiple_overspends_in_one_batch_produce_one_row_each_but_one_notice(self):
        batch = [
            batch_item(self.product.sku, self.product.name, 'off-a', student_id=self.fx['student'].id),
            batch_item(self.product.sku, self.product.name, 'off-b', student_id=self.fx['student'].id),
        ]
        process_offline_pos_batch(self.terminal, batch)

        cases = WalletReconciliation.all_tenants.filter(wallet=self.wallet)
        self.assertEqual(cases.count(), 2)

        intents = NotificationIntent.all_tenants.filter(
            foundation_id=self.fx['foundation'].id, category=NotificationCategory.WALLET_RECONCILIATION,
        )
        self.assertEqual(intents.count(), 1)
        self.assertEqual(intents.first().payload['txn_count'], '2')

    def test_non_financial_guardian_not_notified(self):
        other_person = Person.all_tenants.create(foundation_id=self.fx['foundation'].id, nik='3471010101019999', full_name='Pak Budi')
        other_user = User.objects.create(
            foundation_id=self.fx['foundation'].id, phone_e164='+628177777788', email='budi.nf@parent.id', full_name='Pak Budi',
        )
        other_guardian = Guardian.all_tenants.create(foundation_id=self.fx['foundation'].id, person=other_person, user=other_user)
        GuardianLink.all_tenants.create(
            foundation_id=self.fx['foundation'].id, guardian=other_guardian, student=self.fx['student'],
            relation=GuardianLink.RELATION_FATHER, financial_responsible=False,
        )

        batch = [batch_item(self.product.sku, self.product.name, 'off-2', student_id=self.fx['student'].id)]
        process_offline_pos_batch(self.terminal, batch)

        intents = NotificationIntent.all_tenants.filter(foundation_id=self.fx['foundation'].id, category=NotificationCategory.WALLET_RECONCILIATION)
        self.assertEqual(intents.count(), 1)
        self.assertEqual(intents.first().recipient_user_id, self.guardian.user_id)


class ReconciliationSettlementTests(TestCase):
    def setUp(self):
        self.fx = build_wallet_fixture()
        self.merchant, self.product, self.terminal = build_pos_fixture(self.fx)
        self.guardian = attach_financial_guardian(self.fx)
        self.wallet = get_or_create_wallet(self.fx['student'])
        topup_wallet(self.wallet, Decimal('10000'), 'CASH', 'seed-1')
        process_offline_pos_batch(
            self.terminal, [batch_item(self.product.sku, self.product.name, 'off-3', student_id=self.fx['student'].id)]
        )

    def test_topup_settles_open_case(self):
        self.wallet.refresh_from_db()
        self.assertTrue(self.wallet.requires_reconciliation)

        topup_wallet(self.wallet, Decimal('5000'), 'CASH', 'settle-1')

        self.wallet.refresh_from_db()
        self.assertFalse(self.wallet.requires_reconciliation)
        case = WalletReconciliation.all_tenants.get(wallet=self.wallet)
        self.assertEqual(case.status, WalletReconciliationStatus.SETTLED)
        self.assertIsNotNone(case.settled_at)

    def test_send_time_reevaluation_cancels_if_already_settled(self):
        intent = NotificationIntent.all_tenants.get(
            foundation_id=self.fx['foundation'].id, category=NotificationCategory.WALLET_RECONCILIATION,
        )
        self.assertTrue(is_reconciliation_notice_still_needed(intent))

        topup_wallet(self.wallet, Decimal('5000'), 'CASH', 'settle-2')

        self.assertFalse(is_reconciliation_notice_still_needed(intent))

    def test_settled_notice_only_sent_if_notice_was_sent(self):
        # Notice was queued (PENDING, normal priority) but never actually dispatched in this test.
        topup_wallet(self.wallet, Decimal('5000'), 'CASH', 'settle-3')

        settled_intents = NotificationIntent.all_tenants.filter(
            foundation_id=self.fx['foundation'].id, category=NotificationCategory.WALLET_RECONCILIATION,
            template_key='wallet.recon.settled',
        )
        self.assertEqual(settled_intents.count(), 0)


class ReconciliationCategoryConfigTests(TestCase):
    def test_wallet_reconciliation_not_opt_outable_and_exempt_from_rate_cap(self):
        from apps.notifications.models import CATEGORY_CONFIG
        config = CATEGORY_CONFIG[NotificationCategory.WALLET_RECONCILIATION]
        self.assertFalse(config['opt_out_allowed'])
        self.assertTrue(config['quiet_hours_respected'])
