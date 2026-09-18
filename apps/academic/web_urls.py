"""Web (session-auth HTMX) URL routes for apps.academic — mounted under /web/academic/.

These are page/fragment routes (HTML responses), distinct from the JSON API
routes in urls.py (mounted under /api/v1/). Both share the same services and
permission keys.
"""
from django.urls import path

from apps.academic.console_views import (
    ClassDetailPageView,
    ClassListPageView,
    GradingQueuePageView,
    ReportCardDetailPageView,
    ReportCardListPageView,
    ReportCardPrintPageView,
    TimetablePageView,
)
from apps.academic.web_views import ExamModeConsolePageView, ExamProctorConsolePageView, ExamPublishView
from apps.academic.views import (
    PermissionSlipConsoleCreateView,
    PermissionSlipConsolePageView,
    PermissionSlipConsoleRosterView,
    PermissionSlipConsoleTallyView,
)

urlpatterns = [
    path('report-cards/', ReportCardListPageView.as_view(), name='academic-report-card-list-page'),
    path('report-cards/<int:report_card_id>/', ReportCardDetailPageView.as_view(), name='academic-report-card-detail-page'),
    path('report-cards/<int:report_card_id>/print/', ReportCardPrintPageView.as_view(), name='academic-report-card-print-page'),
    path('grading-queue/', GradingQueuePageView.as_view(), name='academic-grading-queue-page'),
    path('timetable/', TimetablePageView.as_view(), name='academic-timetable-page'),
    path('classes/', ClassListPageView.as_view(), name='academic-class-list-page'),
    path('classes/<int:class_group_id>/', ClassDetailPageView.as_view(), name='academic-class-detail-page'),
    path('permission-slips/', PermissionSlipConsolePageView.as_view(), name='permission-slip-console-page'),
    path('permission-slips/create/', PermissionSlipConsoleCreateView.as_view(), name='permission-slip-console-create'),
    path('permission-slips/<int:slip_id>/', PermissionSlipConsoleTallyView.as_view(), name='permission-slip-console-tally'),
    path('permission-slips/<int:slip_id>/roster/', PermissionSlipConsoleRosterView.as_view(), name='permission-slip-console-roster'),
    path('exams/', ExamModeConsolePageView.as_view(), name='exam-mode-console-page'),
    path('exams/<int:exam_id>/publish/', ExamPublishView.as_view(), name='exam-publish-console'),
    path('exams/<int:exam_id>/', ExamProctorConsolePageView.as_view(), name='exam-proctor-console-page'),
]
