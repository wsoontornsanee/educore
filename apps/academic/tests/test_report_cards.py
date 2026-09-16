import datetime
from decimal import Decimal
from unittest import mock
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
    get_curriculum_phase,
    get_visible_report_card,
    is_student_blocked_by_arrears,
    publish_report_card,
    render_report_card_html,
    revise_report_card,
    set_arrears_gate,
    set_assessment_score,
    set_report_card_content,
    suggest_objective_narrative,
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
        patcher = mock.patch('apps.core.storage._client')
        self.mock_gcs_client = patcher.start()
        self.addCleanup(patcher.stop)
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
        patcher = mock.patch('apps.core.storage._client')
        self.mock_gcs_client = patcher.start()
        self.addCleanup(patcher.stop)
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
        patcher = mock.patch('apps.core.storage._client')
        self.mock_gcs_client = patcher.start()
        self.addCleanup(patcher.stop)
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


class GetCurriculumPhaseTests(TestCase):
    def test_phase_boundaries(self):
        self.assertEqual(get_curriculum_phase(1), 'A')
        self.assertEqual(get_curriculum_phase(2), 'A')
        self.assertEqual(get_curriculum_phase(3), 'B')
        self.assertEqual(get_curriculum_phase(4), 'B')
        self.assertEqual(get_curriculum_phase(5), 'C')
        self.assertEqual(get_curriculum_phase(6), 'C')
        self.assertEqual(get_curriculum_phase(7), 'D')
        self.assertEqual(get_curriculum_phase(9), 'D')
        self.assertEqual(get_curriculum_phase(10), 'E')
        self.assertEqual(get_curriculum_phase(11), 'F')
        self.assertEqual(get_curriculum_phase(12), 'F')


class SetReportCardContentTests(TestCase):
    def setUp(self):
        patcher = mock.patch('apps.core.storage._client')
        self.mock_gcs_client = patcher.start()
        self.addCleanup(patcher.stop)
        self.fx = build_academic_fixture()
        enroll_and_grade(self.fx)
        generate_report_cards(self.fx['class_group'], self.fx['term'])
        self.rc = ReportCard.objects.get(student=self.fx['student'], term=self.fx['term'])

    def test_updates_narrative_and_extracurricular_and_decision(self):
        updated = set_report_card_content(
            self.rc,
            narrative='Sangat baik.',
            extracurricular_notes=[{'name': 'Pramuka', 'grade': 'Baik'}],
            promotion_decision='NAIK KE KELAS XI',
        )
        self.assertEqual(updated.narrative, 'Sangat baik.')
        self.assertEqual(updated.extracurricular_notes, [{'name': 'Pramuka', 'grade': 'Baik'}])
        self.assertEqual(updated.promotion_decision, 'NAIK KE KELAS XI')

    def test_subject_narrative_merges_into_grades_snapshot(self):
        subject_code = self.rc.grades_snapshot[0]['subject_code']
        updated = set_report_card_content(self.rc, subject_narratives={subject_code: 'Sangat menguasai materi.'})
        self.assertEqual(updated.grades_snapshot[0]['objective_narrative'], 'Sangat menguasai materi.')

    def test_rejected_once_published(self):
        approve_report_card(self.rc)
        publish_report_card(self.rc)
        self.rc.refresh_from_db()
        with self.assertRaises(ReportCardStateError):
            set_report_card_content(self.rc, narrative='Too late.')


