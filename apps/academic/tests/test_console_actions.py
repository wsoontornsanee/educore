"""Web console: Akademik write actions (grade/return homework, generate/approve/
publish/revise report cards). Spec:
docs/superpowers/specs/2026-09-19-web-console-akademik-design.md ("Follow-on: write actions")."""
import datetime
from decimal import Decimal
from unittest import mock

from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.academic.models import (
    ClassEnrollment,
    Homework,
    HomeworkSubmission,
    HomeworkSubmissionStatus as S,
    ReportCard,
    ReportCardStatus,
)
from apps.academic.tests.base import build_academic_fixture
from apps.academic.tests.test_permission_slips import make_guardian
from apps.academic.tests.test_report_cards import enroll_and_grade
from apps.academic.services import generate_report_cards
from apps.core.models import AuditEvent
from apps.identity.models import Person, RoleAssignment, School, Staff, User
from educore.middleware.tenancy import set_current_foundation_id

QUEUE = '/web/academic/grading-queue/'
CARDS = '/web/academic/report-cards/'


def flashes(response):
    """The flash added by this response: the session accumulates unread messages
    across requests within one test, so only the last one is this response's."""
    return [(m.level_tag, str(m)) for m in get_messages(response.wsgi_request)][-1:]


class ConsoleActionTestBase(TestCase):
    def setUp(self):
        self.fx = build_academic_fixture("Yayasan Aksi Console")
        self.foundation = self.fx['foundation']
        self.school = self.fx['school']
        self.teacher = self.fx['teacher']
        set_current_foundation_id(self.foundation.id)
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id, user=self.teacher.user,
            role='teacher', scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=self.school.id,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.teacher.user)

    def make_staff_user(self, role, school=None, phone='+628119990100', with_staff=True):
        school = school or self.school
        person = Person.all_tenants.create(foundation_id=self.foundation.id, full_name=f'Tester {role}')
        user = User.objects.create(
            foundation_id=self.foundation.id, phone_e164=phone, email=f'{phone[-4:]}@aksi.test',
            full_name=f'Tester {role}',
        )
        RoleAssignment.all_tenants.create(
            foundation_id=self.foundation.id, user=user, role=role,
            scope_type=RoleAssignment.SCOPE_SCHOOL, scope_id=school.id,
        )
        if with_staff:
            Staff.all_tenants.create(
                foundation_id=self.foundation.id, person=person, user=user, school=school,
                nip=f'1990{phone[-4:]}', employment_type=Staff.TYPE_PERMANENT,
                join_date=datetime.date(2021, 1, 1), status=Staff.STATUS_ACTIVE,
            )
        return user

    def other_school_user(self, role='teacher'):
        other = School.all_tenants.create(
            foundation_id=self.foundation.id, name='SMA Lain', npsn='20999999', level=School.LEVEL_SMA,
        )
        return self.make_staff_user(role, school=other, phone='+628119990200')


