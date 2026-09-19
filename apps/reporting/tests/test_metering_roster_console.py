"""Administrasi > Siswa terhitung > daftar: the drill-down from a school's count to the students behind it."""
import datetime
from unittest import mock

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.identity.models import Foundation, Person, RoleAssignment, School, Student, User
from apps.reporting.models import RptActiveStudent, RptActiveStudentRoster

NOW = timezone.now()
CURRENT = NOW.date().replace(day=1)
PREVIOUS = (CURRENT - datetime.timedelta(days=1)).replace(day=1)
NISN = '0099887766'


class _Base(TestCase):
    def setUp(self):
        self.foundation = Foundation.objects.create(legal_name='F', brand_name='F')
        self.school_a = School.objects.create(
            foundation_id=self.foundation.id, name='SMA Alpha', npsn='11111111', level=School.LEVEL_SMA,
        )
        self.school_b = School.objects.create(
            foundation_id=self.foundation.id, name='SMP Beta', npsn='22222222', level=School.LEVEL_SMP,
        )
        self.page = reverse('admin-metering')
        self.roster_url = reverse('admin-metering-roster')

    def user(self, role, scope_type, scope_id, phone):
        user = User.objects.create(phone_e164=phone, full_name=f'{role} {phone}', foundation_id=self.foundation.id)
        RoleAssignment.objects.create(
            foundation_id=self.foundation.id, user=user, role=role, scope_type=scope_type, scope_id=scope_id,
        )
        return user

    def admin(self):
        return self.user(
            RoleAssignment.ROLE_FOUNDATION_ADMIN, RoleAssignment.SCOPE_FOUNDATION, self.foundation.id, '+6281300003001',
        )

    def student(self, school, name, nis, status=Student.STATUS_ACTIVE):
        person = Person.all_tenants.create(foundation_id=self.foundation.id, full_name=name)
        return Student.all_tenants.create(
            foundation_id=self.foundation.id, school=school, person=person, nis=nis, nisn=NISN, status=status,
        )

    def counted(self, school, month, students, with_roster=True):
        RptActiveStudent.all_tenants.create(
            foundation_id=self.foundation.id, school=school, month=month, active_count=len(students), computed_at=NOW,
        )
        if with_roster:
            RptActiveStudentRoster.all_tenants.create(
                foundation_id=self.foundation.id, school=school, month=month,
                student_ids=sorted(s.id for s in students), captured_at=NOW,
            )

    def get(self, user, url, **params):
        self.client.force_login(user)
        return self.client.get(url, params)


class RosterLinkOnStatementPageTests(_Base):
    def test_a_school_with_a_roster_links_to_it_for_the_month_shown(self):
        self.counted(self.school_a, CURRENT, [self.student(self.school_a, 'Budi', '2026001')])
        response = self.get(self.admin(), self.page, month=f'{CURRENT:%Y-%m}')
        self.assertContains(response, 'Lihat daftar')
        self.assertContains(response, f'{self.roster_url}?school_id={self.school_a.id}&amp;month={CURRENT:%Y-%m}')

    def test_a_frozen_month_with_no_roster_says_it_is_unavailable_instead_of_linking(self):
        self.counted(self.school_a, PREVIOUS, [self.student(self.school_a, 'Budi', '2026001')], with_roster=False)
        response = self.get(self.admin(), self.page, month=f'{PREVIOUS:%Y-%m}')
        self.assertContains(response, 'Tidak tersedia')
        self.assertNotContains(response, 'Lihat daftar')

    def test_a_school_not_yet_counted_has_no_link_and_no_claim(self):
        response = self.get(self.admin(), self.page, month=f'{CURRENT:%Y-%m}')
        self.assertNotContains(response, 'Lihat daftar')
        self.assertNotContains(response, 'Tidak tersedia')

    def test_no_link_when_the_viewer_may_not_see_student_records_for_that_school(self):
        self.counted(self.school_a, CURRENT, [self.student(self.school_a, 'Budi', '2026001')])
        with mock.patch('apps.reporting.web_views.accessible_school_ids_for_all', return_value=set()):
            response = self.get(self.admin(), self.page, month=f'{CURRENT:%Y-%m}')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Lihat daftar')


