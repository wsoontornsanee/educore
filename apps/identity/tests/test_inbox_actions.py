"""Kotak tugas in-place decisions (POST /web/home/inbox/<kind>/<pk>/<action>/)."""
import datetime

from django.urls import reverse

from apps.academic.models import ReportCard, ReportCardStatus, SubstitutionStatus, TimetableSubstitution
from apps.academic.tests.base import build_academic_fixture
from apps.attendance.models import AbsenceRequest, AbsenceRequestStatus
from apps.core.models import AuditEvent
from apps.identity.tests.test_inbox import InboxTestBase, SubstitutionInboxTests
from educore.middleware.tenancy import set_current_foundation_id


def action_url(kind, pk, action):
    return reverse('console-inbox-action', kwargs={'kind': kind, 'pk': pk, 'action': action})


class InboxActionTestBase(InboxTestBase):
    def make_absence(self, **overrides):
        fields = dict(
            foundation_id=self.foundation.id, school=self.school, student=self.fx['student'],
            requested_by=self.teacher, date_from=datetime.date(2026, 8, 3), date_to=datetime.date(2026, 8, 4),
            type='SAKIT', reason='Demam', status=AbsenceRequestStatus.PENDING,
        )
        fields.update(overrides)
        return AbsenceRequest.objects.create(**fields)


class AbsenceRequestActionTests(InboxActionTestBase):
    def test_approve_goes_through_the_service_and_refreshes_the_fragment(self):
        req = self.make_absence()
        self.client.force_login(self.school_admin)
        res = self.client.post(action_url('absence_request', req.pk, 'approve'), {'note': 'Surat dokter ada'})
        self.assertEqual(res.status_code, 200)
        req.refresh_from_db()
        self.assertEqual(req.status, AbsenceRequestStatus.APPROVED)
        self.assertEqual(req.decision_note, 'Surat dokter ada')
        self.assertEqual(req.decided_by, self.school_admin)
        self.assertTrue(AuditEvent.objects.filter(action='attendance.absence_request.approved').exists())
        self.assertContains(res, 'Keputusan tersimpan.')
        self.assertContains(res, 'Tidak ada tugas')  # the only item is gone from the refreshed fragment
        self.assertNotContains(res, '<html')  # fragment, not a full page

    def test_reject_records_status_and_note(self):
        req = self.make_absence()
        self.client.force_login(self.school_admin)
        self.client.post(action_url('absence_request', req.pk, 'reject'), {'note': 'Tidak ada bukti'})
        req.refresh_from_db()
        self.assertEqual(req.status, AbsenceRequestStatus.REJECTED)
        self.assertEqual(req.decision_note, 'Tidak ada bukti')

    def test_already_decided_item_is_reported_not_applied_twice(self):
        req = self.make_absence(status=AbsenceRequestStatus.APPROVED)
        self.client.force_login(self.school_admin)
        res = self.client.post(action_url('absence_request', req.pk, 'reject'))
        self.assertContains(res, 'sudah tidak menunggu tindakan Anda')
        req.refresh_from_db()
        self.assertEqual(req.status, AbsenceRequestStatus.APPROVED)

    def test_other_schools_admin_cannot_decide_by_guessing_the_id(self):
        req = self.make_absence()
        self.client.force_login(self.admin_b)
        res = self.client.post(action_url('absence_request', req.pk, 'approve'))
        self.assertContains(res, 'sudah tidak menunggu tindakan Anda')
        req.refresh_from_db()
        self.assertEqual(req.status, AbsenceRequestStatus.PENDING)

    def test_another_foundations_request_is_not_actionable(self):
        other = build_academic_fixture(foundation_name='Yayasan Lain')
        foreign = AbsenceRequest.objects.create(
            foundation_id=other['foundation'].id, school=other['school'], student=other['student'],
            requested_by=other['teacher_user'], date_from=datetime.date(2026, 8, 3),
            date_to=datetime.date(2026, 8, 3), type='IZIN', reason='Keluarga', status=AbsenceRequestStatus.PENDING,
        )
        set_current_foundation_id(self.foundation.id)
        self.client.force_login(self.admin)
        res = self.client.post(action_url('absence_request', foreign.pk, 'approve'))
        self.assertContains(res, 'sudah tidak menunggu tindakan Anda')
        foreign.refresh_from_db()
        self.assertEqual(foreign.status, AbsenceRequestStatus.PENDING)


