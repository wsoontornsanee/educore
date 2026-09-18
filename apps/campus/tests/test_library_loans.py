import datetime

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.academic.tests.base import build_academic_fixture
from apps.campus.models import LibraryItem, LibraryItemType, Loan, LoanBorrowerType, LoanStatus
from apps.campus.services import remind_overdue_loans
from apps.campus.tests.test_behaviour import attach_guardian
from apps.core.models import JobRun
from apps.identity.models import Foundation, School
from apps.notifications.models import NotificationCategory
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id


class LibraryLoanModelTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture("Yayasan Uji Pustaka")
        self.item = LibraryItem.objects.create(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'],
            type=LibraryItemType.BOOK, title="Laskar Pelangi", author="Andrea Hirata",
            copies_total=3, copies_available=2,
        )

    def tearDown(self):
        clear_current_foundation_id()

    def test_loan_defaults_active(self):
        loan = Loan.objects.create(
            foundation_id=self.fx['foundation'].id, item=self.item,
            borrower_type=LoanBorrowerType.STUDENT, borrower_id=self.fx['student'].id,
            due_at=timezone.now() + datetime.timedelta(days=7),
        )
        self.assertEqual(loan.status, LoanStatus.ACTIVE)
        self.assertIsNone(loan.returned_at)


class RemindOverdueLoansTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture("Yayasan Uji Pengingat")
        self.guardian = attach_guardian(self.fx, nik="3471010101018888", full_name="Bu Rina")
        self.item = LibraryItem.objects.create(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'],
            type=LibraryItemType.BOOK, title="Bumi Manusia", author="Pramoedya",
            copies_total=1, copies_available=0,
        )
        self.overdue_loan = Loan.objects.create(
            foundation_id=self.fx['foundation'].id, item=self.item,
            borrower_type=LoanBorrowerType.STUDENT, borrower_id=self.fx['student'].id,
            borrowed_at=timezone.now() - datetime.timedelta(days=14),
            due_at=timezone.now() - datetime.timedelta(days=1),
        )
        self.not_yet_due_loan = Loan.objects.create(
            foundation_id=self.fx['foundation'].id, item=self.item,
            borrower_type=LoanBorrowerType.STAFF, borrower_id=self.fx['teacher'].id,
            due_at=timezone.now() + datetime.timedelta(days=3),
        )

    def tearDown(self):
        clear_current_foundation_id()

    def test_marks_overdue_and_dispatches_intent(self):
        result = remind_overdue_loans(self.fx['foundation'].id)

        self.overdue_loan.refresh_from_db()
        self.assertEqual(self.overdue_loan.status, LoanStatus.OVERDUE)
        self.assertIsNotNone(self.overdue_loan.last_reminded_at)
        self.assertEqual(result['count'], 1)

        self.not_yet_due_loan.refresh_from_db()
        self.assertEqual(self.not_yet_due_loan.status, LoanStatus.ACTIVE)

        from apps.notifications.models import NotificationIntent
        intent = NotificationIntent.objects.get(
            foundation_id=self.fx['foundation'].id, category=NotificationCategory.LIBRARY_LOAN_DUE,
        )
        self.assertEqual(intent.recipient_user_id, self.guardian.user_id)

    def test_second_run_same_day_does_not_duplicate_intent(self):
        remind_overdue_loans(self.fx['foundation'].id)
        remind_overdue_loans(self.fx['foundation'].id)

        from apps.notifications.models import NotificationIntent
        count = NotificationIntent.objects.filter(
            foundation_id=self.fx['foundation'].id, category=NotificationCategory.LIBRARY_LOAN_DUE,
        ).count()
        self.assertEqual(count, 1)

    def test_is_digest_only(self):
        from apps.notifications.models import CATEGORY_CONFIG
        self.assertTrue(CATEGORY_CONFIG[NotificationCategory.LIBRARY_LOAN_DUE]['digest_only'])


class RemindLibraryLoansCommandTests(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.fx = build_academic_fixture("Yayasan Uji Sapu Pustaka")
        self.item = LibraryItem.objects.create(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'],
            type=LibraryItemType.BOOK, title="Sang Pemimpi", author="Andrea Hirata",
            copies_total=1, copies_available=0,
        )
        self.overdue_loan = Loan.objects.create(
            foundation_id=self.fx['foundation'].id, item=self.item,
            borrower_type=LoanBorrowerType.STUDENT, borrower_id=self.fx['student'].id,
            due_at=timezone.now() - datetime.timedelta(days=2),
        )

    def tearDown(self):
        clear_current_foundation_id()

    def test_dry_run_does_not_mutate(self):
        call_command('remind_library_loans', '--dry-run', '--force')

        self.overdue_loan.refresh_from_db()
        self.assertEqual(self.overdue_loan.status, LoanStatus.ACTIVE)

    def test_marks_overdue_across_foundations_without_ambient_context(self):
        clear_current_foundation_id()
        call_command('remind_library_loans', '--force')

        self.overdue_loan.refresh_from_db()
        self.assertEqual(self.overdue_loan.status, LoanStatus.OVERDUE)

        job_run = JobRun.objects.filter(job_name='remind_library_loans').latest('started_at')
        self.assertEqual(job_run.status, JobRun.STATUS_SUCCESS)
        self.assertEqual(job_run.items_processed, 1)

    def test_multi_foundation_isolation(self):
        other_foundation = Foundation.objects.create(
            legal_name="Yayasan Pustaka Lain", brand_name="Pustaka Lain", npwp="02.000.000.0-014.000",
        )
        other_school = School.all_tenants.create(
            foundation_id=other_foundation.id, name="SMA Pustaka Lain", npsn="40100099", level=School.LEVEL_SMA,
        )
        other_item = LibraryItem.all_tenants.create(
            foundation_id=other_foundation.id, school=other_school,
            type=LibraryItemType.EQUIPMENT, title="Mikroskop", copies_total=1, copies_available=0,
        )
        other_loan = Loan.all_tenants.create(
            foundation_id=other_foundation.id, item=other_item,
            borrower_type=LoanBorrowerType.STAFF, borrower_id=999,
            due_at=timezone.now() - datetime.timedelta(days=1),
        )

        clear_current_foundation_id()
        call_command('remind_library_loans', '--force')

        self.overdue_loan.refresh_from_db()
        self.assertEqual(self.overdue_loan.status, LoanStatus.OVERDUE)
        other_loan.refresh_from_db()
        self.assertEqual(other_loan.status, LoanStatus.OVERDUE)

        job_run = JobRun.objects.filter(job_name='remind_library_loans').latest('started_at')
        self.assertEqual(job_run.items_processed, 2)
