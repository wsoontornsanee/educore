"""Per-teacher class scope on the JSON API, plus regression checks that the
flows the mobile teacher app relies on keep working for a restricted teacher.
Spec: docs/superpowers/specs/2026-09-19-web-console-akademik-design.md
("Follow-on: per-teacher class scope on the JSON API")."""
import datetime

from django.utils import timezone

from apps.academic.models import (
    Assessment,
    AssessmentType,
    ClassSubject,
    Exam,
    HomeworkSubmission,
    HomeworkSubmissionStatus as S,
    LessonPlan,
    PermissionSlip,
    ReportCardStatus,
    Subject,
    SubstitutionStatus,
    TimetableSlot,
    TimetableSubstitution,
)
from apps.academic.models import ReportCard
from apps.academic.tests.test_console_class_scope import ClassScopeTestBase
from apps.academic.tests.test_permission_slips import make_guardian
from apps.identity.models import RoleAssignment

API = '/api/v1/academic'


def ids(response):
    data = response.json()
    rows = data['results'] if isinstance(data, dict) and 'results' in data else data
    return {row['id'] for row in rows}


class ApiScopeTestBase(ClassScopeTestBase):
    def setUp(self):
        super().setUp()
        now = timezone.now()
        self.assessment_a = Assessment.objects.create(
            foundation_id=self.foundation.id, class_subject=self.fx['class_subject'], type=AssessmentType.SUMMATIVE,
            title='UH A', max_score=100, weight=100,
        )
        self.assessment_b = Assessment.objects.create(
            foundation_id=self.foundation.id, class_subject=self.cs_b, type=AssessmentType.SUMMATIVE,
            title='UH B', max_score=100, weight=100,
        )
        self.homework = {k: sub.homework for k, sub in self.submissions.items()}
        self.slot_a = TimetableSlot.objects.get(class_subject=self.fx['class_subject'])
        self.slot_b = TimetableSlot.objects.get(class_subject=self.cs_b)


