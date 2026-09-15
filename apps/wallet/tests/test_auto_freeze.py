from decimal import Decimal
from django.test import TestCase

from apps.identity.models import Student
from apps.wallet.models import WalletStatus
from apps.wallet.services import get_or_create_wallet, sync_wallet_freeze_on_student_status_change
from apps.wallet.tests.test_wallet_core import build_wallet_fixture


class WalletAutoFreezeServiceTests(TestCase):
    def setUp(self):
        self.fx = build_wallet_fixture()
        self.wallet = get_or_create_wallet(self.fx['student'])

    def test_freezes_when_leaving_active(self):
        sync_wallet_freeze_on_student_status_change(self.fx['student'], Student.STATUS_INACTIVE)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.status, WalletStatus.FROZEN)

    def test_unfreezes_on_return_to_active(self):
        sync_wallet_freeze_on_student_status_change(self.fx['student'], Student.STATUS_INACTIVE)
        sync_wallet_freeze_on_student_status_change(self.fx['student'], Student.STATUS_ACTIVE)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.status, WalletStatus.ACTIVE)

    def test_noop_when_no_wallet(self):
        self.wallet.hard_delete()
        # Should not raise even though no wallet remains.
        sync_wallet_freeze_on_student_status_change(self.fx['student'], Student.STATUS_INACTIVE)


class StudentTransitionStatusIntegrationTests(TestCase):
    def setUp(self):
        self.fx = build_wallet_fixture()
        self.student = self.fx['student']
        self.wallet = get_or_create_wallet(self.student)

    def test_transition_status_freezes_wallet(self):
        self.student.transition_status(Student.STATUS_INACTIVE, actor_id=str(self.fx['finance_user'].id))
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.status, WalletStatus.FROZEN)
        self.student.refresh_from_db()
        self.assertEqual(self.student.status, Student.STATUS_INACTIVE)

    def test_transition_status_unfreezes_wallet_on_reactivation(self):
        self.student.transition_status(Student.STATUS_INACTIVE)
        self.student.transition_status(Student.STATUS_ACTIVE)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.status, WalletStatus.ACTIVE)

    def test_transition_status_commits_even_if_wallet_sync_raises(self):
        import apps.wallet.services as wallet_services

        original = wallet_services.sync_wallet_freeze_on_student_status_change
        wallet_services.sync_wallet_freeze_on_student_status_change = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            self.student.transition_status(Student.STATUS_INACTIVE)
        finally:
            wallet_services.sync_wallet_freeze_on_student_status_change = original

        self.student.refresh_from_db()
        self.assertEqual(self.student.status, Student.STATUS_INACTIVE)