class MeteringRosterPageTests(_Base):
    def test_lists_name_nis_and_current_status_but_never_nisn(self):
        active = self.student(self.school_a, 'Budi Santoso', '2026001')
        leaver = self.student(self.school_a, 'Pindah Sekolah', '2026002')
        self.counted(self.school_a, CURRENT, [active, leaver])
        leaver.status = Student.STATUS_TRANSFERRED_OUT
        leaver.save()
        response = self.get(self.admin(), self.roster_url, school_id=self.school_a.id, month=f'{CURRENT:%Y-%m}')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Budi Santoso')
        self.assertContains(response, '2026001')
        self.assertContains(response, 'Pindah Sekolah')
        self.assertContains(response, 'Pindah keluar')  # current status of someone counted earlier
        self.assertNotContains(response, NISN)
        self.assertEqual(response.content.decode().count('data-testid="roster-row"'), 2)

    def test_shows_the_count_and_a_way_back_to_the_same_month(self):
        self.counted(self.school_a, CURRENT, [self.student(self.school_a, 'Budi', '2026001')])
        response = self.get(self.admin(), self.roster_url, school_id=self.school_a.id, month=f'{CURRENT:%Y-%m}')
        self.assertContains(response, 'data-testid="roster-count">1<')
        self.assertContains(response, f'{self.page}?month={CURRENT:%Y-%m}')

    def test_a_frozen_month_without_a_roster_says_so_and_lists_nobody(self):
        self.counted(self.school_a, PREVIOUS, [self.student(self.school_a, 'Budi', '2026001')], with_roster=False)
        response = self.get(self.admin(), self.roster_url, school_id=self.school_a.id, month=f'{PREVIOUS:%Y-%m}')
        self.assertContains(response, 'data-testid="roster-unavailable"')
        self.assertNotContains(response, 'data-testid="roster-row"')
        self.assertNotContains(response, 'Budi')

    def test_a_month_never_counted_says_so(self):
        response = self.get(self.admin(), self.roster_url, school_id=self.school_a.id, month=f'{PREVIOUS:%Y-%m}')
        self.assertContains(response, 'belum dihitung')
        self.assertContains(response, 'data-testid="roster-unavailable"')

    def test_an_invalid_month_falls_back_to_the_current_month_and_says_so(self):
        self.counted(self.school_a, CURRENT, [self.student(self.school_a, 'Budi', '2026001')])
        response = self.get(self.admin(), self.roster_url, school_id=self.school_a.id, month='nonsense')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Format bulan tidak valid')
        self.assertContains(response, 'Budi')

    def test_a_school_admin_sees_only_their_own_school(self):
        self.counted(self.school_a, CURRENT, [self.student(self.school_a, 'Budi', '2026001')])
        self.counted(self.school_b, CURRENT, [self.student(self.school_b, 'Ani', '2026009')])
        school_admin = self.user(
            RoleAssignment.ROLE_SCHOOL_ADMIN, RoleAssignment.SCOPE_SCHOOL, self.school_a.id, '+6281300003002',
        )
        own = self.get(school_admin, self.roster_url, school_id=self.school_a.id)
        self.assertEqual(own.status_code, 200)
        other = self.get(school_admin, self.roster_url, school_id=self.school_b.id)
        self.assertEqual(other.status_code, 404)
        self.assertNotContains(other, 'Ani', status_code=404)

    def test_another_foundations_school_is_not_found(self):
        other = Foundation.objects.create(legal_name='Lain', brand_name='Lain')
        foreign = School.objects.create(foundation_id=other.id, name='SD Lain', npsn='33333333', level=School.LEVEL_SD)
        self.assertEqual(self.get(self.admin(), self.roster_url, school_id=foreign.id).status_code, 404)

    def test_a_missing_or_non_numeric_school_is_not_found(self):
        admin = self.admin()
        self.assertEqual(self.get(admin, self.roster_url).status_code, 404)
        self.assertEqual(self.get(admin, self.roster_url, school_id='abc').status_code, 404)
        self.assertEqual(self.get(admin, self.roster_url, school_id=999999).status_code, 404)

    def test_a_role_without_reporting_read_is_sent_home(self):
        teacher = self.user(
            RoleAssignment.ROLE_TEACHER, RoleAssignment.SCOPE_SCHOOL, self.school_a.id, '+6281300003003',
        )
        response = self.get(teacher, self.roster_url, school_id=self.school_a.id)
        self.assertRedirects(response, reverse('web-console-home'), fetch_redirect_response=False)

    def test_a_viewer_without_student_records_for_the_school_is_refused(self):
        self.counted(self.school_a, CURRENT, [self.student(self.school_a, 'Budi', '2026001')])
        with mock.patch('apps.reporting.web_views.accessible_school_ids_for_all', return_value=set()):
            response = self.get(self.admin(), self.roster_url, school_id=self.school_a.id)
        self.assertEqual(response.status_code, 404)

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(self.roster_url, {'school_id': self.school_a.id})
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response['Location'])
