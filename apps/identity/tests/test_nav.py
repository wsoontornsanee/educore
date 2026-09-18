from django.test import TestCase
from django.utils import timezone

from apps.identity.models import Foundation, Person, RoleAssignment, School, Staff, User
from apps.identity.nav import get_nav_for_user


class GetNavForUserTests(TestCase):
    def setUp(self):
        self.foundation = Foundation.objects.create(legal_name='F', brand_name='F')
        self.school = School.objects.create(foundation_id=self.foundation.id, name='S', npsn='12345678', level=School.LEVEL_SMA)
        self.teacher = User.objects.create(
            phone_e164='+6281300000001', full_name='Teacher One', foundation_id=self.foundation.id,
        )
        RoleAssignment.objects.create(
            foundation_id=self.foundation.id, user=self.teacher, role=RoleAssignment.ROLE_TEACHER,
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.school.id,
        )
        self.canteen = User.objects.create(
            phone_e164='+6281300000002', full_name='Canteen One', foundation_id=self.foundation.id,
        )
        RoleAssignment.objects.create(
            foundation_id=self.foundation.id, user=self.canteen, role=RoleAssignment.ROLE_CANTEEN_OPERATOR,
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.school.id,
        )

    def test_teacher_without_permission_does_not_see_finance_items(self):
        nav = get_nav_for_user(self.teacher, self.foundation.id)
        item_ids = {item['id'] for group in nav for item in group['items']}
        self.assertNotIn('recon', item_ids)
        self.assertNotIn('partners', item_ids)

    def test_permission_slips_hidden_without_a_linked_staff_profile(self):
        """Regression test: PermissionSlipConsolePageView 404s
        ("Akun ini tidak terhubung ke profil staf") for a permission-holding
        user with no Staff row -- the nav must not show a link that then
        404s on click. self.teacher has grades.read but (in this test) no
        Staff profile."""
        nav = get_nav_for_user(self.teacher, self.foundation.id)
        item_ids = {item['id'] for group in nav for item in group['items']}
        self.assertNotIn('permission_slips', item_ids)

    def test_permission_slips_shown_with_a_linked_staff_profile(self):
        person = Person.objects.create(foundation_id=self.foundation.id, full_name='Teacher One')
        Staff.objects.create(
            foundation_id=self.foundation.id, person=person, user=self.teacher, school=self.school,
            join_date=timezone.localdate(),
        )
        nav = get_nav_for_user(self.teacher, self.foundation.id)
        item_ids = {item['id'] for group in nav for item in group['items']}
        self.assertIn('permission_slips', item_ids)

    def test_roster_and_schedule_require_staff_profile_and_permission(self):
        """Siswa & kelas resolves to a real page that 404s without a Staff
        profile (a guardian holds student_records.read too), so the nav must
        hide it for a permission-holding user with no Staff row."""
        nav = get_nav_for_user(self.teacher, self.foundation.id)
        ids = {item['id'] for group in nav for item in group['items']}
        self.assertFalse({'roster', 'schedule'} & ids)

        person = Person.objects.create(foundation_id=self.foundation.id, full_name='Teacher One')
        Staff.objects.create(
            foundation_id=self.foundation.id, person=person, user=self.teacher, school=self.school,
            join_date=timezone.localdate(),
        )
        nav = get_nav_for_user(self.teacher, self.foundation.id)
        akademik = next(group for group in nav if str(group['label']) == 'Akademik')
        roster = next(item for item in akademik['items'] if item['id'] == 'roster')
        self.assertEqual(roster['url_name'], 'academic-class-list-page')
        schedule = next(item for item in akademik['items'] if item['id'] == 'schedule')
        self.assertEqual(schedule['url_name'], 'academic-timetable-page')

    def test_coming_soon_items_are_hidden_even_with_permission(self):
        """Regression test (2026-09-19): a coming_soon item must never
        render, even for a user who holds its RBAC permission -- clicking
        it only ever reached a "Modul ini belum tersedia" placeholder,
        which read as broken/no-access rather than simply unbuilt."""
        person = Person.objects.create(foundation_id=self.foundation.id, full_name='Teacher One')
        Staff.objects.create(
            foundation_id=self.foundation.id, person=person, user=self.teacher, school=self.school,
            join_date=timezone.localdate(),
        )
        nav = get_nav_for_user(self.teacher, self.foundation.id)
        item_ids = {item['id'] for group in nav for item in group['items']}
        self.assertIn('inbox', item_ids)
        self.assertNotIn('grading', item_ids)
        self.assertNotIn('roster', item_ids)
        self.assertIn('permission_slips', item_ids)

    def test_operasional_pages_shown_only_with_a_linked_staff_profile(self):
        """Kehadiran & gerbang / Mode ujian are real pages that 404 without a
        Staff profile (a guardian holds attendance.read + grades.read too)."""
        operasional = {'attendance', 'exam'}
        nav = get_nav_for_user(self.teacher, self.foundation.id)
        self.assertFalse(operasional & {item['id'] for group in nav for item in group['items']})

        person = Person.objects.create(foundation_id=self.foundation.id, full_name='Teacher One')
        Staff.objects.create(
            foundation_id=self.foundation.id, person=person, user=self.teacher, school=self.school,
            join_date=timezone.localdate(),
        )
        nav = get_nav_for_user(self.teacher, self.foundation.id)
        items = {item['id']: item['url_name'] for group in nav for item in group['items']}
        self.assertEqual(items['attendance'], 'attendance-gate-console-page')
        self.assertEqual(items['exam'], 'exam-mode-console-page')

    def test_canteen_page_shown_to_canteen_operator_with_staff_profile(self):
        person = Person.objects.create(foundation_id=self.foundation.id, full_name='Canteen One')
        Staff.objects.create(
            foundation_id=self.foundation.id, person=person, user=self.canteen, school=self.school,
            join_date=timezone.localdate(),
        )
        nav = get_nav_for_user(self.canteen, self.foundation.id)
        items = {item['id']: item['url_name'] for group in nav for item in group['items']}
        self.assertEqual(items['canteen'], 'canteen-console-page')

    def test_empty_groups_are_omitted(self):
        nav = get_nav_for_user(self.canteen, self.foundation.id)
        for group in nav:
            self.assertGreater(len(group['items']), 0)
        group_labels = {str(g['label']) for g in nav}
        self.assertNotIn('Administrasi', group_labels)
        self.assertIn('Beranda', group_labels)  # inbox is permission-free, so always present

    def test_nav_computation_query_count_is_bounded_not_per_item(self):
        """Regression test for the N+1: computing the nav for a user with 2
        assigned schools must cost a small, fixed number of queries —
        1 (assigned-schools list) + 1 (foundation-scope permissions) +
        2 (one per assigned school) + 1 (the single Staff-profile existence
        check, run once regardless of how many items declare
        requires_staff_profile) + 1 (the single is_foundation_admin check,
        likewise once for all requires_foundation_admin items) — regardless
        of NAV_GROUPS' 16 items."""
        school_2 = School.objects.create(
            foundation_id=self.foundation.id, name='S2', npsn='87654321', level=School.LEVEL_SMA,
        )
        RoleAssignment.objects.create(
            foundation_id=self.foundation.id, user=self.teacher, role=RoleAssignment.ROLE_TEACHER,
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=school_2.id,
        )
        with self.assertNumQueries(6):
            get_nav_for_user(self.teacher, self.foundation.id)


