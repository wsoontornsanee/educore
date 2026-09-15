from decimal import Decimal
from django.test import TestCase

from apps.academic.models import Assessment, AssessmentScore, AssessmentType
from apps.academic.services import (
    ReasonRequiredError,
    ScoreConflictError,
    ScoreOutOfRangeError,
    WeightConfigError,
    compute_term_grade,
    publish_assessment,
    set_assessment_score,
)
from apps.academic.tests.base import build_academic_fixture


class ScoreEntryTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        self.assessment = Assessment.objects.create(
            foundation_id=self.fx['foundation'].id,
            class_subject=self.fx['class_subject'],
            type=AssessmentType.SUMMATIVE,
            title="Ulangan Harian 1",
            max_score=Decimal('100.00'),
            weight=Decimal('40.00'),
        )

    def test_set_score_within_range_succeeds(self):
        record = set_assessment_score(self.assessment, self.fx['student'], score=Decimal('85'))
        self.assertEqual(record.score, Decimal('85.00'))
        self.assertEqual(record.descriptor, 'Baik')

    def test_score_above_max_rejected(self):
        with self.assertRaises(ScoreOutOfRangeError):
            set_assessment_score(self.assessment, self.fx['student'], score=Decimal('150'))

    def test_score_below_zero_rejected(self):
        with self.assertRaises(ScoreOutOfRangeError):
            set_assessment_score(self.assessment, self.fx['student'], score=Decimal('-5'))

    def test_descriptor_bands(self):
        cases = [(95, 'Sangat Baik'), (85, 'Baik'), (75, 'Cukup'), (50, 'Perlu Bimbingan')]
        for score, expected in cases:
            record = set_assessment_score(self.assessment, self.fx['student'], score=Decimal(score))
            self.assertEqual(record.descriptor, expected)

    def test_changing_published_score_without_reason_rejected(self):
        set_assessment_score(self.assessment, self.fx['student'], score=Decimal('70'))
        self.assessment.published = True
        self.assessment.save()

        with self.assertRaises(ReasonRequiredError):
            set_assessment_score(self.assessment, self.fx['student'], score=Decimal('80'))

    def test_changing_published_score_with_reason_succeeds(self):
        set_assessment_score(self.assessment, self.fx['student'], score=Decimal('70'))
        self.assessment.published = True
        self.assessment.save()

        record = set_assessment_score(
            self.assessment, self.fx['student'], score=Decimal('80'), reason="Koreksi input"
        )
        self.assertEqual(record.score, Decimal('80.00'))

    def test_first_time_publish_does_not_require_reason(self):
        self.assessment.published = True
        self.assessment.save()
        record = set_assessment_score(self.assessment, self.fx['student'], score=Decimal('70'))
        self.assertEqual(record.score, Decimal('70.00'))


class ScoreConcurrencyTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        self.assessment = Assessment.objects.create(
            foundation_id=self.fx['foundation'].id,
            class_subject=self.fx['class_subject'],
            type=AssessmentType.SUMMATIVE,
            title="Ulangan Harian 1",
            max_score=Decimal('100.00'),
            weight=Decimal('40.00'),
        )

    def test_first_score_always_succeeds_regardless_of_expected_version(self):
        record = set_assessment_score(self.assessment, self.fx['student'], score=Decimal('85'), expected_version=99)
        self.assertEqual(record.version, 1)

    def test_omitting_expected_version_overwrites_unconditionally(self):
        set_assessment_score(self.assessment, self.fx['student'], score=Decimal('70'))
        record = set_assessment_score(self.assessment, self.fx['student'], score=Decimal('80'))
        self.assertEqual(record.score, Decimal('80.00'))
        self.assertEqual(record.version, 2)

    def test_stale_expected_version_rejected_with_current_values(self):
        first = set_assessment_score(self.assessment, self.fx['student'], score=Decimal('70'))
        self.assertEqual(first.version, 1)

        # Teacher A saves, bumping to version 2.
        second = set_assessment_score(self.assessment, self.fx['student'], score=Decimal('75'), expected_version=1)
        self.assertEqual(second.version, 2)

        # Teacher B, who read version 1 before A saved, now tries to write — must be rejected,
        # not silently overwrite A's change (TCH-007).
        with self.assertRaises(ScoreConflictError) as ctx:
            set_assessment_score(self.assessment, self.fx['student'], score=Decimal('60'), expected_version=1)
        self.assertEqual(ctx.exception.current_score, Decimal('75.00'))
        self.assertEqual(ctx.exception.current_version, 2)

        # And A's write is intact — not clobbered by B's rejected attempt.
        current = AssessmentScore.objects.get(assessment=self.assessment, student=self.fx['student'])
        self.assertEqual(current.score, Decimal('75.00'))

    def test_matching_expected_version_succeeds(self):
        first = set_assessment_score(self.assessment, self.fx['student'], score=Decimal('70'))
        record = set_assessment_score(self.assessment, self.fx['student'], score=Decimal('90'), expected_version=first.version)
        self.assertEqual(record.score, Decimal('90.00'))
        self.assertEqual(record.version, 2)


class PublishAssessmentTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()

    def _make_assessment(self, weight, assessment_type=AssessmentType.SUMMATIVE):
        return Assessment.objects.create(
            foundation_id=self.fx['foundation'].id,
            class_subject=self.fx['class_subject'],
            type=assessment_type,
            title=f"Assessment {weight}",
            max_score=Decimal('100.00'),
            weight=Decimal(weight),
        )

    def test_publish_within_budget_succeeds(self):
        a1 = self._make_assessment(60)
        publish_assessment(a1)
        self.assertTrue(a1.published)

    def test_publish_exceeding_100_percent_rejected(self):
        a1 = self._make_assessment(60)
        publish_assessment(a1)
        a2 = self._make_assessment(50)
        with self.assertRaises(WeightConfigError):
            publish_assessment(a2)

    def test_formative_type_not_counted_toward_weight_budget(self):
        a1 = self._make_assessment(80, assessment_type=AssessmentType.FORMATIVE)
        publish_assessment(a1)
        a2 = self._make_assessment(80, assessment_type=AssessmentType.FORMATIVE)
        # Formative types are unweighted; no budget conflict.
        publish_assessment(a2)
        self.assertTrue(a2.published)


class ComputeTermGradeTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        self.a1 = Assessment.objects.create(
            foundation_id=self.fx['foundation'].id,
            class_subject=self.fx['class_subject'],
            type=AssessmentType.SUMMATIVE,
            title="UH1",
            max_score=Decimal('100.00'),
            weight=Decimal('40.00'),
        )
        self.a2 = Assessment.objects.create(
            foundation_id=self.fx['foundation'].id,
            class_subject=self.fx['class_subject'],
            type=AssessmentType.EXAM,
            title="UAS",
            max_score=Decimal('50.00'),
            weight=Decimal('60.00'),
        )
        publish_assessment(self.a1)
        publish_assessment(self.a2)

    def test_incomplete_weight_config_raises(self):
        Assessment.objects.filter(pk=self.a2.pk).update(weight=Decimal('50.00'))
        with self.assertRaises(WeightConfigError):
            compute_term_grade(self.fx['student'], self.fx['class_subject'])

    def test_missing_score_returns_incomplete(self):
        set_assessment_score(self.a1, self.fx['student'], score=Decimal('80'))
        result = compute_term_grade(self.fx['student'], self.fx['class_subject'])
        self.assertEqual(result['status'], 'INCOMPLETE')
        self.assertIn('UAS', result['missing_assessments'])

    def test_weighted_grade_computed_and_rounded_half_up(self):
        set_assessment_score(self.a1, self.fx['student'], score=Decimal('80'))  # 80/100*100=80 * 40% = 32
        set_assessment_score(self.a2, self.fx['student'], score=Decimal('45'))  # 45/50*100=90 * 60% = 54
        result = compute_term_grade(self.fx['student'], self.fx['class_subject'])
        self.assertEqual(result['status'], 'COMPLETE')
        self.assertEqual(result['grade'], 86)  # 32 + 54 = 86.00
        self.assertIsNotNone(result['formula'])
