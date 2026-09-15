import datetime
from decimal import Decimal
from django.test import TestCase
from rest_framework.test import APIClient

from apps.identity.models import RoleAssignment
from apps.academic.models import (
    Assessment,
    AssessmentType,
    ClassEnrollment,
    ReportCard,
    ReportCardStatus,
)
from apps.academic.services import (
    ReportCardStateError,
    approve_report_card,
    generate_report_cards,
    get_visible_report_card,
    is_student_blocked_by_arrears,
    publish_report_card,
    revise_report_card,
    set_arrears_gate,
    set_assessment_score,
)
from apps.academic.tests.base import build_academic_fixture
from apps.finance.models import FeeCategory, FeePlan, FeeRecurrence, FeeType, Invoice, InvoiceStatus


def enroll_and_grade(fx):
    ClassEnrollment.objects.create(
        foundation_id=fx['foundation'].id,
        student=fx['student'],
        class_group=fx['class_group'],
        enrolled_at=datetime.date(2026, 7, 1),
    )
    a = Assessment.objects.create(
        foundation_id=fx['foundation'].id,
        class_subject=fx['class_subject'],
        type=AssessmentType.SUMMATIVE,
        title="UH1",
        max_score=Decimal('100.00'),
        weight=Decimal('100.00'),
        published=True,
    )
    set_assessment_score(a, fx['student'], score=Decimal('85'))


class GenerateReportCardsTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        enroll_and_grade(self.fx)

    def test_generate_creates_draft_with_grades_and_attendance(self):
        result = generate_report_cards(self.fx['class_group'], self.fx['term'])
        self.assertEqual(result, {'created': 1, 'updated': 0, 'skipped': 0})

        rc = ReportCard.objects.get(student=self.fx['student'], term=self.fx['term'])
        self.assertEqual(rc.status, ReportCardStatus.DRAFT)
        self.assertEqual(len(rc.grades_snapshot), 1)
        self.assertEqual(rc.grades_snapshot[0]['grade'], 85)

    def test_regenerate_updates_draft_idempotently(self):
        generate_report_cards(self.fx['class_group'], self.fx['term'])
        result = generate_report_cards(self.fx['class_group'], self.fx['term'])
        self.assertEqual(result, {'created': 0, 'updated': 1, 'skipped': 0})
        self.assertEqual(ReportCard.objects.filter(student=self.fx['student'], term=self.fx['term']).count(), 1)

    def test_regenerate_skips_approved_card(self):
        generate_report_cards(self.fx['class_group'], self.fx['term'])
        rc = ReportCard.objects.get(student=self.fx['student'], term=self.fx['term'])
        approve_report_card(rc)

        result = generate_report_cards(self.fx['class_group'], self.fx['term'])
        self.assertEqual(result, {'created': 0, 'updated': 0, 'skipped': 1})


class ReportCardStateMachineTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        enroll_and_grade(self.fx)
        generate_report_cards(self.fx['class_group'], self.fx['term'])
        self.rc = ReportCard.objects.get(student=self.fx['student'], term=self.fx['term'])

    def test_publish_before_approve_rejected(self):
        with self.assertRaises(ReportCardStateError):
            publish_report_card(self.rc)

    def test_approve_then_publish_succeeds(self):
        approve_report_card(self.rc)
        published = publish_report_card(self.rc)
        self.assertEqual(published.status, ReportCardStatus.PUBLISHED)
        self.assertTrue(published.pdf_key)

    def test_revise_only_allowed_when_published(self):
        with self.assertRaises(ReportCardStateError):
            revise_report_card(self.rc)

        approve_report_card(self.rc)
        publish_report_card(self.rc)
        self.rc.refresh_from_db()
        revised = revise_report_card(self.rc)

        self.assertEqual(revised.version, 2)
        self.assertTrue(revised.is_current)
        self.rc.refresh_from_db()
        self.assertFalse(self.rc.is_current)
        self.assertEqual(self.rc.status, ReportCardStatus.PUBLISHED)  # old version stays immutable


class ArrearsGateTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        enroll_and_grade(self.fx)
        generate_report_cards(self.fx['class_group'], self.fx['term'])
        self.rc = ReportCard.objects.get(student=self.fx['student'], term=self.fx['term'])
        approve_report_card(self.rc)
        publish_report_card(self.rc)
        self.rc.refresh_from_db()

    def test_visible_by_default(self):
        result = get_visible_report_card(self.rc)
        self.assertTrue(result['visible'])

    def test_blocked_when_gate_on_and_overdue(self):
        set_arrears_gate(self.fx['school'], True)

        fee_type = FeeType.objects.create(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'],
            code='SPP', name='SPP', category=FeeCategory.SPP, recurrence=FeeRecurrence.MONTHLY,
            default_amount=Decimal('500000.00'),
        )
        Invoice.objects.create(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            number='INV/TEST/2026/000001',
            period='2026-01',
            due_date=datetime.date(2026, 1, 10),
            total=Decimal('500000.00'),
            status=InvoiceStatus.ISSUED,
        )

        self.assertTrue(is_student_blocked_by_arrears(self.fx['student'], self.fx['school']))
        result = get_visible_report_card(self.rc)
        self.assertFalse(result['visible'])
        self.assertEqual(result['reason'], 'ARREARS')

    def test_not_blocked_when_gate_off_even_with_overdue(self):
        FeeType.objects.create(
            foundation_id=self.fx['foundation'].id, school=self.fx['school'],
            code='SPP2', name='SPP', category=FeeCategory.SPP, recurrence=FeeRecurrence.MONTHLY,
            default_amount=Decimal('500000.00'),
        )
        Invoice.objects.create(
            foundation_id=self.fx['foundation'].id,
            school=self.fx['school'],
            student=self.fx['student'],
            number='INV/TEST/2026/000002',
            period='2026-02',
            due_date=datetime.date(2026, 1, 10),
            total=Decimal('500000.00'),
            status=InvoiceStatus.ISSUED,
        )
        self.assertFalse(is_student_blocked_by_arrears(self.fx['student'], self.fx['school']))


class ReportCardViewsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.fx = build_academic_fixture()
        enroll_and_grade(self.fx)
        RoleAssignment.all_tenants.create(
            foundation_id=self.fx['foundation'].id,
            user=self.fx['teacher_user'],
            role='school_admin',
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=self.fx['school'].id,
        )

    def test_generate_approve_publish_flow_via_api(self):
        self.client.force_authenticate(user=self.fx['teacher_user'])
        res_gen = self.client.post('/api/v1/academic/report-cards/generate/', {
            'class_group_id': self.fx['class_group'].id, 'term_id': self.fx['term'].id,
        }, format='json')
        self.assertEqual(res_gen.status_code, 200, res_gen.content)
        self.assertEqual(res_gen.json()['created'], 1)

        rc_id = ReportCard.all_tenants.get(student=self.fx['student'], term=self.fx['term']).id

        res_approve = self.client.post(f'/api/v1/academic/report-cards/{rc_id}/approve/')
        self.assertEqual(res_approve.status_code, 200, res_approve.content)

        res_publish = self.client.post(f'/api/v1/academic/report-cards/{rc_id}/publish/')
        self.assertEqual(res_publish.status_code, 200, res_publish.content)
        self.assertEqual(res_publish.json()['status'], ReportCardStatus.PUBLISHED)

        res_student_view = self.client.get(
            f'/api/v1/academic/students/{self.fx["student"].id}/report-cards/?term_id={self.fx["term"].id}'
        )
        self.assertEqual(res_student_view.status_code, 200)
        self.assertTrue(res_student_view.json()['visible'])

    def test_arrears_policy_toggle_via_api(self):
        self.client.force_authenticate(user=self.fx['teacher_user'])
        res_get = self.client.get(f'/api/v1/academic/schools/{self.fx["school"].id}/report-card-policy/')
        self.assertEqual(res_get.status_code, 200)
        self.assertFalse(res_get.json()['block_rapor_on_arrears'])

        res_patch = self.client.patch(
            f'/api/v1/academic/schools/{self.fx["school"].id}/report-card-policy/',
            {'block_rapor_on_arrears': True}, format='json',
        )
        self.assertEqual(res_patch.status_code, 200)
        self.assertTrue(res_patch.json()['block_rapor_on_arrears'])