class AdministrasiNavTests(TestCase):
    """The four Administrasi items now have real destinations, each gated by
    its own permission — and the partner-key console additionally requires
    foundation-admin authority (its backing API's actual gate)."""

    def setUp(self):
        self.foundation = Foundation.objects.create(legal_name='F', brand_name='F')
        self.school = School.objects.create(foundation_id=self.foundation.id, name='S', npsn='12345678', level=School.LEVEL_SMA)

    def _nav_urls(self, role, scope_type, scope_id):
        user = User.objects.create(phone_e164='+6281300009001', full_name='U', foundation_id=self.foundation.id)
        RoleAssignment.objects.create(
            foundation_id=self.foundation.id, user=user, role=role, scope_type=scope_type, scope_id=scope_id,
        )
        nav = get_nav_for_user(user, self.foundation.id)
        return {item['id']: item['url_name'] for group in nav for item in group['items']}

    def test_foundation_admin_sees_all_four_administrasi_items(self):
        urls = self._nav_urls(
            RoleAssignment.ROLE_FOUNDATION_ADMIN, RoleAssignment.SCOPE_FOUNDATION, self.foundation.id,
        )
        self.assertEqual(urls['staff'], 'admin-staff')
        self.assertEqual(urls['partners'], 'admin-partners')
        self.assertEqual(urls['settings'], 'admin-settings')
        self.assertEqual(urls['audit'], 'admin-audit')

    def test_school_admin_sees_admin_items_except_partner_keys(self):
        urls = self._nav_urls(RoleAssignment.ROLE_SCHOOL_ADMIN, RoleAssignment.SCOPE_SCHOOL, self.school.id)
        self.assertIn('staff', urls)
        self.assertIn('settings', urls)
        self.assertIn('audit', urls)
        self.assertNotIn('partners', urls)
