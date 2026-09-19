"""Role labels are translatable: Indonesian is the source language, English the translation."""
from django.test import SimpleTestCase
from django.utils.translation import override

from apps.identity.models import PlatformRoleAssignment, RoleAssignment
from apps.identity.rbac_matrix import _role_name

EXPECTED = {
    'foundation_admin': ('Admin Yayasan', 'Foundation Admin'),
    'school_admin': ('Admin Sekolah', 'School Admin'),
    'finance_officer': ('Bendahara', 'Finance Officer'),
    'teacher': ('Guru', 'Teacher'),
    'counsellor': ('Guru BK', 'Counsellor'),
    'canteen_operator': ('Operator Kantin', 'Canteen Operator'),
    'clinic_officer': ('Petugas UKS', 'Clinic Officer'),
    'parent': ('Wali Murid', 'Parent'),
}


class RoleLabelTests(SimpleTestCase):
    def labels(self, language):
        with override(language):
            return {role: str(label) for role, label in RoleAssignment.ROLE_CHOICES}

    def test_every_tenant_role_has_an_indonesian_and_an_english_label(self):
        self.assertEqual(set(EXPECTED), {role for role, _label in RoleAssignment.ROLE_CHOICES})
        self.assertEqual(self.labels('id'), {role: pair[0] for role, pair in EXPECTED.items()})
        self.assertEqual(self.labels('en'), {role: pair[1] for role, pair in EXPECTED.items()})

    def test_platform_role_label_translates(self):
        with override('id'):
            self.assertEqual(str(dict(PlatformRoleAssignment.ROLE_CHOICES)['platform_operator']), 'Operator Platform')
        with override('en'):
            self.assertEqual(str(dict(PlatformRoleAssignment.ROLE_CHOICES)['platform_operator']), 'Platform Operator')

    def test_access_sheet_role_names_stay_english_whatever_language_is_active(self):
        with override('id'):
            self.assertEqual(_role_name('teacher'), 'Teacher')
            self.assertEqual(_role_name('foundation_admin'), 'Foundation Admin')
