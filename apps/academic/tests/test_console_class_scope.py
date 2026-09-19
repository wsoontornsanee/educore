"""Per-teacher class scoping across the Akademik console (pages + write actions).
Spec: docs/superpowers/specs/2026-09-19-web-console-akademik-design.md
("Follow-on: per-teacher class scoping")."""
import datetime

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.academic.class_scope import ClassScope
from apps.academic.models import (
    ClassGroup,
    ClassSubject,
    DayOfWeek,
    Homework,
    HomeworkSubmission,
    HomeworkSubmissionStatus as S,
    ReportCard,
    ReportCardStatus,
    Subject,
    TimetableSlot,
)
from apps.academic.services import generate_report_cards
from apps.academic.tests.base import build_academic_fixture
from apps.academic.tests.test_console_actions import ConsoleActionTestBase
from apps.academic.tests.test_report_cards import enroll_and_grade
from apps.identity.models import Person, RoleAssignment, School, Staff, Student, User
from educore.middleware.tenancy import set_current_foundation_id

CLASSES, TIMETABLE, QUEUE, CARDS = (
    '/web/academic/classes/', '/web/academic/timetable/', '/web/academic/grading-queue/', '/web/academic/report-cards/',
)


class ClassScopeTestBase(ConsoleActionTestBase):
    """self.teacher (T1) teaches/homerooms class A ('X IPA 1'). Class B ('X IPA 2')
    belongs to a second teacher (T2) in the same school and has its own slot,
    ungraded submission and report card."""

    def setUp(self):
        super().setUp()
        self.class_a = self.fx['class_group']
        self.t2_user = self.make_staff_user('teacher', phone='+628119990900')
        self.t2 = Staff.all_tenants.get(user=self.t2_user)
        self.class_b = ClassGroup.objects.create(
            foundation_id=self.foundation.id, school=self.school, academic_year=self.fx['academic_year'],
            grade_level=10, name='X IPA 2', homeroom_teacher=self.t2,
        )
        subject_b = Subject.objects.create(
            foundation_id=self.foundation.id, school=self.school, code='FIS', name='Fisika', credit_hours=3,
        )
        self.cs_b = ClassSubject.objects.create(
            foundation_id=self.foundation.id, class_group=self.class_b, subject=subject_b, teacher=self.t2,
            term=self.fx['term'],
        )
        TimetableSlot.objects.create(
            foundation_id=self.foundation.id, class_subject=self.fx['class_subject'], day_of_week=DayOfWeek.MONDAY,
            period_no=1, start_time=datetime.time(7, 0), end_time=datetime.time(7, 45), room='R-A',
        )
        TimetableSlot.objects.create(
            foundation_id=self.foundation.id, class_subject=self.cs_b, day_of_week=DayOfWeek.TUESDAY,
            period_no=1, start_time=datetime.time(7, 0), end_time=datetime.time(7, 45), room='R-B',
        )
        person = Person.all_tenants.create(foundation_id=self.foundation.id, full_name='Siswa Kelas B')
        self.student_b = Student.all_tenants.create(
            foundation_id=self.foundation.id, school=self.school, person=person, nis='B-001',
            status=Student.STATUS_ACTIVE,
        )
        from apps.academic.models import ClassEnrollment
        ClassEnrollment.objects.create(
            foundation_id=self.foundation.id, student=self.student_b, class_group=self.class_b,
            enrolled_at=datetime.date(2026, 7, 1),
        )
        now = timezone.now()
        self.submissions = {}
        for key, cs, student in (('a', self.fx['class_subject'], self.fx['student']), ('b', self.cs_b, self.student_b)):
            hw = Homework.objects.create(
                foundation_id=self.foundation.id, class_subject=cs, title=f'PR {key.upper()}',
                assigned_at=now - datetime.timedelta(days=3), due_at=now - datetime.timedelta(days=1),
            )
            self.submissions[key] = HomeworkSubmission.objects.create(
                foundation_id=self.foundation.id, homework=hw, student=student,
                submitted_at=now - datetime.timedelta(hours=4), status=S.SUBMITTED,
            )
        enroll_and_grade(self.fx)  # enrols the fixture student in class A and grades them
        generate_report_cards(self.class_a, self.fx['term'])
        generate_report_cards(self.class_b, self.fx['term'])
        self.card_a = ReportCard.all_tenants.get(class_group=self.class_a)
        self.card_b = ReportCard.all_tenants.get(class_group=self.class_b)

    def as_user(self, user):
        self.client.force_authenticate(user=user)
        return self.client