class GradeAndReturnTests(ConsoleActionTestBase):
    def setUp(self):
        super().setUp()
        homework = Homework.objects.create(
            foundation_id=self.foundation.id, class_subject=self.fx['class_subject'], title='PR Aljabar',
            assigned_at=timezone.now() - datetime.timedelta(days=3), due_at=timezone.now() - datetime.timedelta(days=1),
        )
        self.submission = HomeworkSubmission.objects.create(
            foundation_id=self.foundation.id, homework=homework, student=self.fx['student'],
            submitted_at=timezone.now() - datetime.timedelta(hours=5), status=S.SUBMITTED,
        )
        self.grade_url = f'{QUEUE}{self.submission.id}/grade/'
        self.return_url = f'{QUEUE}{self.submission.id}/return/'

    def reload(self):
        return HomeworkSubmission.all_tenants.get(id=self.submission.id)

    def test_grade_saves_score_feedback_actor_and_audits(self):
        res = self.client.post(self.grade_url, {'score': '85.5', 'feedback': 'Bagus', 'class_subject': '7'})
        self.assertEqual(res.status_code, 302)
        self.assertEqual(res['Location'], f'{QUEUE}?class_subject=7')
        sub = self.reload()
        self.assertEqual((sub.status, sub.score, sub.feedback), (S.GRADED, Decimal('85.50'), 'Bagus'))
        self.assertEqual(sub.graded_by_id, self.teacher.user_id)
        self.assertEqual(flashes(res), [('success', 'Nilai tersimpan.')])
        self.assertTrue(AuditEvent.objects.filter(
            action='academic.homework_submission.graded', entity_id=str(self.submission.id)).exists())

    def test_grade_boundaries_accepted(self):
        for value in ('0', '100', '99.99'):
            HomeworkSubmission.all_tenants.filter(id=self.submission.id).update(status=S.SUBMITTED)
            res = self.client.post(self.grade_url, {'score': value})
            self.assertEqual(flashes(res)[0][0], 'success', value)

    def test_grade_rejects_invalid_scores_without_changing_state(self):
        for value in ('', 'abc', '-1', '100.01', '5.123', 'NaN', 'Infinity'):
            res = self.client.post(self.grade_url, {'score': value})
            self.assertEqual(res.status_code, 302)
            self.assertEqual(flashes(res)[0][0], 'error', value)
            self.assertEqual(self.reload().status, S.SUBMITTED, value)

    def test_grade_redirect_ignores_non_numeric_class_subject(self):
        res = self.client.post(self.grade_url, {'score': '80', 'class_subject': 'evil//x'})
        self.assertEqual(res['Location'], QUEUE)

    def test_already_graded_or_returned_is_refused(self):
        HomeworkSubmission.all_tenants.filter(id=self.submission.id).update(status=S.GRADED, score=Decimal('70'))
        res = self.client.post(self.grade_url, {'score': '10'})
        self.assertEqual(flashes(res)[0][0], 'error')
        self.assertEqual(self.reload().score, Decimal('70.00'))
        res = self.client.post(self.return_url, {'feedback': 'ulang'})
        self.assertEqual(flashes(res)[0][0], 'error')
        self.assertEqual(self.reload().status, S.GRADED)

    def test_return_requires_feedback_and_clears_state(self):
        res = self.client.post(self.return_url, {'feedback': '   '})
        self.assertEqual(flashes(res)[0][0], 'error')
        self.assertEqual(self.reload().status, S.SUBMITTED)
        res = self.client.post(self.return_url, {'feedback': 'Perbaiki nomor 3'})
        self.assertEqual(flashes(res), [('success', 'Tugas dikembalikan ke siswa.')])
        sub = self.reload()
        self.assertEqual((sub.status, sub.feedback, sub.score), (S.RETURNED, 'Perbaiki nomor 3', None))
        self.assertTrue(AuditEvent.objects.filter(
            action='academic.homework_submission.returned', entity_id=str(self.submission.id)).exists())

    def test_permission_and_scope_gates(self):
        counsellor = self.make_staff_user('counsellor', phone='+628119990300')  # grades.read only
        no_staff = self.make_staff_user('teacher', phone='+628119990400', with_staff=False)
        other_school = self.other_school_user()
        _guardian, guardian_user = make_guardian(
            self.foundation, self.school, "+628133000009", "wali@aksi.test", "Wali Aksi",
            "3471010101022009", student=self.fx['student'],
        )
        # no permission -> sent home (never a raw JSON 403); no Staff profile / other school -> 404
        home = reverse('web-console-home')
        for user, expected in ((counsellor, 302), (guardian_user, 302), (no_staff, 404), (other_school, 404)):
            self.client.force_authenticate(user=user)
            for url in (self.grade_url, self.return_url):
                res = self.client.post(url, {'score': '90', 'feedback': 'x'})
                self.assertEqual(res.status_code, expected, (user.full_name, url))
                if expected == 302:
                    self.assertEqual(res['Location'], home, (user.full_name, url))
            self.assertEqual(self.reload().status, S.SUBMITTED)
        self.client.force_authenticate(user=None)
        self.assertIn(self.client.post(self.grade_url, {'score': '90'}).status_code, (401, 403))

    def test_get_is_not_allowed_and_unknown_or_cross_tenant_404(self):
        self.assertEqual(self.client.get(self.grade_url).status_code, 405)
        self.assertEqual(self.client.post(f'{QUEUE}999999/grade/', {'score': '1'}).status_code, 404)
        other = build_academic_fixture("Yayasan Lain Aksi")
        set_current_foundation_id(other['foundation'].id)
        hw = Homework.all_tenants.create(
            foundation_id=other['foundation'].id, class_subject=other['class_subject'], title='X',
            assigned_at=timezone.now(), due_at=timezone.now(),
        )
        foreign = HomeworkSubmission.all_tenants.create(
            foundation_id=other['foundation'].id, homework=hw, student=other['student'],
            submitted_at=timezone.now(), status=S.SUBMITTED,
        )
        set_current_foundation_id(self.foundation.id)
        self.assertEqual(self.client.post(f'{QUEUE}{foreign.id}/grade/', {'score': '1'}).status_code, 404)
        self.assertEqual(HomeworkSubmission.all_tenants.get(id=foreign.id).status, S.SUBMITTED)

    def test_queue_page_shows_forms_only_to_writers(self):
        res = self.client.get(QUEUE)
        self.assertContains(res, f'/web/academic/grading-queue/{self.submission.id}/grade/')
        self.assertContains(res, 'Kembalikan')
        counsellor = self.make_staff_user('counsellor', phone='+628119990300')
        self.client.force_authenticate(user=counsellor)
        res = self.client.get(QUEUE)
        self.assertEqual(res.status_code, 200)
        self.assertNotContains(res, '/grade/')


