import datetime
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.academic.models import (
    ClassEnrollment,
    PermissionSlip,
    PermissionSlipAcknowledgement,
)
from apps.academic.services import (
    acknowledge_permission_slip,
    create_permission_slip,
    get_effective_acknowledgement,
    get_slip_consent_tally,
    GuardianNotLinkedError,
    PermissionSlipClosedError,
    StudentNotEnrolledError,
)
from apps.academic.tests.base import build_academic_fixture
from apps.identity.models import Foundation, Guardian, GuardianLink, Person, RoleAssignment, School, Student, User
from apps.identity.rbac import assign_role
from educore.middleware.tenancy import set_current_foundation_id


def make_guardian(foundation, school, phone, email, name, nik, student=None, financial=True):
    person = Person.all_tenants.create(foundation_id=foundation.id, nik=nik, full_name=name)
    user = User.objects.create(
        foundation_id=foundation.id, phone_e164=phone, email=email, full_name=name,
    )
    assign_role(
        user=user, role=RoleAssignment.ROLE_PARENT,
        scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=school.id,
        foundation_id=foundation.id,
    )
    guardian = Guardian.all_tenants.create(foundation_id=foundation.id, user=user, person=person)
    if student is not None:
        GuardianLink.all_tenants.create(
            foundation_id=foundation.id, guardian=guardian, student=student,
            relation=GuardianLink.RELATION_FATHER, financial_responsible=financial,
        )
    return guardian, user


class PermissionSlipServiceTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture("Yayasan Slip Service")
        self.foundation = self.fx['foundation']
        self.school = self.fx['school']
        self.student = self.fx['student']
        self.class_group = self.fx['class_group']
        self.teacher = self.fx['teacher']
        set_current_foundation_id(self.foundation.id)
        self.enrollment = ClassEnrollment.objects.create(
            foundation_id=self.foundation.id, student=self.student,
            class_group=self.class_group, enrolled_at=datetime.date(2026, 7, 1), is_active=True,
        )

    def test_create_permission_slip_creates_audit_and_notifies_guardians(self):
        guardian, user = make_guardian(
            self.foundation, self.school, "+628111000001", "wali1@slip.test", "Wali Satu",
            "3471010101020001", student=self.student,
        )
        with mock.patch('apps.notifications.services.dispatch_intent') as mock_dispatch:
            slip = create_permission_slip(
                self.teacher, self.class_group, "Kunjungan Museum Nasional",
                description="Study tour sejarah", event_date=datetime.date(2026, 10, 1),
                location="Museum Nasional, Jakarta",
            )
        self.assertIsNotNone(slip.id)
        self.assertEqual(slip.title, "Kunjungan Museum Nasional")
        self.assertEqual(slip.created_by, self.teacher)
        mock_dispatch.assert_called_once()

        from apps.core.models import AuditEvent
        self.assertTrue(AuditEvent.objects.filter(
            action='academic.permission_slip.created', entity_id=slip.id,
        ).exists())

    def test_create_permission_slip_notification_failure_does_not_block_creation(self):
        guardian, user = make_guardian(
            self.foundation, self.school, "+628111000002", "wali2@slip.test", "Wali Dua",
            "3471010101020002", student=self.student,
        )
        with mock.patch('apps.notifications.services.dispatch_intent', side_effect=RuntimeError("provider down")):
            slip = create_permission_slip(self.teacher, self.class_group, "Izin Kemah")
        self.assertIsNotNone(slip.id)

    def test_create_permission_slip_requires_title(self):
        with self.assertRaises(ValueError):
            create_permission_slip(self.teacher, self.class_group, "   ")

    def test_create_permission_slip_rejects_due_after_event(self):
        with self.assertRaises(ValueError):
            create_permission_slip(
                self.teacher, self.class_group, "Izin Terlambat",
                event_date=datetime.date(2026, 10, 1),
                due_at=timezone.make_aware(datetime.datetime(2026, 10, 5, 12, 0)),
            )

    def test_acknowledge_happy_path_records_timestamp_and_audit(self):
        guardian, user = make_guardian(
            self.foundation, self.school, "+628111000003", "wali3@slip.test", "Wali Tiga",
            "3471010101020003", student=self.student,
        )
        slip = create_permission_slip(self.teacher, self.class_group, "Izin Renang")
        ack = acknowledge_permission_slip(
            slip, guardian, self.student,
            PermissionSlipAcknowledgement.RESPONSE_APPROVED, "Wali Tiga",
        )
        self.assertEqual(ack.response, 'APPROVED')
        self.assertIsNotNone(ack.responded_at)
        self.assertEqual(ack.signature, "Wali Tiga")

        from apps.core.models import AuditEvent
        self.assertTrue(AuditEvent.objects.filter(
            action='academic.permission_slip.acknowledged', entity_id=ack.id,
        ).exists())

    def test_acknowledge_requires_signature(self):
        guardian, user = make_guardian(
            self.foundation, self.school, "+628111000004", "wali4@slip.test", "Wali Empat",
            "3471010101020004", student=self.student,
        )
        slip = create_permission_slip(self.teacher, self.class_group, "Izin Pramuka")
        with self.assertRaises(ValueError):
            acknowledge_permission_slip(
                slip, guardian, self.student,
                PermissionSlipAcknowledgement.RESPONSE_APPROVED, "  ",
            )

    def test_acknowledge_rejects_unlinked_guardian(self):
        guardian, user = make_guardian(
            self.foundation, self.school, "+628111000005", "wali5@slip.test", "Wali Lain",
            "3471010101020005", student=None,
        )
        slip = create_permission_slip(self.teacher, self.class_group, "Izin Olahraga")
        with self.assertRaises(GuardianNotLinkedError):
            acknowledge_permission_slip(
                slip, guardian, self.student,
                PermissionSlipAcknowledgement.RESPONSE_APPROVED, "Wali Lain",
            )

    def test_acknowledge_rejects_student_not_in_slip_class(self):
        # Guardian is linked to other_student, but other_student is NOT enrolled
        # in the slip's class group -> StudentNotEnrolledError (guardian link check passes).
        other_person = Person.all_tenants.create(
            foundation_id=self.foundation.id, nik="3471010101020999", full_name="Siswa Lain",
        )
        other_student = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, person=other_person,
            nisn="1122339999", nis="X-999", status=Student.STATUS_ACTIVE,
        )
        guardian, user = make_guardian(
            self.foundation, self.school, "+628111000006", "wali6@slip.test", "Wali Enam",
            "3471010101020006", student=other_student,
        )
        slip = create_permission_slip(self.teacher, self.class_group, "Izin Class Trip")
        with self.assertRaises(StudentNotEnrolledError):
            acknowledge_permission_slip(
                slip, guardian, other_student,
                PermissionSlipAcknowledgement.RESPONSE_APPROVED, "Wali Enam",
            )

    def test_acknowledge_rejected_after_due(self):
        guardian, user = make_guardian(
            self.foundation, self.school, "+628111000007", "wali7@slip.test", "Wali Tujuh",
            "3471010101020007", student=self.student,
        )
        slip = create_permission_slip(
            self.teacher, self.class_group, "Izin Terbatas",
            due_at=timezone.now() - datetime.timedelta(hours=1),
        )
        with self.assertRaises(PermissionSlipClosedError):
            acknowledge_permission_slip(
                slip, guardian, self.student,
                PermissionSlipAcknowledgement.RESPONSE_APPROVED, "Wali Tujuh",
            )

    def test_changed_answer_supersedes_never_rewrites(self):
        guardian, user = make_guardian(
            self.foundation, self.school, "+628111000008", "wali8@slip.test", "Wali Delapan",
            "3471010101020008", student=self.student,
        )
        slip = create_permission_slip(self.teacher, self.class_group, "Izin outing")
        first = acknowledge_permission_slip(
            slip, guardian, self.student,
            PermissionSlipAcknowledgement.RESPONSE_APPROVED, "Wali Delapan",
        )
        first.responded_at = timezone.now() - datetime.timedelta(hours=2)
        first.save(update_fields=['responded_at'])

        second = acknowledge_permission_slip(
            slip, guardian, self.student,
            PermissionSlipAcknowledgement.RESPONSE_DECLINED, "Wali Delapan",
        )
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(first.response, 'APPROVED')  # immutable history
        effective = get_effective_acknowledgement(slip, self.student, guardian)
        self.assertEqual(effective.id, second.id)
        self.assertEqual(effective.response, 'DECLINED')

    def test_consent_tally_counts_latest_effective_response(self):
        g1, _u1 = make_guardian(
            self.foundation, self.school, "+628111000009", "wali9@slip.test", "Wali Sembilan",
            "3471010101020009", student=self.student,
        )
        other_person = Person.all_tenants.create(
            foundation_id=self.foundation.id, nik="3471010101020888", full_name="Siswa Dua",
        )
        other_student = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, person=other_person,
            nisn="1122338888", nis="X-002", status=Student.STATUS_ACTIVE,
        )
        ClassEnrollment.objects.create(
            foundation_id=self.foundation.id, student=other_student,
            class_group=self.class_group, enrolled_at=datetime.date(2026, 7, 1), is_active=True,
        )
        GuardianLink.all_tenants.create(
            foundation_id=self.foundation.id, guardian=g1, student=other_student,
            relation=GuardianLink.RELATION_MOTHER, financial_responsible=True,
        )
        slip = create_permission_slip(self.teacher, self.class_group, "Izin tally test")

        acknowledge_permission_slip(
            slip, g1, self.student,
            PermissionSlipAcknowledgement.RESPONSE_APPROVED, "Wali Sembilan",
        )
        # other_student's guardian changes mind: approved then declined
        acknowledge_permission_slip(
            slip, g1, other_student,
            PermissionSlipAcknowledgement.RESPONSE_APPROVED, "Wali Sembilan",
        )
        # changed answer: supersedes the earlier APPROVED for other_student
        acknowledge_permission_slip(
            slip, g1, other_student,
            PermissionSlipAcknowledgement.RESPONSE_DECLINED, "Wali Sembilan",
        )

        tally = get_slip_consent_tally(slip)
        self.assertEqual(tally['total_enrolled'], 2)
        self.assertEqual(tally['approved'], 1)
        self.assertEqual(tally['declined'], 1)
        self.assertEqual(tally['pending'], 0)