class TeacherRestrictedTests(ClassScopeTestBase):
    def test_class_list_and_detail(self):
        res = self.client.get(CLASSES)
        self.assertContains(res, 'X IPA 1')
        self.assertNotContains(res, 'X IPA 2')
        self.assertEqual(self.client.get(f'{CLASSES}{self.class_a.id}/').status_code, 200)
        self.assertEqual(self.client.get(f'{CLASSES}{self.class_b.id}/').status_code, 404)

    def test_homeroom_without_teaching_counts_as_own(self):
        ClassSubject.all_tenants.filter(id=self.cs_b.id).update(teacher=self.teacher)  # T1 now teaches B
        self.assertContains(self.client.get(CLASSES), 'X IPA 2')
        ClassSubject.all_tenants.filter(id=self.cs_b.id).update(teacher=self.t2)
        ClassGroup.all_tenants.filter(id=self.class_b.id).update(homeroom_teacher=self.teacher)  # homeroom only
        res = self.client.get(CLASSES)
        self.assertContains(res, 'X IPA 2')
        self.assertEqual(self.client.get(f'{CLASSES}{self.class_b.id}/').status_code, 200)

    def test_timetable(self):
        res = self.client.get(TIMETABLE)  # default lens = first visible class
        self.assertContains(res, 'R-A')
        self.assertNotContains(res, 'X IPA 2')  # not even offered in the picker
        self.assertEqual(self.client.get(TIMETABLE, {'lens': f'class:{self.class_b.id}'}).status_code, 404)
        # Another teacher's timetable only shows the slots of classes the viewer may see
        res = self.client.get(TIMETABLE, {'lens': f'teacher:{self.t2.id}'})
        self.assertEqual(res.status_code, 200)
        self.assertNotContains(res, 'R-B')

    def test_grading_queue(self):
        res = self.client.get(QUEUE)
        self.assertContains(res, 'PR A')
        self.assertNotContains(res, 'PR B')
        self.assertEqual(res.context['total_count'], 1)
        res = self.client.get(QUEUE, {'class_subject': self.cs_b.id})
        self.assertEqual(res.context['total_count'], 0)

    def test_report_cards(self):
        res = self.client.get(CARDS)
        self.assertEqual(res.context['total_count'], 1)
        self.assertContains(res, f'{CARDS}{self.card_a.id}/')
        self.assertNotContains(res, f'{CARDS}{self.card_b.id}/')
        self.assertEqual(self.client.get(f'{CARDS}{self.card_a.id}/').status_code, 200)
        for suffix in ('', 'print/'):
            self.assertEqual(self.client.get(f'{CARDS}{self.card_b.id}/{suffix}').status_code, 404)

    def test_write_actions_refuse_other_classes(self):
        sub_b = self.submissions['b']
        self.assertEqual(self.client.post(f'{QUEUE}{sub_b.id}/grade/', {'score': '90'}).status_code, 404)
        self.assertEqual(self.client.post(f'{QUEUE}{sub_b.id}/return/', {'feedback': 'x'}).status_code, 404)
        self.assertEqual(HomeworkSubmission.all_tenants.get(id=sub_b.id).status, S.SUBMITTED)
        ReportCard.all_tenants.filter(id=self.card_b.id).update(status=ReportCardStatus.PUBLISHED)
        self.assertEqual(self.client.post(f'{CARDS}{self.card_b.id}/revise/').status_code, 404)
        res = self.client.post(f'{CARDS}generate/', {'class_group': self.class_b.id, 'term': self.fx['term'].id})
        self.assertEqual(res.status_code, 302)
        self.assertNotIn('term=', res['Location'])  # error redirect, nothing generated
        # ...but own-class writes still work
        self.assertEqual(self.client.post(f'{QUEUE}{self.submissions["a"].id}/grade/', {'score': '90'}).status_code, 302)
        self.assertEqual(HomeworkSubmission.all_tenants.get(id=self.submissions['a'].id).status, S.GRADED)


