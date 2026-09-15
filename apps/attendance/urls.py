from django.urls import path
from rest_framework.routers import DefaultRouter

from apps.attendance.views import (
    AttendanceDayViewSet,
    AttendanceRuleViewSet,
    CredentialViewSet,
    GateEventViewSet,
    LiveGateConsoleView,
    ManualCheckInView,
    PeriodAttendanceView,
    TeacherAgendaView,
)

router = DefaultRouter()
router.register('credentials', CredentialViewSet, basename='credential')
router.register('gate/events', GateEventViewSet, basename='gate-event')
router.register('attendance/daily', AttendanceDayViewSet, basename='attendance-daily')
router.register('attendance/rules', AttendanceRuleViewSet, basename='attendance-rule')

urlpatterns = [
    # Direct spec routes (spec/05 §8)
    path('gate/live', LiveGateConsoleView.as_view(), name='gate-live'),
    path('gate/live/', LiveGateConsoleView.as_view(), name='gate-live-slash'),
    path('attendance/manual', ManualCheckInView.as_view(), name='attendance-manual'),
    path('attendance/manual/', ManualCheckInView.as_view(), name='attendance-manual-slash'),
    path('teacher/agenda', TeacherAgendaView.as_view(), name='teacher-agenda'),
    path('teacher/agenda/', TeacherAgendaView.as_view(), name='teacher-agenda-slash'),
    path('timetable/slots/<int:slot_id>/period-attendance/', PeriodAttendanceView.as_view(), name='period-attendance'),
] + router.urls