class RestrictedTeacherApiTests(ApiScopeTestBase):
    def test_homework_and_submissions(self):
        self.assertEqual(ids(self.client.get(f'{API}/homework/')), {self.homework['a'].id})
        self.assertEqual(self.client.get(f'{API}/homework/{self.homework["b"].id}/').status_code, 404)
        self.assertEqual(self.client.get(f'{API}/homework/{self.homework["b"].id}/submissions/').status_code, 404)
        self.assertEqual(
            ids(self.client.get(f'{API}/homework-submissions/')), {self.submissions['a'].id},
        )
        self.assertEqual(self.client.get(f'{API}/homework-submissions/{self.submissions["b"].id}/').status_code, 404)
        res = self.client.post(f'{API}/homework-submissions/{self.submissions["b"].id}/grade/', {'score': '90'})
        self.assertEqual(res.status_code, 404)
        self.assertEqual(HomeworkSubmission.all_tenants.get(id=self.submissions['b'].id).status, S.SUBMITTED)

    def test_grading_queue(self):
        queue = self.client.get(f'{API}/homework/grading-queue/').json()
        self.assertEqual({row['id'] for row in queue}, {self.submissions['a'].id})
        per_homework = self.client.get(f'{API}/homework/{self.homework["b"].id}/grading-queue/')
        self.assertEqual(per_homework.status_code, 404)

    def test_assessments_read_and_write(self):
        listed = ids(self.client.get(f'{API}/assessments/'))
        self.assertIn(self.assessment_a.id, listed)
        self.assertNotIn(self.assessment_b.id, listed)
        self.assertEqual(self.client.get(f'{API}/assessments/{self.assessment_b.id}/').status_code, 404)
        payload = {
            'class_subject': self.cs_b.id, 'type': AssessmentType.SUMMATIVE, 'title': 'Baru',
            'max_score': '100', 'weight': '10',
        }
        self.assertEqual(self.client.post(f'{API}/assessments/', payload, format='json').status_code, 404)
        payload['class_subject'] = self.fx['class_subject'].id
        self.assertEqual(self.client.post(f'{API}/assessments/', payload, format='json').status_code, 201)

    def test_homework_and_lesson_plan_creation_in_another_class_refused(self):
        payload = {
            'class_subject': self.cs_b.id, 'title': 'PR baru', 'instructions': '',
            'assigned_at': timezone.now().isoformat(),
            'due_at': (timezone.now() + datetime.timedelta(days=2)).isoformat(),
        }
        self.assertEqual(self.client.post(f'{API}/homework/', payload, format='json').status_code, 404)
        plan = {'class_subject': self.cs_b.id, 'week_start_date': '2026-09-14', 'title': 'RPP', 'content': ''}
        self.assertEqual(self.client.post(f'{API}/lesson-plans/', plan, format='json').status_code, 404)
        self.assertFalse(LessonPlan.all_tenants.exists())

    def test_class_enrollments_and_gradebook(self):
        listed = ids(self.client.get('/api/v1/academic/class-enrollments/'))
        from apps.academic.models import ClassEnrollment
        own = set(ClassEnrollment.all_tenants.filter(class_group=self.class_a).values_list('id', flat=True))
        self.assertTrue(own)
        self.assertEqual(listed, own)
        self.assertEqual(
            len(self.client.get(f'{API}/class-enrollments/', {'class_group_id': self.class_b.id}).json()['results']), 0,
        )
        self.assertEqual(
            self.client.get(f'{API}/gradebook/', {'class_subject_id': self.cs_b.id}).status_code, 404,
        )
        self.assertEqual(
            self.client.get(f'{API}/gradebook/', {'class_subject_id': self.fx['class_subject'].id}).status_code, 200,
        )

    def test_report_cards(self):
        self.assertEqual(ids(self.client.get(f'{API}/report-cards/')), {self.card_a.id})
        self.assertEqual(self.client.get(f'{API}/report-cards/{self.card_b.id}/').status_code, 404)
        res = self.client.post(
            f'{API}/report-cards/generate/', {'class_group_id': self.class_b.id, 'term_id': self.fx['term'].id},
            format='json',
        )
        self.assertEqual(res.status_code, 404)
        ReportCard.all_tenants.filter(id=self.card_b.id).update(status=ReportCardStatus.PUBLISHED)
        self.assertEqual(self.client.post(f'{API}/report-cards/{self.card_b.id}/revise/').status_code, 404)

    def test_broadcasts_and_permission_slips(self):
        res = self.client.post(
            f'{API}/teacher/broadcasts/', {'class_group_id': self.class_b.id, 'title': 'x', 'body': 'y'}, format='json',
        )
        self.assertEqual(res.status_code, 404)
        from apps.academic.services import create_permission_slip
        slip_a = create_permission_slip(self.teacher, self.class_a, 'Kunjungan Alfa')
        slip_b = create_permission_slip(self.t2, self.class_b, 'Kunjungan Beta')
        listed = {row['id'] for row in self.client.get(f'{API}/teacher/permission-slips/').json()['results']}
        self.assertEqual(listed, {slip_a.id})
        self.assertEqual(
            self.client.get(f'{API}/teacher/permission-slips/{slip_b.id}/consent-tally/').status_code, 404,
        )
        res = self.client.post(
            f'{API}/teacher/permission-slips/', {'class_group_id': self.class_b.id, 'title': 'Izin'}, format='json',
        )
        self.assertEqual(res.status_code, 404)
        self.assertEqual(PermissionSlip.all_tenants.filter(title='Izin').count(), 0)
        # ...and the same slips through the Operasional web console
        page = self.client.get('/web/academic/permission-slips/')
        self.assertContains(page, 'Kunjungan Alfa')
        self.assertNotContains(page, 'Kunjungan Beta')
        self.assertEqual(self.client.get(f'/web/academic/permission-slips/{slip_b.id}/').status_code, 404)
        self.assertEqual(self.client.get(f'/web/academic/permission-slips/{slip_b.id}/roster/').status_code, 404)

    def test_student_endpoints(self):
        for suffix in ('grades', 'homework', 'report-cards', 'timetable', 'attainment'):
            query = {'class_subject_id': self.fx['class_subject'].id} if suffix == 'attainment' else {}
            own = self.client.get(f'{API}/students/{self.fx["student"].id}/{suffix}/', query)
            other = self.client.get(f'{API}/students/{self.student_b.id}/{suffix}/', query)
            self.assertEqual(own.status_code, 200, suffix)
            self.assertEqual(other.status_code, 404, suffix)

    def test_teacher_who_is_also_a_guardian_keeps_access_to_their_own_child(self):
        from apps.identity.models import Guardian, GuardianLink
        _guardian, guardian_user = make_guardian(
            self.foundation, self.school, '+628133000777', 'ortu@scope.test', 'Guru Sekaligus Ortu',
            '3471010101022777', student=self.student_b,
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id, user=guardian_user, role='teacher',
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.school.id,
        )
        self.client.force_authenticate(user=guardian_user)
        self.assertEqual(self.client.get(f'{API}/students/{self.student_b.id}/grades/').status_code, 200)