class UnrestrictedTests(ClassScopeTestBase):
    def assert_sees_both(self, user):
        client = self.as_user(user)
        self.assertContains(client.get(CLASSES), 'X IPA 2')
        self.assertEqual(client.get(f'{CLASSES}{self.class_b.id}/').status_code, 200)
        self.assertContains(client.get(QUEUE), 'PR B')
        self.assertEqual(client.get(CARDS).context['total_count'], 2)

    def test_admins_and_non_teaching_roles_see_everything(self):
        for i, role in enumerate(('school_admin', 'counsellor')):
            self.assert_sees_both(self.make_staff_user(role, phone=f'+62811999100{i}'))

    def test_teacher_who_is_also_school_admin_is_unrestricted(self):
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id, user=self.teacher.user, role='school_admin',
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.school.id,
        )
        self.assert_sees_both(self.teacher.user)

    def test_parent_role_does_not_lift_the_restriction(self):
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id, user=self.teacher.user, role='parent',
            scope_type=RoleAssignment.SCOPE_FOUNDATION, scope_id=self.foundation.id,
        )
        self.assertNotContains(self.client.get(CLASSES), 'X IPA 2')

    def test_superuser_is_unrestricted(self):
        User.all_tenants.filter(id=self.teacher.user_id).update(is_superuser=True)
        self.teacher.user.refresh_from_db()
        self.assertFalse(ClassScope(self.teacher.user, self.foundation.id).is_restricted)


class PerSchoolAndScopeObjectTests(ClassScopeTestBase):
    def test_restriction_is_decided_per_school(self):
        other_school = School.all_tenants.create(
            foundation_id=self.foundation.id, name='SMA Lain', npsn='20888888', level=School.LEVEL_SMA,
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id, user=self.teacher.user, role='school_admin',
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=other_school.id,
        )
        other_year = self.fx['academic_year'].__class__.objects.create(
            foundation_id=self.foundation.id, school=other_school, name='2026/2027',
            start_date=datetime.date(2026, 7, 1), end_date=datetime.date(2027, 6, 30),
        )
        other_class = ClassGroup.objects.create(
            foundation_id=self.foundation.id, school=other_school, academic_year=other_year,
            grade_level=10, name='XI Lain',
        )
        scope = ClassScope(self.teacher.user, self.foundation.id)
        self.assertEqual(scope.restricted_school_ids, {self.school.id})
        self.assertTrue(scope.allows(other_class))   # admin of the other school
        self.assertTrue(scope.allows(self.class_a))
        self.assertFalse(scope.allows(self.class_b))
        res = self.client.get(CLASSES)
        self.assertContains(res, 'XI Lain')
        self.assertContains(res, 'X IPA 1')
        self.assertNotContains(res, 'X IPA 2')

    def test_foundation_scoped_teacher_is_restricted_in_every_school(self):
        RoleAssignment.all_tenants.filter(user=self.teacher.user).delete()
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id, user=self.teacher.user, role='teacher',
            scope_type=RoleAssignment.SCOPE_FOUNDATION, scope_id=self.foundation.id,
        )
        scope = ClassScope(self.teacher.user, self.foundation.id)
        self.assertEqual(scope.restricted_school_ids, {self.school.id})
        self.assertFalse(scope.allows(self.class_b))

    def test_construction_cost_is_fixed(self):
        with CaptureQueriesContext(connection) as ctx:
            ClassScope(self.teacher.user, self.foundation.id)
        self.assertLessEqual(len(ctx.captured_queries), 6)
        admin = self.make_staff_user('school_admin', phone='+628119991500')
        with CaptureQueriesContext(connection) as ctx:
            ClassScope(admin, self.foundation.id)
        self.assertLessEqual(len(ctx.captured_queries), 1)  # unrestricted: no school/class lookups
