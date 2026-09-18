"""Write actions for the Akademik console pages (grade/return homework,
generate/approve/publish/revise report cards). Design:
docs/superpowers/specs/2026-09-19-web-console-akademik-design.md ("Follow-on: write actions").

Every view is POST-only, redirects back with a flash message, and reuses the
same audited service the JSON API calls. Each is gated by its own permission key
(fail-closed via StaffConsoleMixin) and by school scope: the target must belong
to a school in which the user holds that key, otherwise it 404s.
"""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy
from rest_framework.views import APIView

from apps.academic.models import (
    ClassGroup,
    HomeworkSubmission,
    HomeworkSubmissionStatus,
    ReportCard,
    Term,
)
from apps.academic.services import (
    HomeworkSubmissionStateError,
    ReasonRequiredError,
    ReportCardStateError,
    approve_report_card,
    generate_report_cards,
    grade_homework_submission,
    publish_report_card,
    return_homework_submission,
    revise_report_card,
)
from apps.identity.console_access import StaffConsoleMixin, permitted_schools
from educore.middleware.tenancy import get_current_foundation_id

MAX_HOMEWORK_SCORE = Decimal('100')


def permitted_school_ids(user, foundation_id, permission_key):
    """ids of the schools in which `user` holds `permission_key`."""
    return {school.id for school in permitted_schools(user, foundation_id, permission_key)}


class _ConsoleWriteAction(StaffConsoleMixin, APIView):
    """POST-only base: resolves the Staff profile (404 without one), exposes the
    school ids the user may write in, and flashes + redirects."""

    permission_key = None

    def get_required_permission(self):
        return self.permission_key

    def write_school_ids(self, request):
        if self._resolve_staff(request) is None:
            raise Http404
        return permitted_school_ids(request.user, get_current_foundation_id(), self.permission_key)

    @staticmethod
    def done(request, level, text, url):
        getattr(messages, level)(request, text)
        return redirect(url)


class _SubmissionAction(_ConsoleWriteAction):
    permission_key = 'grades.write'

    def queue_url(self, request):
        class_subject = request.data.get('class_subject', '')
        url = reverse('academic-grading-queue-page')
        return f'{url}?class_subject={class_subject}' if str(class_subject).isdigit() else url

    def get_submission(self, request, submission_id):
        submission = HomeworkSubmission.objects.filter(
            id=submission_id, foundation_id=get_current_foundation_id(), deleted_at__isnull=True,
            homework__class_subject__class_group__school_id__in=self.write_school_ids(request),
        ).select_related('student__person', 'homework').first()
        if submission is None:
            raise Http404
        return submission

    def post(self, request, submission_id):
        submission = self.get_submission(request, submission_id)
        url = self.queue_url(request)
        if submission.status not in (HomeworkSubmissionStatus.SUBMITTED, HomeworkSubmissionStatus.LATE):
            return self.done(request, 'error', _("Pengumpulan ini sudah dinilai atau dikembalikan."), url)
        return self.act(request, submission, url)


class GradeSubmissionView(_SubmissionAction):
    """POST /web/academic/grading-queue/<id>/grade/ — score (0-100, max 2 decimals) + optional feedback."""

    def act(self, request, submission, url):
        try:
            score = Decimal(str(request.data.get('score', '')).strip())
        except InvalidOperation:
            score = None
        if (
            score is None or not score.is_finite() or not Decimal('0') <= score <= MAX_HOMEWORK_SCORE
            or score.as_tuple().exponent < -2
        ):
            return self.done(request, 'error', _("Nilai harus berupa angka 0–100 dengan maksimal dua desimal."), url)
        grade_homework_submission(
            submission, score=score, feedback=(request.data.get('feedback') or '').strip(), actor=request.user,
        )
        return self.done(request, 'success', _("Nilai tersimpan."), url)


class ReturnSubmissionView(_SubmissionAction):
    """POST /web/academic/grading-queue/<id>/return/ — send back for revision (feedback required)."""

    def act(self, request, submission, url):
        try:
            return_homework_submission(
                submission, feedback=(request.data.get('feedback') or '').strip(), actor=request.user,
            )
        except ReasonRequiredError:
            return self.done(request, 'error', _("Umpan balik wajib diisi untuk mengembalikan tugas."), url)
        except HomeworkSubmissionStateError:
            return self.done(request, 'error', _("Pengumpulan ini sudah dinilai atau dikembalikan."), url)
        return self.done(request, 'success', _("Tugas dikembalikan ke siswa."), url)


class ReportCardGenerateView(_ConsoleWriteAction):
    """POST /web/academic/report-cards/generate/ — generate DRAFT rapor for a class + term.
    Cards no longer in DRAFT are skipped by the service."""

    permission_key = 'grades.write'

    def post(self, request):
        foundation_id = get_current_foundation_id()
        url = reverse('academic-report-card-list-page')
        raw_class, raw_term = request.data.get('class_group', ''), request.data.get('term', '')
        class_group = ClassGroup.objects.filter(
            id=raw_class if str(raw_class).isdigit() else 0, foundation_id=foundation_id, deleted_at__isnull=True,
            school_id__in=self.write_school_ids(request),
        ).first()
        term = Term.objects.filter(
            id=raw_term if str(raw_term).isdigit() else 0, foundation_id=foundation_id, deleted_at__isnull=True,
        ).first()
        if class_group is None or term is None:
            return self.done(request, 'error', _("Pilih kelas dan semester yang valid."), url)
        if term.academic_year_id != class_group.academic_year_id:
            return self.done(request, 'error', _("Semester tidak sesuai dengan tahun ajaran kelas."), url)
        result = generate_report_cards(class_group, term, triggered_by=request.user)
        text = _("Rapor dibuat: %(created)s baru, %(updated)s diperbarui, %(skipped)s dilewati.") % result
        return self.done(request, 'success', text, f'{url}?term={term.id}&class_group={class_group.id}')


class _ReportCardAction(_ConsoleWriteAction):
    """POST /web/academic/report-cards/<id>/<action>/ — one state transition."""

    success_message = ''

    def get_report_card(self, request, report_card_id):
        report_card = ReportCard.objects.filter(
            id=report_card_id, foundation_id=get_current_foundation_id(), deleted_at__isnull=True,
            class_group__school_id__in=self.write_school_ids(request),
        ).first()
        if report_card is None:
            raise Http404
        return report_card

    def post(self, request, report_card_id):
        report_card = self.get_report_card(request, report_card_id)
        try:
            result = self.transition(report_card, request.user)
        except ReportCardStateError:
            return self.done(
                request, 'error', _("Status rapor tidak memungkinkan tindakan ini."),
                reverse('academic-report-card-detail-page', args=[report_card.id]),
            )
        return self.done(
            request, 'success', self.success_message,
            reverse('academic-report-card-detail-page', args=[result.id]),
        )


class ReportCardApproveView(_ReportCardAction):
    permission_key = 'school_config.write'
    success_message = _lazy("Rapor disetujui.")

    def transition(self, report_card, user):
        return approve_report_card(report_card, actor=user)


class ReportCardPublishView(_ReportCardAction):
    permission_key = 'school_config.write'
    success_message = _lazy("Rapor diterbitkan.")

    def transition(self, report_card, user):
        return publish_report_card(report_card, actor=user)


class ReportCardReviseView(_ReportCardAction):
    """Correcting a published rapor creates a new DRAFT version; the redirect goes to it."""

    permission_key = 'grades.write'
    success_message = _lazy("Versi revisi dibuat sebagai konsep.")

    def transition(self, report_card, user):
        return revise_report_card(report_card, actor=user)