class RenderReportCardHtmlTests(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture()
        enroll_and_grade(self.fx)
        generate_report_cards(self.fx['class_group'], self.fx['term'])
        self.rc = ReportCard.objects.get(student=self.fx['student'], term=self.fx['term'])

    def test_contains_identity_and_grade_data(self):
        html_out = render_report_card_html(self.rc)
        self.assertIn(self.fx['foundation'].brand_name, html_out)
        self.assertIn(self.fx['school'].name, html_out)
        self.assertIn(self.fx['school'].npsn, html_out)
        self.assertIn(self.fx['student'].person.full_name, html_out)
        self.assertIn(self.fx['student'].nisn, html_out)
        self.assertIn(self.fx['class_group'].name, html_out)
        self.assertIn('>E<', html_out)  # Fase for grade_level=10
        self.assertIn('85', html_out)  # the graded score
        self.assertIn('Baik', html_out)  # descriptor for 85
        self.assertEqual(html_out.count('class="page"'), 2)

    def test_homeroom_teacher_name_and_nip_present(self):
        html_out = render_report_card_html(self.rc)
        self.assertIn(self.fx['teacher'].person.full_name, html_out)
        self.assertIn(self.fx['teacher'].nip, html_out)

    def test_principal_falls_back_when_no_school_admin_assigned(self):
        html_out = render_report_card_html(self.rc)
        self.assertIn('(...............................)', html_out)

    def test_principal_resolved_from_school_admin_role(self):
        principal_person = self.fx['teacher'].person  # reuse an existing Staff for simplicity
        RoleAssignment.all_tenants.create(
            foundation_id=self.fx['foundation'].id,
            user=self.fx['teacher_user'],
            role=RoleAssignment.ROLE_SCHOOL_ADMIN,
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=self.fx['school'].id,
        )
        html_out = render_report_card_html(self.rc)
        self.assertIn(principal_person.full_name, html_out)

    def test_decision_falls_back_when_unset(self):
        html_out = render_report_card_html(self.rc)
        self.assertIn('BELUM DITENTUKAN', html_out)

    def test_extracurricular_placeholder_when_empty(self):
        html_out = render_report_card_html(self.rc)
        self.assertIn('Tidak ada catatan ekstrakurikuler', html_out)

    def test_subject_narrative_and_extracurricular_render(self):
        subject_code = self.rc.grades_snapshot[0]['subject_code']
        set_report_card_content(
            self.rc,
            subject_narratives={subject_code: 'Sangat menguasai konsep aljabar.'},
            extracurricular_notes=[{'name': 'Pramuka', 'grade': 'Baik'}],
        )
        self.rc.refresh_from_db()
        html_out = render_report_card_html(self.rc)
        self.assertIn('Sangat menguasai konsep aljabar.', html_out)
        self.assertIn('Pramuka', html_out)

    def test_narrative_is_html_escaped(self):
        set_report_card_content(self.rc, narrative='<script>alert(1)</script>')
        self.rc.refresh_from_db()
        html_out = render_report_card_html(self.rc)
        self.assertNotIn('<script>alert(1)</script>', html_out)
        self.assertIn('&lt;script&gt;', html_out)


class ReportCardContentViewTests(TestCase):
    def setUp(self):
        patcher = mock.patch('apps.core.storage._client')
        self.mock_gcs_client = patcher.start()
        self.addCleanup(patcher.stop)
        self.client = APIClient()
        self.fx = build_academic_fixture()
        enroll_and_grade(self.fx)
        generate_report_cards(self.fx['class_group'], self.fx['term'])
        self.rc = ReportCard.objects.get(student=self.fx['student'], term=self.fx['term'])
        RoleAssignment.all_tenants.create(
            foundation_id=self.fx['foundation'].id,
            user=self.fx['teacher_user'],
            role='school_admin',
            scope_type=RoleAssignment.SCOPE_SCHOOL,
            scope_id=self.fx['school'].id,
        )

    def test_patch_content_via_api(self):
        self.client.force_authenticate(user=self.fx['teacher_user'])
        res = self.client.patch(
            f'/api/v1/academic/report-cards/{self.rc.id}/content/',
            {'narrative': 'Catatan wali kelas.', 'promotion_decision': 'NAIK KE KELAS XI'},
            format='json',
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['narrative'], 'Catatan wali kelas.')
        self.assertEqual(res.json()['promotion_decision'], 'NAIK KE KELAS XI')

    def test_patch_content_rejected_once_published(self):
        approve_report_card(self.rc)
        publish_report_card(self.rc)
        self.rc.refresh_from_db()

        self.client.force_authenticate(user=self.fx['teacher_user'])
        res = self.client.patch(
            f'/api/v1/academic/report-cards/{self.rc.id}/content/',
            {'narrative': 'Too late.'}, format='json',
        )
        self.assertEqual(res.status_code, 400)


class SuggestObjectiveNarrativeTests(TestCase):
    def test_sangat_baik_template(self):
        self.assertEqual(
            suggest_objective_narrative('Matematika', 95),
            'Sangat baik dalam pencapaian kompetensi Matematika.',
        )

    def test_baik_template(self):
        self.assertEqual(
            suggest_objective_narrative('Matematika', 85),
            'Baik dalam pencapaian kompetensi Matematika, terus tingkatkan.',
        )

    def test_cukup_template(self):
        self.assertEqual(
            suggest_objective_narrative('Matematika', 75),
            'Cukup dalam pencapaian kompetensi Matematika; perlu penguatan lebih lanjut.',
        )

    def test_perlu_bimbingan_template(self):
        self.assertEqual(
            suggest_objective_narrative('Matematika', 50),
            'Memerlukan bimbingan intensif dalam pencapaian kompetensi Matematika.',
        )

    def test_no_grade_yet(self):
        self.assertEqual(
            suggest_objective_narrative('Matematika', None),
            'Belum ada nilai untuk Matematika pada periode ini.',
        )


class SuggestNarrativesViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.fx = build_academic_fixture()
        enroll_and_grade(self.fx)
        generate_report_cards(self.fx['class_group'], self.fx['term'])
        self.rc = ReportCard.objects.get(student=self.fx['student'], term=self.fx['term'])
        RoleAssignment.all_tenants.create(
            foundation_id=self.fx['foundation'].id, user=self.fx['teacher_user'],
            role='teacher', scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.fx['school'].id,
        )

    def test_returns_suggestion_per_subject_without_saving(self):
        self.client.force_authenticate(user=self.fx['teacher_user'])
        res = self.client.get(f'/api/v1/academic/report-cards/{self.rc.id}/suggest-narratives/')
        self.assertEqual(res.status_code, 200, res.content)
        suggestions = res.json()['suggestions']
        self.assertEqual(len(suggestions), 1)
        self.assertIn('pencapaian kompetensi', suggestions[0]['suggestion'])

        self.rc.refresh_from_db()
        self.assertNotIn('objective_narrative', self.rc.grades_snapshot[0])  # never auto-written

    def test_cross_tenant_report_card_404(self):
        other_fx = build_academic_fixture(foundation_name="Yayasan Cendekia Other Narrative")
        enroll_and_grade(other_fx)
        generate_report_cards(other_fx['class_group'], other_fx['term'])
        other_rc = ReportCard.objects.get(student=other_fx['student'], term=other_fx['term'])

        self.client.force_authenticate(user=self.fx['teacher_user'])
        res = self.client.get(f'/api/v1/academic/report-cards/{other_rc.id}/suggest-narratives/')
        self.assertEqual(res.status_code, 404)
