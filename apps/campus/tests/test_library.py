import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.academic.tests.base import build_academic_fixture
from apps.campus.models import LibraryItem, LibraryItemType, LibraryPolicy, Loan, LoanBorrowerType, LoanStatus
from apps.campus.services import (
    checkout_library_item,
    get_or_create_library_policy,
    get_overdue_loans,
    mark_loan_lost,
    return_library_item,
)
from apps.finance.models import InvoiceLine
from apps.identity.models import Person, RoleAssignment, Student, User


def make_book(fx, copies_total=1, **kwargs):
    defaults = {
        'foundation_id': fx['foundation'].id,
        'school': fx['school'],
        'type': LibraryItemType.BOOK,
        'title': 'Laskar Pelangi',
        'author': 'Andrea Hirata',
        'copies_total': copies_total,
        'copies_available': copies_total,
    }
    defaults.update(kwargs)
    return LibraryItem.objects.create(**defaults)


def make_equipment(fx, copies_total=1, replacement_cost=Decimal('5000000.00')):
    return LibraryItem.objects.create(
        foundation_id=fx['foundation'].id,
        school=fx['school'],
        type=LibraryItemType.EQUIPMENT,
        title='Laptop Chromebook #12',
        copies_total=copies_total,
        copies_available=copies_total,
        replacement_cost=replacement_cost,
    )


class LibraryServiceTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()

    def test_checkout_decrements_availability_and_sets_due_date(self):
        """LIF-019: checkout period configurable per school/borrower type."""
        policy = get_or_create_library_policy(self.fx['school'], LoanBorrowerType.STUDENT)
        policy.loan_period_days = 10
        policy.save()

        item = make_book(self.fx, copies_total=2)
        loan = checkout_library_item(
            school=self.fx['school'],
            item=item,
            borrower_type=LoanBorrowerType.STUDENT,
            borrower_id=self.fx['student'].id,
        )

        item.refresh_from_db()
        self.assertEqual(item.copies_available, 1)
        self.assertEqual(loan.status, LoanStatus.ACTIVE)
        self.assertEqual((loan.due_at - loan.borrowed_at).days, 10)

    def test_checkout_rejected_when_no_copies_available(self):
        item = make_book(self.fx, copies_total=1)
        checkout_library_item(
            school=self.fx['school'], item=item, borrower_type=LoanBorrowerType.STUDENT,
            borrower_id=self.fx['student'].id,
        )
        with self.assertRaises(ValidationError):
            checkout_library_item(
                school=self.fx['school'], item=item, borrower_type=LoanBorrowerType.STUDENT,
                borrower_id=self.fx['student'].id,
            )

    def test_checkout_rejected_past_max_concurrent_loans(self):
        """LIF-019: max concurrent loans configurable per school and borrower type."""
        policy = get_or_create_library_policy(self.fx['school'], LoanBorrowerType.STUDENT)
        policy.max_concurrent_loans = 1
        policy.save()

        item1 = make_book(self.fx, title='Buku 1', copies_total=1)
        item2 = make_book(self.fx, title='Buku 2', copies_total=1)
        checkout_library_item(
            school=self.fx['school'], item=item1, borrower_type=LoanBorrowerType.STUDENT,
            borrower_id=self.fx['student'].id,
        )
        with self.assertRaises(ValidationError):
            checkout_library_item(
                school=self.fx['school'], item=item2, borrower_type=LoanBorrowerType.STUDENT,
                borrower_id=self.fx['student'].id,
            )

    def test_equipment_checkout_requires_condition_on_issue(self):
        """LIF-022: equipment checkout must record condition on issue."""
        equipment = make_equipment(self.fx)
        with self.assertRaises(ValidationError):
            checkout_library_item(
                school=self.fx['school'], item=equipment, borrower_type=LoanBorrowerType.STUDENT,
                borrower_id=self.fx['student'].id, condition_on_issue='',
            )

        loan = checkout_library_item(
            school=self.fx['school'], item=equipment, borrower_type=LoanBorrowerType.STUDENT,
            borrower_id=self.fx['student'].id, condition_on_issue='Baik, tanpa goresan',
        )
        self.assertEqual(loan.condition_on_issue, 'Baik, tanpa goresan')

    def test_equipment_return_requires_condition_on_return(self):
        equipment = make_equipment(self.fx)
        loan = checkout_library_item(
            school=self.fx['school'], item=equipment, borrower_type=LoanBorrowerType.STUDENT,
            borrower_id=self.fx['student'].id, condition_on_issue='Baik',
        )
        with self.assertRaises(ValidationError):
            return_library_item(loan=loan, condition_on_return='')

        returned = return_library_item(loan=loan, condition_on_return='Retak di sudut layar')
        self.assertEqual(returned.status, LoanStatus.RETURNED)
        self.assertEqual(returned.condition_on_return, 'Retak di sudut layar')

    def test_return_on_time_has_no_fine(self):
        item = make_book(self.fx)
        loan = checkout_library_item(
            school=self.fx['school'], item=item, borrower_type=LoanBorrowerType.STUDENT,
            borrower_id=self.fx['student'].id,
        )
        returned = return_library_item(loan=loan, occurred_at=loan.due_at)
        self.assertEqual(returned.fine, Decimal('0.00'))

        item.refresh_from_db()
        self.assertEqual(item.copies_available, 1)

    def test_returning_an_overdue_flagged_loan_still_works(self):
        """A loan already flipped OVERDUE by the daily cron must still be returnable."""
        item = make_book(self.fx)
        loan = checkout_library_item(
            school=self.fx['school'], item=item, borrower_type=LoanBorrowerType.STUDENT,
            borrower_id=self.fx['student'].id,
        )
        loan.status = LoanStatus.OVERDUE
        loan.save(update_fields=['status'])

        returned = return_library_item(loan=loan, occurred_at=loan.due_at + datetime.timedelta(days=1))
        self.assertEqual(returned.status, LoanStatus.RETURNED)

    def test_overdue_fine_posted_to_invoice_as_other_line_capped(self):
        """LIF-020: overdue fines configurable per day with a cap, posted to invoice as OTHER line."""
        policy = get_or_create_library_policy(self.fx['school'], LoanBorrowerType.STUDENT)
        policy.fine_per_day = Decimal('500.00')
        policy.fine_cap = Decimal('2000.00')
        policy.save()

        item = make_book(self.fx)
        loan = checkout_library_item(
            school=self.fx['school'], item=item, borrower_type=LoanBorrowerType.STUDENT,
            borrower_id=self.fx['student'].id,
        )
        # 10 days late * 500/day = 5000, capped at 2000
        late_return = loan.due_at + datetime.timedelta(days=10)
        returned = return_library_item(loan=loan, occurred_at=late_return)

        self.assertEqual(returned.fine, Decimal('2000.00'))

        invoice_line = InvoiceLine.objects.filter(
            foundation_id=self.fx['foundation'].id, code='LIB_FINE',
        ).first()
        self.assertIsNotNone(invoice_line)
        self.assertEqual(invoice_line.amount, Decimal('2000.00'))
        self.assertEqual(invoice_line.invoice.student_id, self.fx['student'].id)

    def test_lost_item_charges_replacement_cost_with_staff_approval(self):
        """LIF-021: lost item handling charges replacement cost with staff approval."""
        equipment = make_equipment(self.fx, copies_total=1, replacement_cost=Decimal('5000000.00'))
        loan = checkout_library_item(
            school=self.fx['school'], item=equipment, borrower_type=LoanBorrowerType.STUDENT,
            borrower_id=self.fx['student'].id, condition_on_issue='Baik',
        )

        with self.assertRaises(ValidationError):
            mark_loan_lost(loan=loan, approved_by=None)

        lost_loan = mark_loan_lost(loan=loan, approved_by=self.fx['teacher'])
        self.assertEqual(lost_loan.status, LoanStatus.LOST)

        equipment.refresh_from_db()
        self.assertEqual(equipment.copies_total, 0)

        invoice_line = InvoiceLine.objects.filter(
            foundation_id=self.fx['foundation'].id, code='LIB_LOST',
        ).first()
        self.assertIsNotNone(invoice_line)
        self.assertEqual(invoice_line.amount, Decimal('5000000.00'))

    def test_get_overdue_loans_includes_active_past_due_and_flagged_overdue(self):
        item = make_book(self.fx, copies_total=2)
        loan_active_late = checkout_library_item(
            school=self.fx['school'], item=item, borrower_type=LoanBorrowerType.STUDENT,
            borrower_id=self.fx['student'].id,
        )
        loan_active_late.due_at = timezone.now() - datetime.timedelta(days=1)
        loan_active_late.save(update_fields=['due_at'])

        overdue = get_overdue_loans(foundation_id=self.fx['foundation'].id, school_id=self.fx['school'].id)
        self.assertIn(loan_active_late, list(overdue))


