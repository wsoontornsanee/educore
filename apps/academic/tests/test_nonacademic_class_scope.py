"""Per-teacher class scope on the non-academic student-keyed endpoints (student
directory, daily attendance, absence requests, behaviour records) and the inbox
absence section. Behaviour creation deliberately stays school-wide (duty teachers
log students outside their classes). Spec: docs/superpowers/specs/2026-09-19-web-console-akademik-design.md."""
import datetime

from apps.academic.tests.test_console_class_scope import ClassScopeTestBase
from apps.academic.tests.test_permission_slips import make_guardian
from apps.attendance.models import (
    AbsenceRequest,
    AbsenceRequestStatus,
    AttendanceDay,
    AttendanceSource,
    AttendanceStatus,
)
from apps.campus.models import BehaviourCategory, BehaviourReason, BehaviourRecord
from apps.identity.inbox import get_inbox_for_user
from apps.identity.models import RoleAssignment
from educore.middleware.tenancy import set_current_foundation_id


def result_ids(response):
    data = response.json()
    return {row['id'] for row in (data['results'] if isinstance(data, dict) and 'results' in data else data)}


class NonAcademicScopeBase(ClassScopeTestBase):
    def setUp(self):
        super().setUp()
        set_current_foundation_id(self.foundation.id)
        self.student_a = self.fx['student']
        self.day = {
            key: AttendanceDay.all_tenants.create(
                foundation_id=self.foundation.id, school=self.school, student=student,
                date=datetime.date(2026, 9, 14), status=AttendanceStatus.HADIR, source=AttendanceSource.SYSTEM,
            )
            for key, student in (('a', self.student_a), ('b', self.student_b))
        }
        self.absence = {
            key: AbsenceRequest.all_tenants.create(
                foundation_id=self.foundation.id, school=self.school, student=student, requested_by=self.teacher.user,
                date_from=datetime.date(2026, 9, 15), date_to=datetime.date(2026, 9, 15), type='SAKIT',
                reason='Demam', status=AbsenceRequestStatus.PENDING,
            )
            for key, student in (('a', self.student_a), ('b', self.student_b))
        }
        self.reason = BehaviourReason.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, code='POS', label='Rajin', points=5,
            category=BehaviourCategory.POSITIVE,
        )


class RestrictedTeacherTests(NonAcademicScopeBase):
    def test_student_directory(self):
        self.assertEqual(result_ids(self.client.get('/api/v1/students/')), {self.student_a.id})
        self.assertEqual(self.client.get(f'/api/v1/students/{self.student_a.id}/').status_code, 200)
        self.assertEqual(self.client.get(f'/api/v1/students/{self.student_b.id}/').status_code, 404)

    def test_daily_attendance(self):
        self.assertEqual(result_ids(self.client.get('/api/v1/attendance/daily/')), {self.day['a'].id})
        self.assertEqual(self.client.get(f'/api/v1/attendance/daily/{self.day["b"].id}/').status_code, 404)
        res = self.client.post(f'/api/v1/attendance/daily/{self.day["b"].id}/override/', {
            'status': 'IZIN', 'reason': 'x',
        }, format='json')
        self.assertEqual(res.status_code, 404)
        self.assertEqual(AttendanceDay.all_tenants.get(id=self.day['b'].id).status, AttendanceStatus.HADIR)

    def test_absence_requests(self):
        self.assertEqual(
            result_ids(self.client.get('/api/v1/attendance/absence-requests/')), {self.absence['a'].id},
        )
        res = self.client.post(f'/api/v1/attendance/absence-requests/{self.absence["b"].id}/approve/', {}, format='json')
        self.assertEqual(res.status_code, 404)
        self.assertEqual(AbsenceRequest.all_tenants.get(id=self.absence['b'].id).status, AbsenceRequestStatus.PENDING)

    def test_inbox_absence_section_is_class_scoped(self):
        sections = {s['id']: s for s in get_inbox_for_user(self.teacher.user, self.foundation.id)}
        self.assertEqual(sections['absence_requests']['total'], 1)

    def test_behaviour_reads_own_classes_plus_own_entries_and_writes_stay_school_wide(self):
        rec_a = BehaviourRecord.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, student=self.student_a, reason=self.reason,
            points=5, recorded_by=self.t2_user,
        )
        rec_b_other = BehaviourRecord.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, student=self.student_b, reason=self.reason,
            points=5, recorded_by=self.t2_user,
        )
        # Duty-teacher case: T1 logs a student outside their classes...
        res = self.client.post('/api/v1/campus/behaviour-records/', {
            'student_id': self.student_b.id, 'reason_id': self.reason.id, 'note': 'Di koridor',
        })
        self.assertEqual(res.status_code, 201)
        own_hallway = res.json()['id']
        # ...and can still read and correct their own entry, but not a colleague's.
        listed = result_ids(self.client.get('/api/v1/campus/behaviour-records/'))
        self.assertEqual(listed, {rec_a.id, own_hallway})
        self.assertNotIn(rec_b_other.id, listed)
        self.assertEqual(self.client.get(f'/api/v1/campus/behaviour-records/{rec_b_other.id}/').status_code, 404)
        res = self.client.post(f'/api/v1/campus/behaviour-records/{own_hallway}/supersede/', {
            'reason_id': self.reason.id, 'correction_reason': 'Keliru siswa', 'points': 3,
        })
        self.assertEqual(res.status_code, 201)

    def test_teacher_who_is_also_a_guardian_keeps_own_child(self):
        _g, user = make_guardian(
            self.foundation, self.school, '+628133000888', 'ortu@nonacad.test', 'Guru Ortu', '3471010101022888',
            student=self.student_b,
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id, user=user, role='teacher',
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.school.id,
        )
        self.client.force_authenticate(user=user)
        self.assertIn(self.student_b.id, result_ids(self.client.get('/api/v1/students/')))
        self.assertIn(self.day['b'].id, result_ids(self.client.get('/api/v1/attendance/daily/')))


class UnrestrictedTests(NonAcademicScopeBase):
    def test_admin_and_parent_paths_unchanged(self):
        admin = self.make_staff_user('school_admin', phone='+628119993000')
        self.client.force_authenticate(user=admin)
        self.assertEqual(result_ids(self.client.get('/api/v1/students/')) & {self.student_a.id, self.student_b.id},
                         {self.student_a.id, self.student_b.id})
        self.assertEqual(
            result_ids(self.client.get('/api/v1/attendance/daily/')), {self.day['a'].id, self.day['b'].id},
        )
        self.assertEqual(
            result_ids(self.client.get('/api/v1/attendance/absence-requests/')),
            {self.absence['a'].id, self.absence['b'].id},
        )
