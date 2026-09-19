"""Permission Slip School Console (HTMX) tests (spec/08 PAR-012, spec/17).

Covers the web/HTMX surface added by the console: template rendering with
spec/17 conventions, session-auth page access, staff gating, HTMX create
fragment round-trip, and the tally/roster fragment endpoints.
"""
import datetime
from unittest import mock

from django.template.loader import render_to_string
from django.test import TestCase
from django.utils import timezone
from django.urls import reverse
from rest_framework.test import APIClient

from apps.academic.models import ClassEnrollment, PermissionSlip, PermissionSlipAcknowledgement
from apps.academic.services import create_permission_slip
from apps.academic.tests.base import build_academic_fixture
from apps.academic.tests.test_permission_slips import make_guardian
from apps.identity.models import RoleAssignment
from apps.identity.rbac import assign_role
from educore.middleware.tenancy import set_current_foundation_id


class PermissionSlipConsoleTemplateTests(TestCase):
    def test_console_component_renders_with_spec17_conventions(self):
        slips = [{
            'id': 1, 'title': 'Kunjungan Museum Nasional', 'description': 'Study tour',
            'event_date': '2026-10-01', 'location': 'Jakarta', 'due_at': None,
            'is_closed': False, 'class_group_id': 11, 'class_group_name': 'X IPA 1',
            'school_id': 2, 'created_by_name': 'Bu Siti', 'created_at': None,
            'tally': {'total_enrolled': 30, 'approved': 21, 'declined': 4, 'pending': 5},
        }]
        html = render_to_string('components/permission_slip_console.html', {
            'staff': None,
            'class_groups': [{'id': 11, 'name': 'X IPA 1'}],
            'slips': slips,
            'create_url': '/web/academic/permission-slips/create/',
            'tally_url_prefix': '/web/academic/permission-slips',
            'form_error': None,
        })
        # id-ID strings + spec/17 institutional conventions
        self.assertIn('Konsol Izin Digital', html)
        self.assertIn('Terbitkan Izin Baru', html)
        self.assertIn('Tally Persetujuan', html)
        self.assertIn('Kunjungan Museum Nasional', html)
        # HTMX wiring
        self.assertIn('hx-post="/web/academic/permission-slips/create/"', html)
        self.assertIn('hx-target="#slip-list-container"', html)
        self.assertIn('hx-indicator="#slip-create-spinner"', html)
        self.assertIn('every 15s', html)
        # 0px radius institutional sharpness (no rounded corners on cards)
        self.assertNotIn('border-radius: 8px', html)
        self.assertNotIn('border-radius: 12px', html)

    def test_slip_list_fragment_empty_state(self):
        html = render_to_string('components/_permission_slip_list.html', {
            'slips': [], 'tally_url_prefix': '/web/academic/permission-slips',
        })
        self.assertIn('Belum Ada Izin', html)
        self.assertIn('Terbitkan izin pertama', html)

    def test_slip_list_fragment_renders_tally_and_polling(self):
        slips = [{
            'id': 7, 'title': 'Izin Kemah Pramuka', 'description': '',
            'event_date': None, 'location': 'Bandung', 'due_at': None,
            'is_closed': False, 'class_group_id': 11, 'class_group_name': 'X IPA 1',
            'school_id': 2, 'created_by_name': 'Bu Siti', 'created_at': None,
            'tally': {'total_enrolled': 10, 'approved': 6, 'declined': 1, 'pending': 3},
        }]
        html = render_to_string('components/_permission_slip_list.html', {
            'slips': slips, 'tally_url_prefix': '/web/academic/permission-slips',
        })
        self.assertIn('Izin Kemah Pramuka', html)
        self.assertIn('Setuju: 6', html)
        self.assertIn('Tolak: 1', html)
        self.assertIn('Menunggu: 3', html)
        self.assertIn('hx-get="/web/academic/permission-slips/7/"', html)
        self.assertIn('every 15s', html)
        self.assertIn('hx-get="/web/academic/permission-slips/7/roster/"', html)

    def test_tally_fragment_uses_semantic_colors(self):
        html = render_to_string('components/_permission_slip_tally.html', {
            'tally': {'total_enrolled': 30, 'approved': 21, 'declined': 4, 'pending': 5},
        })
        self.assertIn('var(--color-success-bg)', html)
        self.assertIn('var(--color-danger-bg)', html)
        self.assertIn('var(--color-warning-bg)', html)
        self.assertIn('/ 30 siswa', html)

    def test_roster_fragment_renders_responses(self):
        roster = [
            {'student_id': 1, 'student_name': 'Andi Wijaya', 'nis': 'X-001',
             'response': 'APPROVED', 'responded_at': '17 Sep 2026 10:00'},
            {'student_id': 2, 'student_name': 'Budi Santoso', 'nis': 'X-002',
             'response': 'PENDING', 'responded_at': None},
        ]
        html = render_to_string('components/_permission_slip_roster.html', {
            'roster': roster, 'tally': None,
        })
        self.assertIn('Andi Wijaya', html)
        self.assertIn('X-001', html)
        self.assertIn('Setuju', html)
        self.assertIn('Menunggu', html)
        self.assertIn('17 Sep 2026 10:00', html)

    def test_error_fragment(self):
        html = render_to_string('components/_slip_form_error.html', {
            'form_error': 'Judul izin wajib diisi.',
        })
        self.assertIn('Judul izin wajib diisi.', html)
        self.assertIn('role="alert"', html)

    def test_full_page_extends_base(self):
        html = render_to_string('pages/permission_slip_console_page.html', {
            'staff': None, 'class_groups': [], 'slips': [],
            'create_url': '/web/academic/permission-slips/create/',
            'tally_url_prefix': '/web/academic/permission-slips',
            'form_error': None,
            'csrf_token': 'dummy-csrf-token',
        })
        self.assertIn('<!DOCTYPE html>', html)
        self.assertIn('Konsol Izin Digital', html)
        self.assertIn('htmx.org', html)


class PermissionSlipConsoleEndpointTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture("Yayasan Slip Console")
        self.foundation = self.fx['foundation']
        self.school = self.fx['school']
        self.student = self.fx['student']
        self.class_group = self.fx['class_group']
        self.teacher = self.fx['teacher']
        set_current_foundation_id(self.foundation.id)
        ClassEnrollment.objects.create(
            foundation_id=self.foundation.id, student=self.student,
            class_group=self.class_group, enrolled_at=datetime.date(2026, 7, 1), is_active=True,
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id, user=self.teacher.user,
            role='teacher', scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.school.id,
        )
        self.guardian, self.guardian_user = make_guardian(
            self.foundation, self.school, "+628133000001", "wali@console.test", "Wali Konsol",
            "3471010101022001", student=self.student,
        )
        self.client = APIClient()

    def _auth_teacher(self):
        self.client.force_authenticate(user=self.teacher.user)

    def test_console_page_renders_for_teacher(self):
        self._auth_teacher()
        res = self.client.get('/web/academic/permission-slips/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Konsol Izin Digital')
        self.assertContains(res, 'X IPA 1')  # class group select populated

    def test_console_page_rejected_for_guardian_without_staff_profile(self):
        # Guardian has grades.read via ROLE_PARENT, but no Staff profile -> 404
        self.client.force_authenticate(user=self.guardian_user)
        res = self.client.get('/web/academic/permission-slips/')
        self.assertEqual(res.status_code, 404)

    def test_console_page_requires_authentication(self):
        res = self.client.get('/web/academic/permission-slips/')
        self.assertIn(res.status_code, (401, 403))

    def test_console_create_round_trip_via_htmx(self):
        self._auth_teacher()
        res = self.client.post('/web/academic/permission-slips/create/', {
            'title': 'Kunjungan Kebun Raya',
            'class_group_id': self.class_group.id,
            'event_date': '2026-11-05',
            'location': 'Bogor',
            'description': 'Ekskusi biologi',
        })
        self.assertEqual(res.status_code, 201)
        self.assertIn('Kunjungan Kebun Raya', res.content.decode())
        self.assertIn('Setuju: 0', res.content.decode())
        self.assertIn('Menunggu: 1', res.content.decode())
        self.assertTrue(PermissionSlip.all_tenants.filter(title='Kunjungan Kebun Raya').exists())

    def test_console_create_blank_title_returns_error_fragment_400(self):
        self._auth_teacher()
        res = self.client.post('/web/academic/permission-slips/create/', {
            'title': '   ', 'class_group_id': self.class_group.id,
        })
        self.assertEqual(res.status_code, 400)
        self.assertIn('Judul izin wajib diisi', res.content.decode())

    def test_console_create_rejected_for_guardian_role(self):
        # A guardian lacks grades.write -> denied (sent home) before any staff lookup
        self.client.force_authenticate(user=self.guardian_user)
        res = self.client.post('/web/academic/permission-slips/create/', {
            'title': 'X', 'class_group_id': self.class_group.id,
        })
        self.assertRedirects(res, reverse('web-console-home'), fetch_redirect_response=False)

    def test_tally_fragment_reflects_acknowledgements(self):
        slip = create_permission_slip(self.teacher, self.class_group, 'Izin tally fragmen')
        from apps.academic.services import acknowledge_permission_slip
        acknowledge_permission_slip(
            slip, self.guardian, self.student,
            PermissionSlipAcknowledgement.RESPONSE_APPROVED, 'Wali Konsol',
        )
        self._auth_teacher()
        res = self.client.get(f'/web/academic/permission-slips/{slip.id}/')
        self.assertEqual(res.status_code, 200)
        self.assertIn('Setuju: 1', res.content.decode())
        self.assertIn('Menunggu: 0', res.content.decode())

    def test_roster_fragment_shows_signature_row(self):
        slip = create_permission_slip(self.teacher, self.class_group, 'Izin roster fragmen')
        from apps.academic.services import acknowledge_permission_slip
        ack = acknowledge_permission_slip(
            slip, self.guardian, self.student,
            PermissionSlipAcknowledgement.RESPONSE_APPROVED, 'Wali Konsol',
        )
        self._auth_teacher()
        res = self.client.get(f'/web/academic/permission-slips/{slip.id}/roster/')
        self.assertEqual(res.status_code, 200)
        body = res.content.decode()
        self.assertIn('Andi Wijaya', body)
        self.assertIn('Setuju', body)
        self.assertIsNotNone(ack.responded_at)

    def test_cross_tenant_slip_tally_returns_404(self):
        from apps.identity.models import Foundation, School
        other_foundation = Foundation.objects.create(
            legal_name="Yayasan Console Lain", brand_name="Yayasan Console Lain",
        )
        slip_other = PermissionSlip.all_tenants.create(
            foundation_id=other_foundation.id, class_group=self.class_group,
            created_by=self.teacher, title="Izin foundation lain",
        )
        set_current_foundation_id(other_foundation.id)
        try:
            self._auth_teacher()
            res = self.client.get(f'/web/academic/permission-slips/{slip_other.id}/')
            self.assertEqual(res.status_code, 404)
        finally:
            set_current_foundation_id(self.foundation.id)
