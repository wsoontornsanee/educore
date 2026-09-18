import datetime
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.attendance.models import AttendanceDay, AttendanceStatus
from apps.finance.models import Invoice, InvoiceStatus
from apps.identity.models import Foundation, Person, RoleAssignment, School, Student, User


class ConsoleLandingPagesTests(TestCase):
    def setUp(self):
        self.foundation = Foundation.objects.create(legal_name='F', brand_name='F')
        self.school = School.objects.create(foundation_id=self.foundation.id, name='S', npsn='12345678', level=School.LEVEL_SMA)
        person = Person.objects.create(foundation_id=self.foundation.id, full_name='Student One')
        self.student = Student.objects.create(
            foundation_id=self.foundation.id, school=self.school, person=person,
            nis='0001', status=Student.STATUS_ACTIVE,
        )
        self.user = User.objects.create(
            phone_e164='+6281300000020', full_name='Chair', foundation_id=self.foundation.id,
        )
        self.user.set_password('pw12345')
        self.user.save()
        RoleAssignment.objects.create(
            foundation_id=self.foundation.id, user=self.user, role=RoleAssignment.ROLE_FOUNDATION_ADMIN,
            scope_type=RoleAssignment.SCOPE_FOUNDATION, scope_id=self.foundation.id,
        )
        self.client.force_login(self.user)

    def test_foundation_overview_shows_real_student_count(self):
        response = self.client.get(reverse('console-home-overview'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['stats']['student_count'], 1)

    def test_finance_billing_shows_overdue_invoice_count(self):
        Invoice.objects.create(
            foundation_id=self.foundation.id, school=self.school, student=self.student,
            number='INV/S1/2026/000001', period='2026-08',
            issue_date=timezone.now().date() - datetime.timedelta(days=40),
            due_date=timezone.now().date() - datetime.timedelta(days=10),
            status=InvoiceStatus.ISSUED,
        )
        response = self.client.get(reverse('console-home-billing'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['stats']['overdue_invoice_count'], 1)

    def test_teacher_agenda_page_renders(self):
        response = self.client.get(reverse('console-home-agenda'))
        self.assertEqual(response.status_code, 200)

    def test_school_admin_today_shows_alpa_count(self):
        AttendanceDay.objects.create(
            foundation_id=self.foundation.id, school=self.school, student=self.student,
            date=timezone.localdate(), status=AttendanceStatus.ALPA,
        )
        response = self.client.get(reverse('console-home-today'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['stats']['alpa_today_count'], 1)


class ConsoleLandingPagesRbacTests(TestCase):
    """A user without the permission a landing page requires must not see
    that page's aggregate stats by typing the URL directly — they should be
    redirected to web-console-home instead (never a raw 403, per this
    feature's "never leave a user at a dead end" design principle)."""

    def setUp(self):
        self.foundation = Foundation.objects.create(legal_name='F', brand_name='F')
        self.school = School.objects.create(foundation_id=self.foundation.id, name='S', npsn='12345678', level=School.LEVEL_SMA)
        # canteen_operator holds neither grades.read nor finance.invoice.read.
        self.user = User.objects.create(
            phone_e164='+6281300000021', full_name='Canteen', foundation_id=self.foundation.id,
        )
        self.user.set_password('pw12345')
        self.user.save()
        RoleAssignment.objects.create(
            foundation_id=self.foundation.id, user=self.user, role=RoleAssignment.ROLE_CANTEEN_OPERATOR,
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.school.id,
        )
        self.client.force_login(self.user)

    def _assert_redirects_to_console_home(self, url_name):
        response = self.client.get(reverse(url_name))
        self.assertRedirects(response, reverse('web-console-home'))

    def test_foundation_overview_denies_user_without_grades_read(self):
        self._assert_redirects_to_console_home('console-home-overview')

    def test_school_admin_today_denies_user_without_grades_read(self):
        self._assert_redirects_to_console_home('console-home-today')

    def test_teacher_agenda_denies_user_without_grades_read(self):
        self._assert_redirects_to_console_home('console-home-agenda')

    def test_finance_billing_denies_user_without_finance_invoice_read(self):
        self._assert_redirects_to_console_home('console-home-billing')
