"""Administrasi > Siswa terhitung: the console page over the metering statement (spec/15 RPT-009)."""
import datetime

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.identity.models import Foundation, RoleAssignment, School, User
from apps.identity.nav import get_nav_for_user
from apps.reporting.models import RptActiveStudent


def _shift_month(month, delta):
    index = month.year * 12 + (month.month - 1) + delta
    return datetime.date(index // 12, index % 12 + 1, 1)


class MeteringConsolePageTests(TestCase):
    def setUp(self):
        self.foundation = Foundation.objects.create(legal_name='F', brand_name='F')
        self.school_a = School.objects.create(
            foundation_id=self.foundation.id, name='SMA Alpha', npsn='11111111', level=School.LEVEL_SMA,
        )
        self.school_b = School.objects.create(
            foundation_id=self.foundation.id, name='SMP Beta', npsn='22222222', level=School.LEVEL_SMP,
        )
        self.current = timezone.now().date().replace(day=1)
        self.previous = _shift_month(self.current, -1)
        self.url = reverse('admin-metering')

    def user(self, role, scope_type, scope_id, phone, foundation=None):
        foundation = foundation or self.foundation
        user = User.objects.create(phone_e164=phone, full_name=f'{role} {phone}', foundation_id=foundation.id)
        RoleAssignment.objects.create(
            foundation_id=foundation.id, user=user, role=role, scope_type=scope_type, scope_id=scope_id,
        )
        return user

    def foundation_admin(self):
        return self.user(
            RoleAssignment.ROLE_FOUNDATION_ADMIN, RoleAssignment.SCOPE_FOUNDATION, self.foundation.id, '+6281300002001',
        )

    def count(self, school, month, active_count, foundation=None):
        return RptActiveStudent.all_tenants.create(
            foundation_id=(foundation or self.foundation).id, school=school, month=month,
            active_count=active_count, computed_at=timezone.now(),
        )

    def get(self, user, **params):
        self.client.force_login(user)
        return self.client.get(self.url, params)

    # -- access ---------------------------------------------------------------------------------

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response['Location'])

    def test_a_role_without_reporting_read_is_sent_home(self):
        teacher = self.user(
            RoleAssignment.ROLE_TEACHER, RoleAssignment.SCOPE_SCHOOL, self.school_a.id, '+6281300002002',
        )
        response = self.get(teacher)
        self.assertRedirects(response, reverse('web-console-home'), fetch_redirect_response=False)

    def test_school_admin_sees_only_their_own_school(self):
        self.count(self.school_a, self.current, 120)
        self.count(self.school_b, self.current, 340)
        admin = self.user(
            RoleAssignment.ROLE_SCHOOL_ADMIN, RoleAssignment.SCOPE_SCHOOL, self.school_a.id, '+6281300002003',
        )
        response = self.get(admin)
        self.assertContains(response, 'SMA Alpha')
        self.assertNotContains(response, 'SMP Beta')
        self.assertEqual(response.context['statement']['total_active'], 120)

    def test_another_foundations_counts_never_appear(self):
        other = Foundation.objects.create(legal_name='Other', brand_name='Other')
        other_school = School.objects.create(
            foundation_id=other.id, name='SMA Rival', npsn='33333333', level=School.LEVEL_SMA,
        )
        self.count(other_school, self.current, 999, foundation=other)
        self.count(self.school_a, self.current, 120)
        response = self.get(self.foundation_admin())
        self.assertNotContains(response, 'SMA Rival')
        self.assertEqual(response.context['statement']['total_active'], 120)

    # -- what the page says ---------------------------------------------------------------------

    def test_lists_counts_state_and_total_for_the_current_month(self):
        self.count(self.school_a, self.current, 120)
        self.count(self.school_b, self.current, 340)
        response = self.get(self.foundation_admin())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'SMA Alpha')
        self.assertContains(response, 'SMP Beta')
        self.assertContains(response, 'Berjalan')  # the running month is still being refreshed
        self.assertContains(response, 'data-testid="metering-total">460<')
        self.assertNotContains(response, 'metering-incomplete')

    def test_a_month_that_has_ended_is_shown_frozen(self):
        self.count(self.school_a, self.previous, 118)
        self.count(self.school_b, self.previous, 300)
        response = self.get(self.foundation_admin(), month=self.previous.strftime('%Y-%m'))
        self.assertContains(response, 'Dibekukan')
        self.assertNotContains(response, 'Berjalan')

    def test_a_school_with_no_count_is_not_computed_never_zero(self):
        self.count(self.school_a, self.current, 120)
        response = self.get(self.foundation_admin())
        self.assertContains(response, 'Belum dihitung')
        self.assertContains(response, 'data-testid="metering-incomplete"')
        self.assertContains(response, 'data-testid="metering-total">120<')  # the uncounted school adds nothing
        entries = {e['school_name']: e for e in response.context['statement']['schools']}
        self.assertIsNone(entries['SMP Beta']['active_count'])

    def test_invalid_month_falls_back_to_the_current_month_and_says_so(self):
        self.count(self.school_a, self.current, 120)
        response = self.get(self.foundation_admin(), month='2026-13')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Format bulan tidak valid')
        self.assertEqual(response.context['month_value'], self.current.strftime('%Y-%m'))

    def test_month_navigation_stops_at_the_current_month(self):
        admin = self.foundation_admin()
        current = self.get(admin)
        self.assertIsNone(current.context['next_month'])
        self.assertEqual(current.context['previous_month'], self.previous.strftime('%Y-%m'))
        earlier = self.get(admin, month=self.previous.strftime('%Y-%m'))
        self.assertEqual(earlier.context['next_month'], self.current.strftime('%Y-%m'))

    def test_a_foundation_with_no_schools_shows_the_empty_state(self):
        empty = Foundation.objects.create(legal_name='Empty', brand_name='Empty')
        admin = self.user(
            RoleAssignment.ROLE_FOUNDATION_ADMIN, RoleAssignment.SCOPE_FOUNDATION, empty.id, '+6281300002004',
            foundation=empty,
        )
        response = self.get(admin)
        self.assertContains(response, 'Belum ada sekolah yang dapat ditampilkan')
        self.assertNotContains(response, 'metering-row')

    def test_page_renders_in_english(self):
        self.count(self.school_a, self.current, 120)  # school_b is not counted, so the notice shows
        self.client.cookies['django_language'] = 'en'
        response = self.get(self.foundation_admin())
        self.assertContains(response, 'Counted students')
        self.assertContains(response, 'Not counted yet')
        self.assertContains(response, 'This total is incomplete: 1 school has not been counted for this month.')

    # -- navigation -----------------------------------------------------------------------------

    def test_nav_offers_the_page_to_roles_that_can_read_reporting_and_not_to_others(self):
        def nav_ids(user):
            return {item['id']: item['url_name'] for g in get_nav_for_user(user, self.foundation.id) for item in g['items']}

        self.assertEqual(nav_ids(self.foundation_admin())['metering'], 'admin-metering')
        teacher = self.user(
            RoleAssignment.ROLE_TEACHER, RoleAssignment.SCOPE_SCHOOL, self.school_a.id, '+6281300002005',
        )
        self.assertNotIn('metering', nav_ids(teacher))