class ReportCardActionTests(InboxActionTestBase):
    def setUp(self):
        super().setUp()
        self.card = ReportCard.objects.create(
            foundation_id=self.foundation.id, student=self.fx['student'], term=self.fx['term'],
            class_group=self.fx['class_group'], status=ReportCardStatus.PENDING_REVIEW, is_current=True,
        )

    def test_school_admin_approves(self):
        self.client.force_login(self.school_admin)
        self.client.post(action_url('report_card', self.card.pk, 'approve'))
        self.card.refresh_from_db()
        self.assertEqual(self.card.status, ReportCardStatus.APPROVED)
        self.assertEqual(self.card.approved_by, self.school_admin)

    def test_teacher_without_approve_permission_cannot(self):
        self.client.force_login(self.teacher)
        res = self.client.post(action_url('report_card', self.card.pk, 'approve'))
        self.assertContains(res, 'sudah tidak menunggu tindakan Anda')
        self.card.refresh_from_db()
        self.assertEqual(self.card.status, ReportCardStatus.PENDING_REVIEW)

    def test_report_card_has_no_reject_action(self):
        self.client.force_login(self.school_admin)
        self.assertEqual(self.client.post(action_url('report_card', self.card.pk, 'reject')).status_code, 404)


class SubstitutionActionTests(SubstitutionInboxTests):
    def test_substitute_accepts(self):
        self.client.force_login(self.sub_user)
        self.client.post(action_url('substitution', self.substitution.pk, 'accept'))
        self.substitution.refresh_from_db()
        self.assertEqual(self.substitution.status, SubstitutionStatus.ACCEPTED)

    def test_decline_requires_a_reason(self):
        self.client.force_login(self.sub_user)
        res = self.client.post(action_url('substitution', self.substitution.pk, 'decline'), {'note': '  '})
        self.assertContains(res, 'Alasan penolakan wajib diisi.')
        self.substitution.refresh_from_db()
        self.assertEqual(self.substitution.status, SubstitutionStatus.PENDING)

    def test_decline_with_reason(self):
        self.client.force_login(self.sub_user)
        self.client.post(action_url('substitution', self.substitution.pk, 'decline'), {'note': 'Bentrok jadwal'})
        self.substitution.refresh_from_db()
        self.assertEqual(self.substitution.status, SubstitutionStatus.DECLINED)
        self.assertEqual(self.substitution.decline_reason, 'Bentrok jadwal')

    def test_only_the_assigned_substitute_can_answer(self):
        self.client.force_login(self.admin)  # even a foundation admin: personal task, not theirs
        res = self.client.post(action_url('substitution', self.substitution.pk, 'accept'))
        self.assertContains(res, 'sudah tidak menunggu tindakan Anda')
        self.substitution.refresh_from_db()
        self.assertEqual(self.substitution.status, SubstitutionStatus.PENDING)

    def test_answered_substitution_cannot_be_flipped(self):
        TimetableSubstitution.objects.filter(pk=self.substitution.pk).update(status=SubstitutionStatus.DECLINED)
        self.client.force_login(self.sub_user)
        res = self.client.post(action_url('substitution', self.substitution.pk, 'accept'))
        self.assertContains(res, 'sudah tidak menunggu tindakan Anda')
        self.substitution.refresh_from_db()
        self.assertEqual(self.substitution.status, SubstitutionStatus.DECLINED)


class InboxActionRequestTests(InboxActionTestBase):
    def test_anonymous_is_redirected_to_login_and_nothing_changes(self):
        req = self.make_absence()
        res = self.client.post(action_url('absence_request', req.pk, 'approve'))
        self.assertEqual(res.status_code, 302)
        req.refresh_from_db()
        self.assertEqual(req.status, AbsenceRequestStatus.PENDING)

    def test_get_is_not_allowed(self):
        req = self.make_absence()
        self.client.force_login(self.school_admin)
        self.assertEqual(self.client.get(action_url('absence_request', req.pk, 'approve')).status_code, 405)

    def test_unknown_kind_and_action_are_404(self):
        self.client.force_login(self.school_admin)
        self.assertEqual(self.client.post(action_url('nope', 1, 'approve')).status_code, 404)
        self.assertEqual(self.client.post(action_url('absence_request', 1, 'nope')).status_code, 404)

    def test_finance_sections_are_not_actionable_here(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(action_url('finance_approval', 1, 'approve')).status_code, 404)
        self.assertEqual(self.client.post(action_url('write_off', 1, 'approve')).status_code, 404)

    def test_page_renders_action_buttons_with_htmx_wiring(self):
        req = self.make_absence()
        self.client.force_login(self.school_admin)
        res = self.client.get(reverse('console-inbox'))
        self.assertContains(res, f'hx-post="{action_url("absence_request", req.pk, "approve")}"')
        self.assertContains(res, f'hx-post="{action_url("absence_request", req.pk, "reject")}"')
        self.assertContains(res, 'hx-target="#inbox-sections"')
        self.assertContains(res, 'Catatan (opsional)')


class InboxActionEnglishTests(InboxActionTestBase):
    def test_buttons_and_result_message_translate(self):
        req = self.make_absence()
        self.client.force_login(self.school_admin)
        self.client.cookies['django_language'] = 'en'
        self.assertContains(self.client.get(reverse('console-inbox')), 'Note (optional)')
        res = self.client.post(action_url('absence_request', req.pk, 'approve'))
        self.assertContains(res, 'Decision saved.')
