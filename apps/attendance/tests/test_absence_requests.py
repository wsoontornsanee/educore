import datetime
from unittest import mock
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.attendance.models import (
    AbsenceRequest,
    AbsenceRequestStatus,
    AbsenceType,
    AttendanceDay,
    AttendanceSource,
    AttendanceStatus,
)
from apps.attendance.services import approve_absence_request, reject_absence_request, submit_absence_request
from apps.core.models import AuditEvent
from apps.identity.models import Foundation, Guardian, GuardianLink, Person, RoleAssignment, School, Staff, Student, User
from apps.identity.rbac import assign_role
from educore.middleware.tenancy import set_current_foundation_id


class AbsenceRequestTestCase(TestCase):
    def setUp(self):
        # Mock storage for file uploads
        patcher = mock.patch('apps.core.storage._client')
        self.mock_gcs_client = patcher.start()
        self.addCleanup(patcher.stop)
        self.mock_gcs_client.return_value.bucket.return_value.blob.return_value.generate_signed_url.return_value = (
            'https://signed.example/absence_attachment.jpg'
        )

        # Create Foundation & School
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Bina Insani",
            brand_name="Bina Insani",
        )
        set_current_foundation_id(self.foundation.id)

        self.school = School.objects.create(
            foundation_id=self.foundation.id,
            name="SMA Bina Insani",
            npsn="12345678",
            level=School.LEVEL_SMA,
        )

        # Student & Person
        self.student_person = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            nik="3171010101010001",
            full_name="Ahmad Santoso",
        )
        self.student = Student.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            person=self.student_person,
            nis="2026001",
            nisn="0012345678",
            status="ACTIVE",
        )

        # Parent & GuardianLink
        self.parent_person = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            nik="3171010101010002",
            full_name="Bapak Joko Santoso",
        )
        self.parent_user = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6281200000001",
            full_name="Bapak Joko Santoso",
        )
        self.parent_user.set_unusable_password()
        self.parent_user.save()

        self.guardian = Guardian.objects.create(
            foundation_id=self.foundation.id,
            person=self.parent_person,
            user=self.parent_user,
        )
        self.guardian_link = GuardianLink.objects.create(
            foundation_id=self.foundation.id,
            student=self.student,
            guardian=self.guardian,
            relation=GuardianLink.RELATION_FATHER,
            financial_responsible=True,
            is_primary=True,
        )
        assign_role(self.parent_user, RoleAssignment.ROLE_PARENT, RoleAssignment.SCOPE_FOUNDATION, self.foundation.id)

        # Unlinked Parent
        self.other_parent_user = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6281200000002",
            full_name="Ibu Ratna",
        )
        self.other_parent_user.set_unusable_password()
        self.other_parent_user.save()
        assign_role(self.other_parent_user, RoleAssignment.ROLE_PARENT, RoleAssignment.SCOPE_FOUNDATION, self.foundation.id)

        # Teacher / Staff
        self.teacher_person = Person.all_tenants.create(
            foundation_id=self.foundation.id,
            nik="3171010101010003",
            full_name="Ibu Guru Siti",
        )
        self.teacher_user = User.objects.create(
            foundation_id=self.foundation.id,
            phone_e164="+6281200000003",
            full_name="Ibu Guru Siti",
        )
        self.teacher_user.set_unusable_password()
        self.teacher_user.save()
        self.teacher_staff = Staff.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            person=self.teacher_person,
            user=self.teacher_user,
            nip="198001012005012001",
            join_date=datetime.date(2020, 1, 1),
            employment_type=Staff.TYPE_PERMANENT,
            status=Staff.STATUS_ACTIVE,
        )
        assign_role(self.teacher_user, RoleAssignment.ROLE_TEACHER, RoleAssignment.SCOPE_SCHOOL, self.school.id)
        # Per-teacher class scope: the teacher sees this student only through a class of theirs.
        from apps.academic.tests.base import enroll_in_class_of
        enroll_in_class_of(self.teacher_staff, self.student)

        # Second Foundation for cross-tenant checks
        self.other_foundation = Foundation.objects.create(
            legal_name="Yayasan Cahaya Ilmu",
            brand_name="Cahaya Ilmu",
        )
        self.other_school = School.objects.create(
            foundation_id=self.other_foundation.id,
            name="SMA Cahaya Ilmu",
            npsn="87654321",
            level=School.LEVEL_SMA,
        )
        self.other_student_person = Person.all_tenants.create(
            foundation_id=self.other_foundation.id,
            nik="3171010101019999",
            full_name="Budi Pratama",
        )
        self.other_student = Student.objects.create(
            foundation_id=self.other_foundation.id,
            school=self.other_school,
            person=self.other_student_person,
            nis="2026999",
            nisn="0098765432",
            status="ACTIVE",
        )

        self.client = APIClient()

    def test_parent_submit_absence_request_success(self):
        self.client.force_authenticate(user=self.parent_user)
        photo = SimpleUploadedFile(
            "surat_dokter.jpg",
            b"fake jpeg content data",
            content_type="image/jpeg"
        )

        response = self.client.post(
            f"/api/v1/attendance/students/{self.student.id}/absence-requests/",
            {
                'date_from': '2026-09-20',
                'date_to': '2026-09-22',
                'type': 'SAKIT',
                'reason': 'Ahmad demam tinggi dan disarankan dokter istirahat 3 hari.',
                'attachment': photo,
            },
            format='multipart',
            HTTP_X_FOUNDATION_ID=str(self.foundation.id),
        )

        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data['status'], 'PENDING')
        self.assertEqual(data['type'], 'SAKIT')
        self.assertEqual(data['date_from'], '2026-09-20')
        self.assertEqual(data['date_to'], '2026-09-22')
        self.assertIn('ABSENCE_ATTACHMENT/', data['attachment_key'])
        self.assertIn('https://', data['attachment_url'])

        # Check DB record
        req = AbsenceRequest.all_tenants.get(id=data['id'])
        self.assertEqual(req.requested_by, self.parent_user)
        self.assertEqual(req.student, self.student)

    def test_parent_submit_absence_request_file_size_exceeded_par011(self):
        self.client.force_authenticate(user=self.parent_user)
        # Create file exceeding 1MB (1024*1024 + 10 bytes)
        large_photo = SimpleUploadedFile(
            "large_surat.jpg",
            b"x" * (1024 * 1024 + 10),
            content_type="image/jpeg"
        )

        response = self.client.post(
            f"/api/v1/attendance/students/{self.student.id}/absence-requests/",
            {
                'date_from': '2026-09-20',
                'date_to': '2026-09-21',
                'type': 'SAKIT',
                'reason': 'Sakit demam',
                'attachment': large_photo,
            },
            format='multipart',
            HTTP_X_FOUNDATION_ID=str(self.foundation.id),
        )

        self.assertEqual(response.status_code, 400)
        content_str = str(response.content)
        self.assertTrue("1MB" in content_str or "PAR-011" in content_str)

    def test_parent_submit_absence_request_invalid_date_range(self):
        self.client.force_authenticate(user=self.parent_user)
        response = self.client.post(
            f"/api/v1/attendance/students/{self.student.id}/absence-requests/",
            {
                'date_from': '2026-09-25',
                'date_to': '2026-09-20',  # date_to before date_from
                'type': 'IZIN',
                'reason': 'Acara keluarga',
            },
            format='json',
            HTTP_X_FOUNDATION_ID=str(self.foundation.id),
        )
        self.assertEqual(response.status_code, 400)

    def test_unlinked_guardian_returns_404_iam014(self):
        self.client.force_authenticate(user=self.other_parent_user)
        response = self.client.get(
            f"/api/v1/attendance/students/{self.student.id}/absence-requests/",
            HTTP_X_FOUNDATION_ID=str(self.foundation.id),
        )
        self.assertEqual(response.status_code, 404)

        response = self.client.post(
            f"/api/v1/attendance/students/{self.student.id}/absence-requests/",
            {
                'date_from': '2026-09-20',
                'date_to': '2026-09-21',
                'type': 'IZIN',
                'reason': 'Acara keluarga',
            },
            format='json',
            HTTP_X_FOUNDATION_ID=str(self.foundation.id),
        )
        self.assertEqual(response.status_code, 404)

    def test_cross_tenant_isolation(self):
        # Authenticated with self.parent_user (Foundation 1), querying other_student (Foundation 2)
        self.client.force_authenticate(user=self.parent_user)
        response = self.client.get(
            f"/api/v1/attendance/students/{self.other_student.id}/absence-requests/",
            HTTP_X_FOUNDATION_ID=str(self.foundation.id),
        )
        self.assertEqual(response.status_code, 404)

    def test_parent_lists_absence_requests(self):
        # Create 2 absence requests
        req1 = AbsenceRequest.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            requested_by=self.parent_user,
            date_from=datetime.date(2026, 9, 10),
            date_to=datetime.date(2026, 9, 11),
            type=AbsenceType.IZIN,
            reason="Izin acara keluarga",
            status=AbsenceRequestStatus.APPROVED,
        )
        req2 = AbsenceRequest.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            requested_by=self.parent_user,
            date_from=datetime.date(2026, 9, 15),
            date_to=datetime.date(2026, 9, 16),
            type=AbsenceType.SAKIT,
            reason="Demam",
            status=AbsenceRequestStatus.PENDING,
        )

        self.client.force_authenticate(user=self.parent_user)
        response = self.client.get(
            f"/api/v1/attendance/students/{self.student.id}/absence-requests/",
            HTTP_X_FOUNDATION_ID=str(self.foundation.id),
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]['id'], req2.id)
        self.assertEqual(data[1]['id'], req1.id)

    def test_staff_approval_updates_attendance_day_att002(self):
        # Initial state: student has ALPA on 2026-09-21
        existing_alpa = AttendanceDay.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            date=datetime.date(2026, 9, 21),
            status=AttendanceStatus.ALPA,
            source=AttendanceSource.SYSTEM,
        )

        req = AbsenceRequest.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            requested_by=self.parent_user,
            date_from=datetime.date(2026, 9, 20),
            date_to=datetime.date(2026, 9, 22),
            type=AbsenceType.SAKIT,
            reason="Sakit demam",
            status=AbsenceRequestStatus.PENDING,
        )

        self.client.force_authenticate(user=self.teacher_user)
        response = self.client.post(
            f"/api/v1/attendance/absence-requests/{req.id}/approve/",
            {'note': 'Surat dokter terverifikasi'},
            format='json',
            HTTP_X_FOUNDATION_ID=str(self.foundation.id),
        )
        self.assertEqual(response.status_code, 200)
        req.refresh_from_db()
        self.assertEqual(req.status, AbsenceRequestStatus.APPROVED)
        self.assertEqual(req.decided_by, self.teacher_user)
        self.assertEqual(req.decision_note, 'Surat dokter terverifikasi')

        set_current_foundation_id(self.foundation.id)

        # Verify ATT-002: covered dates (20, 21, 22) must be SAKIT with is_override=True
        day20 = AttendanceDay.all_tenants.get(student=self.student, date=datetime.date(2026, 9, 20))
        self.assertEqual(day20.status, AttendanceStatus.SAKIT)
        self.assertTrue(day20.is_override)
        self.assertEqual(day20.source, AttendanceSource.MANUAL)

        day21 = AttendanceDay.all_tenants.get(student=self.student, date=datetime.date(2026, 9, 21))
        self.assertEqual(day21.status, AttendanceStatus.SAKIT)
        self.assertEqual(day21.original_status, AttendanceStatus.ALPA)
        self.assertTrue(day21.is_override)

        day22 = AttendanceDay.all_tenants.get(student=self.student, date=datetime.date(2026, 9, 22))
        self.assertEqual(day22.status, AttendanceStatus.SAKIT)
        self.assertTrue(day22.is_override)

        # approve_absence_request now delegates each date's day-level override to the
        # shared override_attendance_day service instead of hand-rolling it, so one
        # 'attendance.day.overridden' AuditEvent must exist per covered date (20, 21, 22),
        # in addition to the existing per-request 'attendance.absence_request.approved'
        # summary event.
        day_override_events = AuditEvent.objects.filter(
            action='attendance.day.overridden', entity_type='AttendanceDay',
        ).order_by('diff__date')
        self.assertEqual(day_override_events.count(), 3)
        self.assertEqual(day_override_events[0].diff['date'], '2026-09-20')
        self.assertEqual(day_override_events[0].diff['old_status'], AttendanceStatus.ALPA)
        self.assertEqual(day_override_events[0].diff['new_status'], AttendanceStatus.SAKIT)
        self.assertEqual(day_override_events[1].diff['date'], '2026-09-21')
        self.assertEqual(day_override_events[1].diff['old_status'], AttendanceStatus.ALPA)
        self.assertEqual(day_override_events[2].diff['date'], '2026-09-22')

        summary_event = AuditEvent.objects.get(action='attendance.absence_request.approved', entity_id=req.id)
        self.assertEqual(summary_event.diff['date_from'], '2026-09-20')

    def test_staff_approval_flips_gate_sourced_day_to_manual(self):
        # A day that already has a GATE-derived AttendanceDay (e.g. the student badged in
        # that morning before the absence request was approved) must still flip to
        # source=MANUAL on approval, matching apps.campus.services_clinic's identical
        # override_attendance_day-consolidation precedent — override_attendance_day itself
        # never touches `source`, so the caller must set it explicitly.
        AttendanceDay.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            date=datetime.date(2026, 9, 20),
            status=AttendanceStatus.HADIR,
            source=AttendanceSource.GATE,
        )

        req = AbsenceRequest.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            requested_by=self.parent_user,
            date_from=datetime.date(2026, 9, 20),
            date_to=datetime.date(2026, 9, 20),
            type=AbsenceType.SAKIT,
            reason="Sakit demam",
            status=AbsenceRequestStatus.PENDING,
        )

        self.client.force_authenticate(user=self.teacher_user)
        response = self.client.post(
            f"/api/v1/attendance/absence-requests/{req.id}/approve/",
            {'note': 'Surat dokter terverifikasi'},
            format='json',
            HTTP_X_FOUNDATION_ID=str(self.foundation.id),
        )
        self.assertEqual(response.status_code, 200)

        set_current_foundation_id(self.foundation.id)
        day20 = AttendanceDay.all_tenants.get(student=self.student, date=datetime.date(2026, 9, 20))
        self.assertEqual(day20.status, AttendanceStatus.SAKIT)
        self.assertEqual(day20.source, AttendanceSource.MANUAL)
        self.assertEqual(day20.original_status, AttendanceStatus.HADIR)
        self.assertTrue(day20.is_override)

    def test_staff_rejection_does_not_modify_attendance(self):
        req = AbsenceRequest.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            requested_by=self.parent_user,
            date_from=datetime.date(2026, 9, 20),
            date_to=datetime.date(2026, 9, 20),
            type=AbsenceType.IZIN,
            reason="Liburan pribadi",
            status=AbsenceRequestStatus.PENDING,
        )

        self.client.force_authenticate(user=self.teacher_user)
        response = self.client.post(
            f"/api/v1/attendance/absence-requests/{req.id}/reject/",
            {'note': 'Izin tidak dapat disetujui menjelang ujian.'},
            format='json',
            HTTP_X_FOUNDATION_ID=str(self.foundation.id),
        )
        self.assertEqual(response.status_code, 200)
        req.refresh_from_db()
        self.assertEqual(req.status, AbsenceRequestStatus.REJECTED)
        self.assertEqual(req.decision_note, 'Izin tidak dapat disetujui menjelang ujian.')

        # Verify no AttendanceDay created for date 20
        self.assertFalse(AttendanceDay.objects.filter(student=self.student, date=datetime.date(2026, 9, 20)).exists())

    def test_cannot_reapprove_or_rereject_decided_request(self):
        req = AbsenceRequest.objects.create(
            foundation_id=self.foundation.id,
            school=self.school,
            student=self.student,
            requested_by=self.parent_user,
            date_from=datetime.date(2026, 9, 20),
            date_to=datetime.date(2026, 9, 20),
            type=AbsenceType.SAKIT,
            reason="Sakit demam",
            status=AbsenceRequestStatus.APPROVED,
        )

        self.client.force_authenticate(user=self.teacher_user)
        response = self.client.post(
            f"/api/v1/attendance/absence-requests/{req.id}/approve/",
            {'note': 'Approve again'},
            format='json',
            HTTP_X_FOUNDATION_ID=str(self.foundation.id),
        )
        self.assertEqual(response.status_code, 400)
