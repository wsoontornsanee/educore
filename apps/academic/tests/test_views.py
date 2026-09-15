import datetime
from decimal import Decimal
from django.test import TestCase
from rest_framework.test import APIClient

from apps.identity.models import Foundation, Person, RoleAssignment, School, Student, User
from apps.academic.models import Assessment, AssessmentType, ClassEnrollment
from apps.academic.tests.base import build_academic_fixture
from educore.middleware.tenancy import set_current_foundation_id


class AcademicViewsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.fx = build_academic_fixture()
        self.foundation = self.fx['foundation']

        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id,
            user=self.fx['teacher_user'],
            role='teacher',
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=self.fx['school'].id,
        )
        ClassEnrollment.objects.create(
            foundation_id=self.foundation.id,
            student=self.fx['student'],
            class_group=self.fx['class_group'],
            enrolled_at=datetime.date(2026, 7, 1),
        )

    def test_subject_crud(self):
        self.client.force_authenticate(user=self.fx['teacher_user'])
        res = self.client.post('/api/v1/academic/subjects/', {
            'school': self.fx['school'].id,
            'code': 'FIS',
            'name': 'Fisika',
        }, format='json')
        self.assertEqual(res.status_code, 403)  # teacher lacks school_config.write

    def test_assessment_create_and_publish_flow(self):
        self.client.force_authenticate(user=self.fx['teacher_user'])
        res = self.client.post('/api/v1/academic/assessments/', {
            'class_subject': self.fx['class_subject'].id,
            'type': AssessmentType.SUMMATIVE,
            'title': 'UH1',
            'max_score': '100.00',
            'weight': '100.00',
        }, format='json')
        self.assertEqual(res.status_code, 201, res.content)
        assessment_id = res.json()['id']

        res_publish = self.client.post(f'/api/v1/academic/assessments/{assessment_id}/publish/')
        self.assertEqual(res_publish.status_code, 200)
        self.assertTrue(res_publish.json()['published'])

    def test_score_out_of_range_returns_400(self):
        assessment = Assessment.objects.create(
            foundation_id=self.foundation.id,
            class_subject=self.fx['class_subject'],
            type=AssessmentType.SUMMATIVE,
            title="UH1",
            max_score=Decimal('100.00'),
            weight=Decimal('100.00'),
        )
        self.client.force_authenticate(user=self.fx['teacher_user'])
        res = self.client.put(
            f'/api/v1/academic/assessments/{assessment.id}/scores/',
            {'scores': [{'student_id': self.fx['student'].id, 'score': '150'}]},
            format='json',
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn('SCORE_OUT_OF_RANGE', res.json()['error'])

    def test_gradebook_matrix(self):
        assessment = Assessment.objects.create(
            foundation_id=self.foundation.id,
            class_subject=self.fx['class_subject'],
            type=AssessmentType.SUMMATIVE,
            title="UH1",
            max_score=Decimal('100.00'),
            weight=Decimal('100.00'),
        )
        self.client.force_authenticate(user=self.fx['teacher_user'])
        self.client.put(
            f'/api/v1/academic/assessments/{assessment.id}/scores/',
            {'scores': [{'student_id': self.fx['student'].id, 'score': '88'}]},
            format='json',
        )

        res = self.client.get(f'/api/v1/academic/gradebook/?class_subject_id={self.fx["class_subject"].id}')
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(len(body['students']), 1)
        self.assertEqual(body['students'][0]['id'], self.fx['student'].id)
        self.assertEqual(len(body['assessments']), 1)
        score_row = next(s for s in body['scores'] if s['student_id'] == self.fx['student'].id)
        self.assertEqual(score_row['score'], '88.00')


class AcademicCrossTenantTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.fx_a = build_academic_fixture(foundation_name="Yayasan A")
        set_current_foundation_id(None)
        self.fx_b = build_academic_fixture(foundation_name="Yayasan B")

        RoleAssignment.all_tenants.create(
            foundation_id=self.fx_b['foundation'].id,
            user=self.fx_b['teacher_user'],
            role='school_admin',
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=self.fx_b['school'].id,
        )

    def test_cross_tenant_class_subject_returns_404(self):
        self.client.force_authenticate(user=self.fx_b['teacher_user'])
        res = self.client.get(f'/api/v1/academic/class-subjects/{self.fx_a["class_subject"].id}/')
        self.assertEqual(res.status_code, 404)
