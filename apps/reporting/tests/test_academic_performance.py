from decimal import Decimal
from django.test import TestCase
from rest_framework.test import APIClient

from apps.academic.models import Assessment, AssessmentType
from apps.academic.services import publish_assessment, set_assessment_score
from apps.academic.tests.base import build_academic_fixture
from apps.identity.models import RoleAssignment
from apps.reporting.models import RptAcademicPerformance
from apps.reporting.services import refresh_academic_performance


def make_assessment(fx, title, max_score='100.00'):
    return Assessment.objects.create(
        foundation_id=fx['foundation'].id, class_subject=fx['class_subject'],
        type=AssessmentType.SUMMATIVE, title=title, max_score=Decimal(max_score), weight=Decimal('40.00'),
    )


class RefreshAcademicPerformanceTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()

    def test_avg_score_and_band_distribution_computed(self):
        a1 = make_assessment(self.fx, "UH1")
        set_assessment_score(a1, self.fx['student'], score=Decimal('90'))
        publish_assessment(a1)
        refresh_academic_performance(scope='full')

        row = RptAcademicPerformance.all_tenants.get(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'], term=self.fx['term'],
            class_group=self.fx['class_group'], subject=self.fx['subject'],
        )
        self.assertEqual(row.avg_score, Decimal('90.00'))
        self.assertEqual(row.band_distribution, {'Sangat Baik': 1})

    def test_unpublished_assessment_excluded(self):
        a1 = make_assessment(self.fx, "UH1 Draft")
        set_assessment_score(a1, self.fx['student'], score=Decimal('90'))
        # deliberately not published
        refresh_academic_performance(scope='full')

        self.assertFalse(RptAcademicPerformance.all_tenants.filter(
            foundation_id=self.fx['foundation'].id, term=self.fx['term'], class_group=self.fx['class_group'],
        ).exists())

    def test_averages_across_multiple_scores(self):
        from apps.identity.models import Person, Student

        a1 = make_assessment(self.fx, "UH1")
        person2 = Person.all_tenants.create(foundation_id=self.fx['foundation'].id, nik='3471010101018888', full_name='Budi Santoso')
        student2 = Student.all_tenants.create(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'], person=person2,
            nisn='1122334400', nis='X-002', status=Student.STATUS_ACTIVE,
        )
        set_assessment_score(a1, self.fx['student'], score=Decimal('80'))
        set_assessment_score(a1, student2, score=Decimal('60'))
        publish_assessment(a1)
        refresh_academic_performance(scope='full')

        row = RptAcademicPerformance.all_tenants.get(
            foundation_id=self.fx['foundation'].id, term=self.fx['term'], class_group=self.fx['class_group'], subject=self.fx['subject'],
        )
        self.assertEqual(row.avg_score, Decimal('70.00'))
        self.assertEqual(row.band_distribution, {'Baik': 1, 'Perlu Bimbingan': 1})

    def test_dashboard_scope_only_active_terms(self):
        self.fx['term'].is_active = False
        self.fx['term'].save()

        a1 = make_assessment(self.fx, "UH1")
        set_assessment_score(a1, self.fx['student'], score=Decimal('90'))
        publish_assessment(a1)

        refresh_academic_performance(scope='dashboard')
        self.assertFalse(RptAcademicPerformance.all_tenants.filter(
            foundation_id=self.fx['foundation'].id, term=self.fx['term'],
        ).exists())

        refresh_academic_performance(scope='full')
        self.assertTrue(RptAcademicPerformance.all_tenants.filter(
            foundation_id=self.fx['foundation'].id, term=self.fx['term'],
        ).exists())

    def test_rerun_is_idempotent(self):
        a1 = make_assessment(self.fx, "UH1")
        set_assessment_score(a1, self.fx['student'], score=Decimal('90'))
        publish_assessment(a1)
        refresh_academic_performance(scope='full')
        refresh_academic_performance(scope='full')

        self.assertEqual(
            RptAcademicPerformance.all_tenants.filter(
                foundation_id=self.fx['foundation'].id, term=self.fx['term'],
                class_group=self.fx['class_group'], subject=self.fx['subject'],
            ).count(),
            1,
        )


class AcademicPerformanceViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.fx = build_academic_fixture()
        RoleAssignment.all_tenants.create(
            foundation_id=self.fx['foundation'].id, user=self.fx['teacher_user'], role='school_admin',
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.fx['school'].id,
        )
        a1 = make_assessment(self.fx, "UH1")
        set_assessment_score(a1, self.fx['student'], score=Decimal('90'))
        publish_assessment(a1)
        refresh_academic_performance(scope='full')

    def test_academic_performance_report_via_api(self):
        self.client.force_authenticate(user=self.fx['teacher_user'])
        res = self.client.get(f'/api/v1/reporting/academic-performance/?school_id={self.fx["school"].id}')
        self.assertEqual(res.status_code, 200, res.content)
        rows = res.json()['rows']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['avg_score'], '90.00')

    def test_filter_by_term_id(self):
        self.client.force_authenticate(user=self.fx['teacher_user'])
        res = self.client.get(
            f'/api/v1/reporting/academic-performance/?school_id={self.fx["school"].id}&term_id={self.fx["term"].id + 1}'
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(len(res.json()['rows']), 0)
