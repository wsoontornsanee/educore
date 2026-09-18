"""Web (session-auth HTMX) views for the Mode ujian console.

The exam list is a read-only overview; the per-exam page renders the existing
proctor console component (spec/04 §6 ACD-024, spec/17 §6.3), whose live
polling talks to the JSON endpoint `/api/v1/academic/exams/<id>/proctor/`.
"""
from django.http import Http404
from django.db.models import Count
from django.shortcuts import render
from django.utils import timezone
from rest_framework.views import APIView

from apps.identity.console_access import StaffConsoleMixin

from .models import Exam, ExamAttemptStatus, ExamAttempt
from .services import build_proctor_snapshot

EXAM_LIST_LIMIT = 100


class ExamConsoleAccessMixin(StaffConsoleMixin):
    def get_required_permission(self):
        return 'grades.read'


def _exam_state(exam, now):
    """Draft / upcoming / live / ended — derived, never stored."""
    if not exam.published:
        return 'DRAFT'
    if now < exam.window_start:
        return 'UPCOMING'
    if now <= exam.window_end:
        return 'LIVE'
    return 'ENDED'


class ExamModeConsolePageView(ExamConsoleAccessMixin, APIView):
    """GET /web/academic/exams/ — the school's exams with their live attempt counts."""

    def get(self, request):
        foundation_id, schools, school = self.console_context(request)
        rows = []
        if school is not None:
            now = timezone.now()
            exams = list(Exam.objects.filter(
                foundation_id=foundation_id, class_subject__class_group__school=school, deleted_at__isnull=True,
            ).select_related('class_subject__class_group', 'class_subject__subject').order_by('-window_start')[:EXAM_LIST_LIMIT])

            counts = {}
            for row in ExamAttempt.objects.filter(
                foundation_id=foundation_id, exam__in=exams, deleted_at__isnull=True,
            ).values('exam_id', 'status').annotate(n=Count('id')):
                counts.setdefault(row['exam_id'], {})[row['status']] = row['n']

            for exam in exams:
                by_status = counts.get(exam.id, {})
                rows.append({
                    'exam': exam,
                    'state': _exam_state(exam, now),
                    'in_progress': by_status.get(ExamAttemptStatus.IN_PROGRESS, 0),
                    'submitted': by_status.get(ExamAttemptStatus.SUBMITTED, 0) + by_status.get(ExamAttemptStatus.AUTO_SUBMITTED, 0),
                })
        return render(request, 'pages/exam_mode_console_page.html', {
            'schools': schools,
            'school': school,
            'rows': rows,
        })


class ExamProctorConsolePageView(ExamConsoleAccessMixin, APIView):
    """GET /web/academic/exams/<exam_id>/ — proctor console for one exam."""

    def get(self, request, exam_id):
        foundation_id, schools, _school = self.console_context(request)
        exam = Exam.objects.filter(
            id=exam_id, foundation_id=foundation_id, deleted_at__isnull=True,
            class_subject__class_group__school__in=schools,
        ).select_related('class_subject__class_group', 'class_subject__subject').first()
        if exam is None:
            raise Http404
        return render(request, 'pages/exam_proctor_console_page.html', {
            'exam': exam,
            'proctor_data': build_proctor_snapshot(exam),
        })
