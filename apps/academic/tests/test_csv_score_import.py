"""Tests for CSV bulk score import with dry-run diff (spec/04 §3, ACD-007)."""
from decimal import Decimal
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from apps.identity.models import RoleAssignment
from apps.academic.models import Assessment, AssessmentScore, AssessmentType, ClassEnrollment
from apps.academic.services import (
    ScoreCsvError,
    apply_bulk_score_import,
    parse_score_csv,
    preview_bulk_score_import,
)
from apps.academic.tests.base import build_academic_fixture


def build_fixture(foundation_name="Yayasan Cendekia Mandiri"):
    fx = build_academic_fixture(foundation_name=foundation_name)
    ClassEnrollment.objects.create(
        foundation_id=fx['foundation'].id, student=fx['student'],
        class_group=fx['class_group'], enrolled_at='2026-07-01',
    )
    assessment = Assessment.objects.create(
        foundation_id=fx['foundation'].id, class_subject=fx['class_subject'],
        type=AssessmentType.SUMMATIVE, title='UH1',
        max_score=Decimal('100.00'), weight=Decimal('100.00'),
    )
    fx['assessment'] = assessment
    return fx


class ParseScoreCsvTests(TestCase):
    def test_parses_valid_rows(self):
        rows = parse_score_csv("nis,score,feedback\nX-001,85,Bagus\nX-002,90,\n")
        self.assertEqual(rows[0], {'row_number': 2, 'nis': 'X-001', 'score': Decimal('85'), 'feedback': 'Bagus'})
        self.assertEqual(rows[1]['score'], Decimal('90'))
        self.assertEqual(rows[1]['feedback'], '')

    def test_blank_score_is_none(self):
        rows = parse_score_csv("nis,score\nX-001,\n")
        self.assertIsNone(rows[0]['score'])

    def test_missing_columns_raises(self):
        with self.assertRaises(ScoreCsvError):
            parse_score_csv("student,marks\nX-001,85\n")

    def test_missing_nis_raises(self):
        with self.assertRaises(ScoreCsvError):
            parse_score_csv("nis,score\n,85\n")

    def test_invalid_score_raises(self):
        with self.assertRaises(ScoreCsvError):
            parse_score_csv("nis,score\nX-001,abc\n")


class PreviewBulkScoreImportTests(TestCase):
    def setUp(self):
        self.fx = build_fixture()

    def test_create_action_for_new_score(self):
        preview = preview_bulk_score_import(self.fx['assessment'], "nis,score\nX-001,85\n")
        self.assertEqual(preview[0]['action'], 'CREATE')
        self.assertEqual(preview[0]['new_score'], '85')
        self.assertIsNone(preview[0]['current_score'])
        self.assertEqual(AssessmentScore.objects.count(), 0)  # dry-run writes nothing

    def test_update_action_for_changed_score(self):
        AssessmentScore.objects.create(
            foundation_id=self.fx['foundation'].id, assessment=self.fx['assessment'],
            student=self.fx['student'], score=Decimal('70.00'),
        )
        preview = preview_bulk_score_import(self.fx['assessment'], "nis,score\nX-001,85\n")
        self.assertEqual(preview[0]['action'], 'UPDATE')
        self.assertEqual(preview[0]['current_score'], '70.00')

    def test_no_change_action_when_same(self):
        AssessmentScore.objects.create(
            foundation_id=self.fx['foundation'].id, assessment=self.fx['assessment'],
            student=self.fx['student'], score=Decimal('85'),
        )
        preview = preview_bulk_score_import(self.fx['assessment'], "nis,score\nX-001,85\n")
        self.assertEqual(preview[0]['action'], 'NO_CHANGE')

    def test_error_when_student_not_found(self):
        preview = preview_bulk_score_import(self.fx['assessment'], "nis,score\nZZZ-999,85\n")
        self.assertEqual(preview[0]['action'], 'ERROR')
        self.assertIn('STUDENT_NOT_FOUND', preview[0]['error'])

    def test_error_when_score_out_of_range(self):
        preview = preview_bulk_score_import(self.fx['assessment'], "nis,score\nX-001,150\n")
        self.assertEqual(preview[0]['action'], 'ERROR')
        self.assertIn('SCORE_OUT_OF_RANGE', preview[0]['error'])


class ApplyBulkScoreImportTests(TestCase):
    def setUp(self):
        self.fx = build_fixture()

    def test_commits_valid_row(self):
        result = apply_bulk_score_import(self.fx['assessment'], "nis,score\nX-001,85\n")
        self.assertEqual(result['imported'], 1)
        self.assertEqual(result['errors'], [])
        record = AssessmentScore.objects.get(assessment=self.fx['assessment'], student=self.fx['student'])
        self.assertEqual(record.score, Decimal('85'))

    def test_unknown_student_collected_as_error_not_raised(self):
        result = apply_bulk_score_import(self.fx['assessment'], "nis,score\nX-001,85\nZZZ-999,90\n")
        self.assertEqual(result['imported'], 1)
        self.assertEqual(len(result['errors']), 1)
        self.assertIn('STUDENT_NOT_FOUND', result['errors'][0]['error'])

    def test_out_of_range_collected_as_error(self):
        result = apply_bulk_score_import(self.fx['assessment'], "nis,score\nX-001,150\n")
        self.assertEqual(result['imported'], 0)
        self.assertEqual(len(result['errors']), 1)
        self.assertIn('SCORE_OUT_OF_RANGE', result['errors'][0]['error'])


class ImportScoresCsvViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.fx = build_fixture()
        RoleAssignment.all_tenants.create(
            foundation_id=self.fx['foundation'].id, user=self.fx['teacher_user'],
            role='teacher', scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.fx['school'].id,
        )
        self.client.force_authenticate(user=self.fx['teacher_user'])
        self.url = f'/api/v1/academic/assessments/{self.fx["assessment"].id}/import-scores-csv/'

    def _csv_file(self, content):
        return SimpleUploadedFile('scores.csv', content.encode('utf-8'), content_type='text/csv')

    def test_dry_run_default_writes_nothing(self):
        res = self.client.post(self.url, {'file': self._csv_file("nis,score\nX-001,85\n")}, format='multipart')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(res.json()['dry_run'])
        self.assertEqual(res.json()['rows'][0]['action'], 'CREATE')
        self.assertEqual(AssessmentScore.all_tenants.filter(assessment=self.fx['assessment']).count(), 0)

    def test_dry_run_false_commits(self):
        res = self.client.post(
            self.url, {'file': self._csv_file("nis,score\nX-001,85\n"), 'dry_run': 'false'}, format='multipart',
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertFalse(res.json()['dry_run'])
        self.assertEqual(res.json()['imported'], 1)
        self.assertEqual(AssessmentScore.all_tenants.filter(assessment=self.fx['assessment']).count(), 1)

    def test_malformed_csv_returns_400(self):
        res = self.client.post(self.url, {'file': self._csv_file("wrong,columns\na,b\n")}, format='multipart')
        self.assertEqual(res.status_code, 400)

    def test_cross_tenant_assessment_404(self):
        other_fx = build_fixture(foundation_name="Yayasan Cendekia Other")
        other_url = f'/api/v1/academic/assessments/{other_fx["assessment"].id}/import-scores-csv/'
        res = self.client.post(other_url, {'file': self._csv_file("nis,score\nX-001,85\n")}, format='multipart')
        self.assertEqual(res.status_code, 404)
