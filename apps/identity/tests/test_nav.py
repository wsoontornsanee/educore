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
        self.assertNotIn('attendance', item_ids)
        self.assertIn('permission_slips', item_ids)

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
        requires_staff_profile) — regardless of NAV_GROUPS' 16 items."""
        school_2 = School.objects.create(
            foundation_id=self.foundation.id, name='S2', npsn='87654321', level=School.LEVEL_SMA,
        )
        RoleAssignment.objects.create(
            foundation_id=self.foundation.id, user=self.teacher, role=RoleAssignment.ROLE_TEACHER,
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=school_2.id,
        )
        with self.assertNumQueries(5):
            get_nav_for_user(self.teacher, self.foundation.id)