class UnrestrictedApiTests(ApiScopeTestBase):
    def test_admin_and_parent_paths_are_unchanged(self):
        admin = self.make_staff_user('school_admin', phone='+628119992000')
        self.client.force_authenticate(user=admin)
        self.assertEqual(ids(self.client.get(f'{API}/homework/')), {self.homework['a'].id, self.homework['b'].id})
        self.assertEqual(ids(self.client.get(f'{API}/report-cards/')), {self.card_a.id, self.card_b.id})
        self.assertEqual(self.client.get(f'{API}/students/{self.student_b.id}/grades/').status_code, 200)
        _g, guardian_user = make_guardian(
            self.foundation, self.school, '+628133000778', 'ortu2@scope.test', 'Ortu Biasa',
            '3471010101022778', student=self.student_b,
        )
        self.client.force_authenticate(user=guardian_user)
        self.assertEqual(self.client.get(f'{API}/students/{self.student_b.id}/grades/').status_code, 200)


class MobileTeacherCompatibilityTests(ApiScopeTestBase):
    """The mobile teacher app (agenda, roll call, substitution modal, broadcast) only
    touches the endpoints below; a restricted teacher must keep every one working,
    including for a class they do not teach but were asked to cover."""

    def setUp(self):
        super().setUp()
        self.day = datetime.date(2026, 9, 15)  # the Tuesday slot_b recurs on
        self.substitution = TimetableSubstitution.objects.create(
            foundation_id=self.foundation.id, slot=self.slot_b, date=self.day,
            original_teacher=self.t2, substitute_teacher=self.teacher, status=SubstitutionStatus.PENDING,
        )

    def test_substitute_sees_and_accepts_a_substitution_in_a_class_they_do_not_teach(self):
        url = f'{API}/timetable/substitutions/{self.substitution.id}/'
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertIn(self.substitution.id, ids(self.client.get(f'{API}/timetable/substitutions/')))
        self.assertEqual(self.client.get(f'{url}slot/').status_code, 200)
        self.assertEqual(self.client.get(f'{API}/timetable/slots/{self.slot_b.id}/').status_code, 200)
        res = self.client.post(f'{url}accept/', format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(TimetableSubstitution.all_tenants.get(id=self.substitution.id).status,
                         SubstitutionStatus.ACCEPTED)

    def test_original_teacher_keeps_seeing_their_substituted_slot(self):
        self.client.force_authenticate(user=self.t2_user)
        self.assertEqual(self.client.get(f'{API}/timetable/substitutions/{self.substitution.id}/').status_code, 200)

    def test_uninvolved_substitutions_in_other_classes_are_hidden(self):
        other = self.make_staff_user('teacher', phone='+628119992100')
        self.client.force_authenticate(user=other)
        self.assertNotIn(self.substitution.id, ids(self.client.get(f'{API}/timetable/substitutions/')))
        self.assertEqual(self.client.get(f'{API}/timetable/substitutions/{self.substitution.id}/').status_code, 404)

    def test_agenda_includes_the_covered_slot_and_own_classes(self):
        TimetableSubstitution.all_tenants.filter(id=self.substitution.id).update(status=SubstitutionStatus.ACCEPTED)
        res = self.client.get('/api/v1/teacher/agenda', {'date': self.day.isoformat()})
        self.assertEqual(res.status_code, 200)
        self.assertIn(self.slot_b.id, {item['slot_id'] if 'slot_id' in item else item['id'] for item in res.json()['agenda']})

    def test_roll_call_flow_for_own_class_slot(self):
        res = self.client.get('/api/v1/teacher/agenda', {'date': '2026-09-14'})  # Monday: slot_a
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.json()['agenda']), 1)
        self.assertEqual(self.client.get(f'{API}/timetable/slots/{self.slot_a.id}/').status_code, 200)

    def test_broadcast_to_own_class_still_works_and_lists(self):
        res = self.client.post(
            f'{API}/teacher/broadcasts/', {'class_group_id': self.class_a.id, 'title': 'Halo', 'body': 'Info'},
            format='json',
        )
        self.assertIn(res.status_code, (201, 400))  # 400 only if the school disabled broadcasts, never 404
        self.assertEqual(self.client.get(f'{API}/teacher/broadcasts/').status_code, 200)
