import datetime
from decimal import Decimal

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.academic.models import (
    ClassGroup, DayOfWeek, Homework, HomeworkSubmission, HomeworkSubmissionStatus, ReportCard, ReportCardStatus,
    SubstitutionStatus, TimetableSubstitution,
)
from apps.academic.services import assign_substitution, create_timetable_slot
from apps.academic.tests.base import build_academic_fixture
from apps.attendance.models import AbsenceRequest, AbsenceRequestStatus
from apps.finance.models import (
    DiscountType, Invoice, InvoiceStatus, InvoiceWriteOffRequest, InvoiceWriteOffStatus, Payment, PaymentMethod,
    PaymentStatus,
)
from apps.finance.services.invoicing import create_discount_with_approval_check
from apps.identity.inbox import ITEMS_PER_SECTION, get_inbox_count, get_inbox_for_user
from apps.identity.models import Person, School, Staff, User
from apps.identity.rbac import (
    ROLE_FINANCE_OFFICER, ROLE_FOUNDATION_ADMIN, ROLE_PARENT, ROLE_SCHOOL_ADMIN, ROLE_TEACHER, SCOPE_FOUNDATION, SCOPE_SCHOOL, assign_role,
)
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id


def _user(fx, phone, name, role=None, scope=SCOPE_SCHOOL, school=None):
    user = User.all_tenants.create_user(foundation_id=fx['foundation'].id, phone_e164=phone, full_name=name)
    if role:
        scope_id = fx['foundation'].id if scope == SCOPE_FOUNDATION else (school or fx['school']).id
        assign_role(user=user, role=role, scope_type=scope, scope_id=scope_id, foundation_id=fx['foundation'].id)
    return user


def _section_ids(user, foundation):
    return [s['id'] for s in get_inbox_for_user(user, foundation.id)]