class ReportCardActionTests(ConsoleActionTestBase):
    def setUp(self):
        super().setUp()
        enroll_and_grade(self.fx)
        generate_report_cards(self.fx['class_group'], self.fx['term'])
        self.card = ReportCard.objects.get(student=self.fx['student'], term=self.fx['term'])
        self.admin = self.make_staff_user('school_admin', phone='+628119990500')

    def set_status(self, status):
        ReportCard.all_tenants.filter(id=self.card.id).update(status=status)

    def reload(self):
        return ReportCard.all_tenants.get(id=self.card.id)

    def url(self, action):
        return f'{CARDS}{self.card.id}/{action}/'

    # generate
    def test_generate_creates_drafts_and_reports_counts(self):
        ReportCard.all_tenants.filter(id=self.card.id).update(deleted_at=timezone.now())
        res = self.client.post(f'{CARDS}generate/', {
            'class_group': self.fx['class_group'].id, 'term': self.fx['term'].id,
        })
        self.assertEqual(res.status_code, 302)
        self.assertIn('term=', res['Location'])
        self.assertEqual(flashes(res), [('success', 'Rapor dibuat: 1 baru, 0 diperbarui, 0 dilewati.')])
        self.assertEqual(ReportCard.all_tenants.filter(status=ReportCardStatus.DRAFT).count(), 1)

    def test_generate_skips_non_draft_cards(self):
        self.set_status(ReportCardStatus.APPROVED)
        res = self.client.post(f'{CARDS}generate/', {
            'class_group': self.fx['class_group'].id, 'term': self.fx['term'].id,
        })
        self.assertEqual(flashes(res), [('success', 'Rapor dibuat: 0 baru, 0 diperbarui, 1 dilewati.')])
        self.assertEqual(self.reload().status, ReportCardStatus.APPROVED)

    def test_generate_rejects_bad_input(self):
        for data in ({}, {'class_group': 'x', 'term': 'y'}, {'class_group': 999999, 'term': self.fx['term'].id}):
            res = self.client.post(f'{CARDS}generate/', data)
            self.assertEqual(flashes(res)[0][0], 'error', data)

    def test_generate_rejects_class_of_another_school_and_mismatched_term(self):
        other_school_user = self.other_school_user()
        self.client.force_authenticate(user=other_school_user)
        res = self.client.post(f'{CARDS}generate/', {
            'class_group': self.fx['class_group'].id, 'term': self.fx['term'].id,
        })
        self.assertEqual(flashes(res)[0][0], 'error')  # class outside the user's schools
        from apps.academic.models import AcademicYear, Term
        year = AcademicYear.objects.create(
            foundation_id=self.foundation.id, school=self.school, name='2027/2028',
            start_date=datetime.date(2027, 7, 1), end_date=datetime.date(2028, 6, 30), is_active=False,
        )
        term = Term.objects.create(
            foundation_id=self.foundation.id, academic_year=year, name='S1', term_no=1,
            start_date=datetime.date(2027, 7, 1), end_date=datetime.date(2027, 12, 20),
        )
        self.client.force_authenticate(user=self.teacher.user)
        res = self.client.post(f'{CARDS}generate/', {'class_group': self.fx['class_group'].id, 'term': term.id})
        self.assertEqual(flashes(res), [('error', 'Semester tidak sesuai dengan tahun ajaran kelas.')])

    # approve / publish / revise
    def test_approve_publish_revise_lifecycle_with_audit(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(self.url('approve'))
        self.assertEqual(res['Location'], f'{CARDS}{self.card.id}/')
        self.assertEqual(self.reload().status, ReportCardStatus.APPROVED)
        self.assertEqual(flashes(res), [('success', 'Rapor disetujui.')])
        with mock.patch('apps.academic.services.render_report_card_pdf', return_value='rapor/key.pdf'):
            res = self.client.post(self.url('publish'))
        self.assertEqual(flashes(res), [('success', 'Rapor diterbitkan.')])
        card = self.reload()
        self.assertEqual((card.status, card.pdf_key), (ReportCardStatus.PUBLISHED, 'rapor/key.pdf'))
        res = self.client.post(self.url('revise'))
        new_card = ReportCard.all_tenants.get(student=self.fx['student'], is_current=True)
        self.assertNotEqual(new_card.id, self.card.id)
        self.assertEqual((new_card.status, new_card.version), (ReportCardStatus.DRAFT, 2))
        self.assertEqual(res['Location'], f'{CARDS}{new_card.id}/')
        for action in ('approved', 'published', 'revised'):
            self.assertTrue(AuditEvent.objects.filter(action=f'academic.report_card.{action}').exists(), action)

    def test_invalid_transitions_flash_error_and_change_nothing(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(self.url('publish'))  # DRAFT cannot be published
        self.assertEqual(flashes(res), [('error', 'Status rapor tidak memungkinkan tindakan ini.')])
        res = self.client.post(self.url('revise'))  # DRAFT cannot be revised
        self.assertEqual(flashes(res)[0][0], 'error')
        self.set_status(ReportCardStatus.PUBLISHED)
        res = self.client.post(self.url('approve'))
        self.assertEqual(flashes(res)[0][0], 'error')
        self.assertEqual(self.reload().status, ReportCardStatus.PUBLISHED)
        self.assertEqual(ReportCard.all_tenants.filter(student=self.fx['student']).count(), 1)

    def test_permission_gates_per_action(self):
        # teacher: grades.write but not school_config.write -> cannot approve/publish; can revise
        home = reverse('web-console-home')
        for action in ('approve', 'publish'):
            res = self.client.post(self.url(action))
            self.assertEqual((res.status_code, res['Location']), (302, home), action)
        counsellor = self.make_staff_user('counsellor', phone='+628119990300')
        self.client.force_authenticate(user=counsellor)
        for action in ('approve', 'publish', 'revise'):
            self.assertEqual(self.client.post(self.url(action)).status_code, 302, action)
        self.assertEqual(self.client.post(f'{CARDS}generate/', {}).status_code, 302)
        no_staff = self.make_staff_user('school_admin', phone='+628119990600', with_staff=False)
        self.client.force_authenticate(user=no_staff)
        self.assertEqual(self.client.post(self.url('approve')).status_code, 404)
        self.assertEqual(self.reload().status, ReportCardStatus.DRAFT)

    def test_other_school_or_cross_tenant_card_404(self):
        self.client.force_authenticate(user=self.other_school_user('school_admin'))
        self.assertEqual(self.client.post(self.url('approve')).status_code, 404)
        other = build_academic_fixture("Yayasan Lain Rapor Aksi")
        set_current_foundation_id(other['foundation'].id)
        enroll_and_grade(other)
        generate_report_cards(other['class_group'], other['term'])
        foreign = ReportCard.all_tenants.get(foundation_id=other['foundation'].id)
        set_current_foundation_id(self.foundation.id)
        self.client.force_authenticate(user=self.admin)
        self.assertEqual(self.client.post(f'{CARDS}{foreign.id}/approve/').status_code, 404)
        self.assertEqual(self.client.post(f'{CARDS}999999/approve/').status_code, 404)
        self.assertEqual(ReportCard.all_tenants.get(id=foreign.id).status, ReportCardStatus.DRAFT)

    # page affordances
    def test_detail_buttons_follow_status_and_permission(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.get(f'{CARDS}{self.card.id}/')
        self.assertContains(res, self.url('approve'))
        self.assertNotContains(res, self.url('publish'))
        self.set_status(ReportCardStatus.APPROVED)
        res = self.client.get(f'{CARDS}{self.card.id}/')
        self.assertContains(res, self.url('publish'))
        self.assertNotContains(res, self.url('approve'))
        self.set_status(ReportCardStatus.PUBLISHED)
        res = self.client.get(f'{CARDS}{self.card.id}/')
        self.assertContains(res, self.url('revise'))
        # a teacher (no school_config.write) never sees approve/publish
        self.client.force_authenticate(user=self.teacher.user)
        self.set_status(ReportCardStatus.DRAFT)
        res = self.client.get(f'{CARDS}{self.card.id}/')
        self.assertNotContains(res, self.url('approve'))

    def test_list_shows_generate_form_only_to_grade_writers(self):
        self.assertContains(self.client.get(CARDS), '/web/academic/report-cards/generate/')
        counsellor = self.make_staff_user('counsellor', phone='+628119990300')
        self.client.force_authenticate(user=counsellor)
        self.assertNotContains(self.client.get(CARDS), '/report-cards/generate/')
