import datetime
from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework.test import APIClient

from apps.identity.models import RoleAssignment
from apps.academic.models import DayOfWeek, LessonPlan
from apps.academic.services import create_timetable_slot, duplicate_lesson_plan
from apps.academic.tests.base import build_academic_fixture


class LessonPlanModelTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        self.week = datetime.date(2026, 8, 3)  # a Monday

    def test_create_lesson_plan(self):
        plan = LessonPlan.objects.create(
            foundation_id=self.fx['foundation'].id,
            class_subject=self.fx['class_subject'],
            week_start_date=self.week,
            title="Aljabar Linear",
            content="Membahas persamaan linear dua variabel.",
        )
        self.assertEqual(plan.title, "Aljabar Linear")

    def test_unique_per_class_subject_week(self):
        LessonPlan.objects.create(
            foundation_id=self.fx['foundation'].id, class_subject=self.fx['class_subject'],
            week_start_date=self.week, title="Plan 1",
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LessonPlan.objects.create(
                    foundation_id=self.fx['foundation'].id, class_subject=self.fx['class_subject'],
                    week_start_date=self.week, title="Plan 2",
                )

    def test_duplicate_to_new_week_is_independent_copy(self):
        slot = create_timetable_slot(
            class_subject=self.fx['class_subject'],
            day_of_week=DayOfWeek.MONDAY, period_no=1,
            start_time=datetime.time(7, 0), end_time=datetime.time(7, 40), room='R1',
        )
        original = LessonPlan.objects.create(
            foundation_id=self.fx['foundation'].id, class_subject=self.fx['class_subject'],
            week_start_date=self.week, title="Plan Minggu 1", content="Isi minggu 1",
        )
        original.slots.add(slot)

        next_week = self.week + datetime.timedelta(days=7)
        duplicated = duplicate_lesson_plan(original, next_week)

        self.assertNotEqual(duplicated.id, original.id)
        self.assertEqual(duplicated.week_start_date, next_week)
        self.assertEqual(duplicated.title, original.title)
        self.assertEqual(duplicated.content, original.content)
        self.assertEqual(list(duplicated.slots.all()), [slot])

        duplicated.content = "Direvisi"
        duplicated.save()
        original.refresh_from_db()
        self.assertEqual(original.content, "Isi minggu 1")


class LessonPlanViewsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.fx = build_academic_fixture()
        self.week = datetime.date(2026, 8, 3)
        RoleAssignment.all_tenants.create(
            foundation_id=self.fx['foundation'].id,
            user=self.fx['teacher_user'],
            role='teacher',
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=self.fx['school'].id,
        )

    def test_create_and_duplicate_via_api(self):
        self.client.force_authenticate(user=self.fx['teacher_user'])
        res_create = self.client.post('/api/v1/academic/lesson-plans/', {
            'class_subject': self.fx['class_subject'].id,
            'week_start_date': self.week.isoformat(),
            'title': 'Aljabar Linear',
            'content': 'Persamaan linear dua variabel',
        }, format='json')
        self.assertEqual(res_create.status_code, 201, res_create.content)
        plan_id = res_create.json()['id']

        next_week = (self.week + datetime.timedelta(days=7)).isoformat()
        res_dup = self.client.post(f'/api/v1/academic/lesson-plans/{plan_id}/duplicate/', {
            'target_week_start_date': next_week,
        }, format='json')
        self.assertEqual(res_dup.status_code, 201, res_dup.content)
        self.assertEqual(res_dup.json()['week_start_date'], next_week)
        self.assertNotEqual(res_dup.json()['id'], plan_id)

    def test_cross_tenant_lesson_plan_returns_404(self):
        fx_b = build_academic_fixture(foundation_name="Yayasan B")
        RoleAssignment.all_tenants.create(
            foundation_id=fx_b['foundation'].id,
            user=fx_b['teacher_user'],
            role='teacher',
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=fx_b['school'].id,
        )
        plan_a = LessonPlan.objects.create(
            foundation_id=self.fx['foundation'].id, class_subject=self.fx['class_subject'],
            week_start_date=self.week, title="Plan A",
        )
        self.client.force_authenticate(user=fx_b['teacher_user'])
        res = self.client.get(f'/api/v1/academic/lesson-plans/{plan_a.id}/')
        self.assertEqual(res.status_code, 404)