class PermissionSlipAPITests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture("Yayasan Slip API")
        self.foundation = self.fx['foundation']
        self.school = self.fx['school']
        self.student = self.fx['student']
        self.class_group = self.fx['class_group']
        self.teacher = self.fx['teacher']
        set_current_foundation_id(self.foundation.id)
        self.enrollment = ClassEnrollment.objects.create(
            foundation_id=self.foundation.id, student=self.student,
            class_group=self.class_group, enrolled_at=datetime.date(2026, 7, 1), is_active=True,
        )
        self.guardian, self.guardian_user = make_guardian(
            self.foundation, self.school, "+628122000001", "wali@api.test", "Wali API",
            "3471010101021001", student=self.student,
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id, user=self.teacher.user,
            role='teacher', scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.school.id,
        )
        self.client = APIClient()

    def _auth(self, user):
        self.client.force_authenticate(user=user)

    # --- staff endpoints ---

    def test_teacher_creates_slip_and_sees_tally(self):
        self._auth(self.teacher.user)
        res = self.client.post('/api/v1/academic/teacher/permission-slips/', {
            'class_group_id': self.class_group.id,
            'title': 'Kunjungan Kebun Raya',
            'description': 'Ekskusi biologi',
            'event_date': '2026-11-05',
            'location': 'Kebun Raya Bogor',
        }, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['title'], 'Kunjungan Kebun Raya')
        self.assertEqual(res.data['tally']['total_enrolled'], 1)
        self.assertEqual(res.data['tally']['pending'], 1)

        res_list = self.client.get('/api/v1/academic/teacher/permission-slips/')
        self.assertEqual(res_list.status_code, 200)
        self.assertEqual(len(res_list.data['results']), 1)

    def test_teacher_tally_roster_shows_signature_and_response(self):
        self._auth(self.teacher.user)
        slip = create_permission_slip(self.teacher, self.class_group, 'Izin roster')
        acknowledge_permission_slip(
            slip, self.guardian, self.student,
            PermissionSlipAcknowledgement.RESPONSE_APPROVED, 'Wali API',
        )
        res = self.client.get(f'/api/v1/academic/teacher/permission-slips/{slip.id}/consent-tally/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['tally']['approved'], 1)
        roster = res.data['roster']
        self.assertEqual(len(roster), 1)
        self.assertEqual(roster[0]['response'], 'APPROVED')
        self.assertEqual(roster[0]['signature'], 'Wali API')
        self.assertEqual(roster[0]['student_id'], self.student.id)

    def test_create_rejected_for_non_staff_roles(self):
        # A guardian lacks grades.write -> 403 before the staff-profile lookup runs.
        self._auth(self.guardian_user)
        res = self.client.post('/api/v1/academic/teacher/permission-slips/', {
            'class_group_id': self.class_group.id, 'title': 'Bukan staf',
        }, format='json')
        self.assertEqual(res.status_code, 403)

    def test_teacher_endpoints_require_authentication(self):
        res = self.client.get('/api/v1/academic/teacher/permission-slips/')
        self.assertIn(res.status_code, (401, 403))

    # --- parent endpoints ---

    def test_parent_lists_child_slips_with_own_response(self):
        slip = create_permission_slip(self.teacher, self.class_group, 'Izin parent view')
        self._auth(self.guardian_user)
        res = self.client.get(f'/api/v1/academic/students/{self.student.id}/permission-slips/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data['results']), 1)
        item = res.data['results'][0]
        self.assertEqual(item['id'], slip.id)
        self.assertIsNone(item['my_response'])
        self.assertTrue(item['my_pending'])

        res_ack = self.client.post(f'/api/v1/academic/permission-slips/{slip.id}/acknowledge/', {
            'student_id': self.student.id, 'response': 'APPROVED', 'signature': 'Wali API',
        }, format='json')
        self.assertEqual(res_ack.status_code, 201, res_ack.data)
        res2 = self.client.get(f'/api/v1/academic/students/{self.student.id}/permission-slips/')
        item2 = res2.data['results'][0]
        self.assertEqual(item2['my_response'], 'APPROVED')
        self.assertFalse(item2['my_pending'])

    def test_parent_acknowledge_signs_with_timestamp(self):
        slip = create_permission_slip(self.teacher, self.class_group, 'Izin sign')
        self._auth(self.guardian_user)
        res = self.client.post(f'/api/v1/academic/permission-slips/{slip.id}/acknowledge/', {
            'student_id': self.student.id,
            'response': 'APPROVED',
            'signature': 'Wali API',
        }, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['response'], 'APPROVED')
        self.assertIsNotNone(res.data['responded_at'])
        self.assertEqual(res.data['signature'], 'Wali API')

    def test_parent_acknowledge_rejects_invalid_response_and_blank_signature(self):
        slip = create_permission_slip(self.teacher, self.class_group, 'Izin validasi')
        self._auth(self.guardian_user)
        res_bad = self.client.post(f'/api/v1/academic/permission-slips/{slip.id}/acknowledge/', {
            'student_id': self.student.id, 'response': 'MAYBE', 'signature': 'Wali API',
        }, format='json')
        self.assertEqual(res_bad.status_code, 400)
        res_blank = self.client.post(f'/api/v1/academic/permission-slips/{slip.id}/acknowledge/', {
            'student_id': self.student.id, 'response': 'APPROVED', 'signature': '',
        }, format='json')
        self.assertEqual(res_blank.status_code, 400)

    def test_parent_acknowledge_404_for_unlinked_guardian_cross_family(self):
        other_guardian, other_user = make_guardian(
            self.foundation, self.school, "+628122000002", "wali-lain@api.test", "Wali Lain API",
            "3471010101021002", student=None,
        )
        slip = create_permission_slip(self.teacher, self.class_group, 'Izin orang lain')
        self._auth(other_user)
        res = self.client.post(f'/api/v1/academic/permission-slips/{slip.id}/acknowledge/', {
            'student_id': self.student.id, 'response': 'APPROVED', 'signature': 'Wali Lain API',
        }, format='json')
        self.assertEqual(res.status_code, 404)

    def test_parent_slip_list_404_for_unlinked_guardian(self):
        other_guardian, other_user = make_guardian(
            self.foundation, self.school, "+628122000003", "wali-dua@api.test", "Wali Dua API",
            "3471010101021003", student=None,
        )
        create_permission_slip(self.teacher, self.class_group, 'Izin tersembunyi')
        self._auth(other_user)
        res = self.client.get(f'/api/v1/academic/students/{self.student.id}/permission-slips/')
        self.assertEqual(res.status_code, 404)

    def test_acknowledge_rejected_after_due_http(self):
        slip = create_permission_slip(
            self.teacher, self.class_group, 'Izin tutup',
            due_at=timezone.now() - datetime.timedelta(hours=2),
        )
        self._auth(self.guardian_user)
        res = self.client.post(f'/api/v1/academic/permission-slips/{slip.id}/acknowledge/', {
            'student_id': self.student.id, 'response': 'APPROVED', 'signature': 'Wali API',
        }, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('PERMISSION_SLIP_CLOSED', res.data['error'])

    # --- 3-layer cross-tenant (Layer 3) ---

    def test_cross_tenant_slip_returns_404(self):
        # another foundation with its own school/class
        other_foundation = Foundation.objects.create(
            legal_name="Yayasan Slip Lain", brand_name="Yayasan Slip Lain",
        )
        other_school = School.all_tenants.create(
            foundation_id=other_foundation.id, name="SMA Lain",
            npsn="20999999", level=School.LEVEL_SMA,
        )
        other_person = Person.all_tenants.create(
            foundation_id=other_foundation.id, nik="3471020101020999", full_name="Siswa Lain Fnd",
        )
        other_student = Student.all_tenants.create(
            foundation_id=other_foundation.id, school=other_school, person=other_person,
            nisn="1199887766", nis="L-001", status=Student.STATUS_ACTIVE,
        )
        slip_other = PermissionSlip.all_tenants.create(
            foundation_id=other_foundation.id, class_group=self.class_group,
            created_by=self.teacher, title="Izin foundation lain",
        )
        set_current_foundation_id(other_foundation.id)
        try:
            self._auth(self.guardian_user)
            res_ack = self.client.post(f'/api/v1/academic/permission-slips/{slip_other.id}/acknowledge/', {
                'student_id': other_student.id, 'response': 'APPROVED', 'signature': 'Wali API',
            }, format='json')
            self.assertEqual(res_ack.status_code, 404)
        finally:
            set_current_foundation_id(self.foundation.id)

    def test_hard_delete_forbidden_data_preserved_soft_only(self):
        slip = create_permission_slip(self.teacher, self.class_group, 'Izin soft delete')
        slip.deleted_at = timezone.now()
        slip.save()
        # parent no longer sees it
        self._auth(self.guardian_user)
        res = self.client.get(f'/api/v1/academic/students/{self.student.id}/permission-slips/')
        self.assertEqual(len(res.data['results']), 0)
        # row still exists (soft delete, never hard)
        self.assertTrue(PermissionSlip.all_tenants.with_deleted().filter(id=slip.id).exists())
