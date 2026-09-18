from django.test import TestCase

from apps.identity.models import Foundation, RoleAssignment, School, User
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

    def test_teacher_sees_grades_and_attendance_items_not_finance(self):
        nav = get_nav_for_user(self.teacher, self.foundation.id)
        item_ids = {item['id'] for group in nav for item in group['items']}
        self.assertIn('grading', item_ids)
        self.assertIn('attendance', item_ids)
        self.assertNotIn('recon', item_ids)
        self.assertNotIn('partners', item_ids)

    def test_permissionless_item_always_shown_to_authenticated_user(self):
        nav = get_nav_for_user(self.canteen, self.foundation.id)
        item_ids = {item['id'] for group in nav for item in group['items']}
        self.assertIn('inbox', item_ids)

    def test_empty_groups_are_omitted(self):
        nav = get_nav_for_user(self.canteen, self.foundation.id)
        for group in nav:
            self.assertGreater(len(group['items']), 0)
        group_labels = {g['label'] for g in nav}
        self.assertNotIn('Administrasi', group_labels)