class InboxTestBase(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        self.foundation = self.fx['foundation']
        self.school = self.fx['school']
        self.admin = _user(self.fx, '+6281290000001', 'Ketua', ROLE_FOUNDATION_ADMIN, scope=SCOPE_FOUNDATION)
        self.school_admin = _user(self.fx, '+6281290000002', 'Admin Sekolah', ROLE_SCHOOL_ADMIN)
        self.teacher = _user(self.fx, '+6281290000003', 'Guru', ROLE_TEACHER)
        self.school_b = School.all_tenants.create(
            foundation_id=self.foundation.id, name='SMA B', npsn='20999999', level=School.LEVEL_SMA,
        )
        self.admin_b = _user(self.fx, '+6281290000004', 'Admin B', ROLE_SCHOOL_ADMIN, school=self.school_b)

    def tearDown(self):
        clear_current_foundation_id()


class FinanceInboxTests(InboxTestBase):
    def test_pending_approval_shown_to_foundation_admin_only(self):
        create_discount_with_approval_check(
            foundation_id=self.foundation.id, student=self.fx['student'], type=DiscountType.FIXED,
            value=Decimal('2000000.00'), reason='Keringanan', valid_from='2026-01-01', user=self.school_admin,
        )
        sections = get_inbox_for_user(self.admin, self.foundation.id)
        self.assertEqual([s['id'] for s in sections], ['finance_approvals'])
        self.assertIn('Andi Wijaya', sections[0]['items'][0].title)
        self.assertEqual(_section_ids(self.school_admin, self.foundation), [])
        self.assertEqual(_section_ids(self.teacher, self.foundation), [])

    def test_write_off_shown_to_foundation_admin_only(self):
        invoice = Invoice.objects.create(
            foundation_id=self.foundation.id, school=self.school, student=self.fx['student'], period='2026-01',
            due_date=datetime.date(2026, 1, 10), subtotal=Decimal('1000000.00'), total=Decimal('1000000.00'),
            status=InvoiceStatus.ISSUED, number='INV/2026/0001',
        )
        InvoiceWriteOffRequest.objects.create(
            foundation_id=self.foundation.id, invoice=invoice, school=self.school, amount=Decimal('800000.00'),
            reason='Pindah', status=InvoiceWriteOffStatus.PENDING, requested_by=self.school_admin,
        )
        sections = get_inbox_for_user(self.admin, self.foundation.id)
        self.assertEqual([s['id'] for s in sections], ['write_offs'])
        self.assertIn('INV/2026/0001', sections[0]['items'][0].title)
        self.assertEqual(_section_ids(self.school_admin, self.foundation), [])


class AbsenceRequestInboxTests(InboxTestBase):
    def setUp(self):
        super().setUp()
        AbsenceRequest.objects.create(
            foundation_id=self.foundation.id, school=self.school, student=self.fx['student'],
            requested_by=self.teacher, date_from=datetime.date(2026, 8, 3), date_to=datetime.date(2026, 8, 4),
            type='SAKIT', reason='Demam', status=AbsenceRequestStatus.PENDING,
        )

    def test_shown_to_holder_of_attendance_write_in_that_school(self):
        for user in (self.admin, self.school_admin):
            self.assertEqual(_section_ids(user, self.foundation), ['absence_requests'], user.full_name)

    def test_teacher_sees_only_requests_of_students_in_their_own_classes(self):
        # Per-teacher class scope: a teacher with no class of this student sees nothing...
        self.assertEqual(_section_ids(self.teacher, self.foundation), [])
        # ...and sees the request once the student is in a class they are homeroom teacher of.
        from apps.academic.tests.base import enroll_in_class_of
        from apps.identity.models import Person, Staff
        person = Person.all_tenants.create(foundation_id=self.foundation.id, full_name='Guru Wali')
        staff = Staff.all_tenants.create(
            foundation_id=self.foundation.id, person=person, user=self.teacher, school=self.school,
            join_date=datetime.date(2020, 1, 1),
        )
        enroll_in_class_of(staff, self.fx['student'], name='Kelas Guru Wali')
        self.assertEqual(_section_ids(self.teacher, self.foundation), ['absence_requests'])

    def test_school_scoped_user_never_sees_another_schools_requests(self):
        self.assertEqual(_section_ids(self.admin_b, self.foundation), [])

    def test_decided_requests_are_not_tasks(self):
        AbsenceRequest.objects.update(status=AbsenceRequestStatus.APPROVED)
        self.assertEqual(_section_ids(self.admin, self.foundation), [])


class ReportCardInboxTests(InboxTestBase):
    def setUp(self):
        super().setUp()
        ReportCard.objects.create(
            foundation_id=self.foundation.id, student=self.fx['student'], term=self.fx['term'],
            class_group=self.fx['class_group'], status=ReportCardStatus.PENDING_REVIEW, is_current=True,
        )

    def test_shown_to_school_config_writers_in_scope_only(self):
        self.assertEqual(_section_ids(self.school_admin, self.foundation), ['report_cards'])
        self.assertEqual(_section_ids(self.admin, self.foundation), ['report_cards'])
        self.assertEqual(_section_ids(self.admin_b, self.foundation), [])
        self.assertEqual(_section_ids(self.teacher, self.foundation), [])  # grades.write, not approve

    def test_draft_and_superseded_cards_are_not_tasks(self):
        ReportCard.objects.update(is_current=False)
        self.assertEqual(_section_ids(self.school_admin, self.foundation), [])


class SubstitutionInboxTests(InboxTestBase):
    def setUp(self):
        super().setUp()
        slot = create_timetable_slot(
            class_subject=self.fx['class_subject'], day_of_week=DayOfWeek.MONDAY, period_no=1,
            start_time=datetime.time(7, 0), end_time=datetime.time(7, 40), room='R1',
        )
        person = Person.all_tenants.create(foundation_id=self.foundation.id, nik='3471010101019999', full_name='Pak Sub')
        self.sub_user = _user(self.fx, '+6281290000005', 'Pak Sub')
        self.sub_staff = Staff.all_tenants.create(
            foundation_id=self.foundation.id, person=person, user=self.sub_user, school=self.school,
            employment_type=Staff.TYPE_PERMANENT, join_date=datetime.date(2026, 1, 1),
        )
        self.substitution = assign_substitution(
            slot=slot, date=timezone.localdate() + datetime.timedelta(days=7), substitute_teacher=self.sub_staff,
        )

    def test_shown_only_to_the_assigned_substitute(self):
        sections = get_inbox_for_user(self.sub_user, self.foundation.id)
        self.assertEqual([s['id'] for s in sections], ['substitutions'])
        self.assertIn('Matematika', sections[0]['items'][0].title)
        self.assertEqual(_section_ids(self.fx['teacher_user'], self.foundation), [])
        self.assertEqual(_section_ids(self.admin, self.foundation), [])

    def test_answered_or_past_substitutions_are_not_tasks(self):
        TimetableSubstitution.objects.filter(id=self.substitution.id).update(status=SubstitutionStatus.ACCEPTED)
        self.assertEqual(_section_ids(self.sub_user, self.foundation), [])
        TimetableSubstitution.objects.filter(id=self.substitution.id).update(
            status=SubstitutionStatus.PENDING, date=timezone.localdate() - datetime.timedelta(days=1),
        )
        self.assertEqual(_section_ids(self.sub_user, self.foundation), [])


class InboxIsolationTests(InboxTestBase):
    def test_another_foundations_pending_items_never_leak(self):
        other = build_academic_fixture(foundation_name='Yayasan Lain')
        AbsenceRequest.objects.create(
            foundation_id=other['foundation'].id, school=other['school'], student=other['student'],
            requested_by=other['teacher_user'], date_from=datetime.date(2026, 8, 3),
            date_to=datetime.date(2026, 8, 3), type='IZIN', reason='Keluarga', status=AbsenceRequestStatus.PENDING,
        )
        set_current_foundation_id(self.foundation.id)
        self.assertEqual(get_inbox_for_user(self.admin, self.foundation.id), [])

    def test_section_rows_are_capped_but_total_is_real(self):
        for i in range(ITEMS_PER_SECTION + 3):
            AbsenceRequest.objects.create(
                foundation_id=self.foundation.id, school=self.school, student=self.fx['student'],
                requested_by=self.teacher, date_from=datetime.date(2026, 8, 3), date_to=datetime.date(2026, 8, 3),
                type='SAKIT', reason=f'r{i}', status=AbsenceRequestStatus.PENDING,
            )
        section = get_inbox_for_user(self.admin, self.foundation.id)[0]
        self.assertEqual(section['total'], ITEMS_PER_SECTION + 3)
        self.assertEqual(len(section['items']), ITEMS_PER_SECTION)


class ConsoleInboxViewTests(InboxTestBase):
    def test_anonymous_redirected_to_login(self):
        response = self.client.get(reverse('console-inbox'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/web/login/', response.url)

    def test_empty_state(self):
        self.client.force_login(self.teacher)
        response = self.client.get(reverse('console-inbox'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Tidak ada tugas')

    def test_guardian_only_account_is_sent_home(self):
        parent = _user(self.fx, '+6281290000005', 'Wali', ROLE_PARENT, scope=SCOPE_FOUNDATION)
        self.client.force_login(parent)
        response = self.client.get(reverse('console-inbox'))
        self.assertRedirects(response, reverse('web-console-home'), fetch_redirect_response=False)

    def test_renders_sections_and_items(self):
        AbsenceRequest.objects.create(
            foundation_id=self.foundation.id, school=self.school, student=self.fx['student'],
            requested_by=self.teacher, date_from=datetime.date(2026, 8, 3), date_to=datetime.date(2026, 8, 4),
            type='SAKIT', reason='Demam tinggi', status=AbsenceRequestStatus.PENDING,
        )
        self.client.force_login(self.school_admin)
        response = self.client.get(reverse('console-inbox'))
        self.assertContains(response, 'Izin &amp; sakit siswa')
        self.assertContains(response, 'Andi Wijaya')
        self.assertContains(response, 'Demam tinggi')


class ConsoleInboxEnglishTests(InboxTestBase):
    def test_section_labels_and_empty_state_translate(self):
        self.client.force_login(self.teacher)
        self.client.cookies['django_language'] = 'en'
        response = self.client.get(reverse('console-inbox'))
        self.assertContains(response, 'Nothing is waiting on you right now.')


class InboxBadgeTests(InboxTestBase):
    def _pending_absence(self, n=1):
        for i in range(n):
            AbsenceRequest.objects.create(
                foundation_id=self.foundation.id, school=self.school, student=self.fx['student'],
                requested_by=self.teacher, date_from=datetime.date(2026, 8, 3), date_to=datetime.date(2026, 8, 3),
                type='SAKIT', reason=f'r{i}', status=AbsenceRequestStatus.PENDING,
            )

    def test_count_sums_every_source_including_beyond_the_row_cap(self):
        self._pending_absence(ITEMS_PER_SECTION + 5)
        ReportCard.objects.create(
            foundation_id=self.foundation.id, student=self.fx['student'], term=self.fx['term'],
            class_group=self.fx['class_group'], status=ReportCardStatus.PENDING_REVIEW, is_current=True,
        )
        self.assertEqual(get_inbox_count(self.admin, self.foundation.id), ITEMS_PER_SECTION + 6)
        self.assertEqual(get_inbox_count(self.school_admin, self.foundation.id), ITEMS_PER_SECTION + 6)
        self.assertEqual(get_inbox_count(self.teacher, self.foundation.id), 0)  # teacher has no classes

    def test_nav_shows_badge_only_when_something_is_pending(self):
        self.client.force_login(self.school_admin)
        self.assertNotContains(self.client.get(reverse('console-inbox')), 'tugas menunggu')
        self._pending_absence(2)
        response = self.client.get(reverse('console-inbox'))
        self.assertContains(response, '>2</span>')

    def test_badge_is_capped(self):
        self._pending_absence(100)
        self.client.force_login(self.school_admin)
        self.assertContains(self.client.get(reverse('console-inbox')), '>99+</span>')

    def test_badge_query_is_lazy(self):
        """A page whose template never reads inbox_badge must not pay for
        the count queries."""
        from django.test import RequestFactory
        from apps.identity.context_processors import console_nav

        request = RequestFactory().get('/')
        request.user = self.school_admin
        with CaptureQueriesContext(connection) as queries:
            console_nav(request)
        self.assertFalse([q for q in queries if 'absence_requests' in q['sql']])


class ManualTransferInboxTests(InboxTestBase):
    def setUp(self):
        super().setUp()
        self.finance = _user(self.fx, '+6281290000006', 'Keuangan', ROLE_FINANCE_OFFICER)
        Payment.objects.create(
            foundation_id=self.foundation.id, school=self.school, student=self.fx['student'],
            amount=Decimal('500000.00'), net=Decimal('500000.00'), method=PaymentMethod.MANUAL,
            channel='MANUAL_TRANSFER', reference='PAY/T/2026/000001', status=PaymentStatus.PENDING_VERIFICATION,
        )

    def test_shown_to_invoice_writers_in_that_school_only(self):
        for user in (self.finance, self.admin):
            self.assertEqual(_section_ids(user, self.foundation), ['manual_transfers'], user.full_name)
        self.assertEqual(_section_ids(self.school_admin, self.foundation), [])  # invoice.read only
        self.assertEqual(_section_ids(self.teacher, self.foundation), [])
        self.assertIn('PAY/T/2026/000001', get_inbox_for_user(self.finance, self.foundation.id)[0]['items'][0].title)

    def test_verified_transfers_are_not_tasks(self):
        Payment.objects.update(status=PaymentStatus.SETTLED)
        self.assertEqual(_section_ids(self.finance, self.foundation), [])

    def test_finance_officer_of_another_school_does_not_see_it(self):
        other = _user(self.fx, '+6281290000007', 'Keuangan B', ROLE_FINANCE_OFFICER, school=self.school_b)
        self.assertEqual(_section_ids(other, self.foundation), [])


class HomeworkToGradeInboxTests(InboxTestBase):
    def setUp(self):
        super().setUp()
        now = timezone.now()
        self.homework = Homework.objects.create(
            foundation_id=self.foundation.id, class_subject=self.fx['class_subject'], title='Latihan Aljabar',
            assigned_at=now - datetime.timedelta(days=5), due_at=now - datetime.timedelta(days=1),
        )
        self.submission = HomeworkSubmission.objects.create(
            foundation_id=self.foundation.id, homework=self.homework, student=self.fx['student'],
            submitted_at=now - datetime.timedelta(days=2), status=HomeworkSubmissionStatus.SUBMITTED,
        )

    def test_shown_only_to_the_teacher_of_that_class(self):
        owner = self.fx['teacher_user']
        sections = get_inbox_for_user(owner, self.foundation.id)
        self.assertEqual([s['id'] for s in sections], ['homework_to_grade'])
        self.assertIn('Latihan Aljabar', sections[0]['items'][0].title)
        self.assertEqual(_section_ids(self.teacher, self.foundation), [])  # a teacher, but not of this class
        self.assertEqual(_section_ids(self.admin, self.foundation), [])

    def test_late_submissions_count_too(self):
        HomeworkSubmission.objects.update(status=HomeworkSubmissionStatus.LATE)
        self.assertEqual(_section_ids(self.fx['teacher_user'], self.foundation), ['homework_to_grade'])

    def test_graded_returned_and_not_yet_due_are_not_tasks(self):
        HomeworkSubmission.objects.update(status=HomeworkSubmissionStatus.GRADED)
        self.assertEqual(_section_ids(self.fx['teacher_user'], self.foundation), [])
        HomeworkSubmission.objects.update(status=HomeworkSubmissionStatus.SUBMITTED)
        Homework.objects.update(due_at=timezone.now() + datetime.timedelta(days=1))
        self.assertEqual(_section_ids(self.fx['teacher_user'], self.foundation), [])