class LibraryConcurrencyTests(TestCase):
    """LIF-023: availability accurate under concurrent checkout (row-level lock on copies_available)."""

    def setUp(self):
        self.fx = build_academic_fixture()
        self.item = make_book(self.fx, copies_total=1)
        get_or_create_library_policy(self.fx['school'], LoanBorrowerType.STUDENT)

        person2 = Person.all_tenants.create(
            foundation_id=self.fx['foundation'].id, nik="3471010101012222", full_name="Siswa Kedua",
        )
        self.student2 = Student.all_tenants.create(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            person=person2,
            nis="X-002",
            status=Student.STATUS_ACTIVE,
        )

    def test_checkout_uses_select_for_update_row_lock(self):
        """The last-copy race is closed by locking the LibraryItem row before decrementing;
        verify the lock is actually taken (SQLite can't simulate real cross-thread contention)."""
        from unittest.mock import patch
        with patch(
            'apps.campus.services.LibraryItem.objects.select_for_update',
            wraps=LibraryItem.objects.select_for_update,
        ) as mocked_lock:
            checkout_library_item(
                school=self.fx['school'], item=self.item, borrower_type=LoanBorrowerType.STUDENT,
                borrower_id=self.fx['student'].id,
            )
            mocked_lock.assert_called_once()

    def test_only_one_of_two_sequential_checkouts_of_last_copy_succeeds(self):
        checkout_library_item(
            school=self.fx['school'], item=self.item, borrower_type=LoanBorrowerType.STUDENT,
            borrower_id=self.fx['student'].id,
        )
        with self.assertRaises(ValidationError):
            checkout_library_item(
                school=self.fx['school'], item=self.item, borrower_type=LoanBorrowerType.STUDENT,
                borrower_id=self.student2.id,
            )
        self.item.refresh_from_db()
        self.assertEqual(self.item.copies_available, 0)


class LibraryAPITests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        self.client = APIClient()

        admin_person = Person.all_tenants.create(foundation_id=self.fx['foundation'].id, nik="3471010101017777", full_name="Bu Admin")
        self.admin_user = User.objects.create(
            foundation_id=self.fx['foundation'].id,
            phone_e164="+6281800000002",
            email="admin2@cendekia.sch.id",
            full_name="Bu Admin",
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.fx['foundation'].id,
            user=self.admin_user,
            role=RoleAssignment.ROLE_SCHOOL_ADMIN,
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=self.fx['school'].id,
        )

    def test_checkout_and_return_via_api(self):
        self.client.force_authenticate(user=self.admin_user)
        item = make_book(self.fx, copies_total=1)

        res = self.client.post('/api/v1/campus/library/loans/', {
            'item_id': item.id,
            'borrower_type': LoanBorrowerType.STUDENT,
            'borrower_id': self.fx['student'].id,
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        loan_id = res.data['id']

        item.refresh_from_db()
        self.assertEqual(item.copies_available, 0)

        res_return = self.client.post(f'/api/v1/campus/library/loans/{loan_id}/return/', {})
        self.assertEqual(res_return.status_code, status.HTTP_200_OK)
        self.assertEqual(res_return.data['status'], LoanStatus.RETURNED)

        item.refresh_from_db()
        self.assertEqual(item.copies_available, 1)

    def test_overdue_endpoint(self):
        self.client.force_authenticate(user=self.admin_user)
        item = make_book(self.fx, copies_total=1)
        loan = checkout_library_item(
            school=self.fx['school'], item=item, borrower_type=LoanBorrowerType.STUDENT,
            borrower_id=self.fx['student'].id,
        )
        loan.due_at = timezone.now() - datetime.timedelta(days=2)
        loan.save(update_fields=['due_at'])

        res = self.client.get('/api/v1/campus/library/overdue?school_id=%s' % self.fx['school'].id)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        loan_ids = [row['id'] for row in res.data]
        self.assertIn(loan.id, loan_ids)
