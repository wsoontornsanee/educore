from django.urls import path
from apps.core.routers import EduCoreRouter

from apps.attendance.pickup_views import (
    PickupAuthorizationRevokeView,
    PickupAuthorizationView,
    PickupOverrideView,
    PickupReleaseView,
    PickupVerifyView,
)

from apps.attendance.views import (
    AbsenceRequestStaffViewSet,
    AttendanceDayViewSet,
    AttendanceRuleViewSet,
    CredentialViewSet,
    GateEventViewSet,
    LiveGateConsoleView,
    ManualCheckInView,
    PeriodAttendanceSyncView,
    PeriodAttendanceView,
    StudentAbsenceRequestView,
    TeacherAgendaView,
)

router = EduCoreRouter()
router.register('credentials', CredentialViewSet, basename='credential')
router.register('gate/events', GateEventViewSet, basename='gate-event')
router.register('attendance/daily', AttendanceDayViewSet, basename='attendance-daily')
router.register('attendance/rules', AttendanceRuleViewSet, basename='attendance-rule')
router.register('attendance/absence-requests', AbsenceRequestStaffViewSet, basename='absence-request-staff')

urlpatterns = [
    # Direct spec routes (spec/05 §8)
    path('gate/live', LiveGateConsoleView.as_view(), name='gate-live'),
    path('gate/live/', LiveGateConsoleView.as_view(), name='gate-live-slash'),
    path('attendance/manual', ManualCheckInView.as_view(), name='attendance-manual'),
    path('attendance/manual/', ManualCheckInView.as_view(), name='attendance-manual-slash'),
    path('teacher/agenda', TeacherAgendaView.as_view(), name='teacher-agenda'),
    path('teacher/agenda/', TeacherAgendaView.as_view(), name='teacher-agenda-slash'),
    path('timetable/slots/<int:slot_id>/period-attendance/', PeriodAttendanceView.as_view(), name='period-attendance'),
    path('period-attendance/sync/', PeriodAttendanceSyncView.as_view(), name='period-attendance-sync'),
    path('attendance/students/<int:student_id>/absence-requests/', StudentAbsenceRequestView.as_view(), name='student-absence-requests'),
    # Pickup safety (spec/05 §5, §8)
    path('pickup-authorizations/', PickupAuthorizationView.as_view(), name='pickup-authorizations'),
    path('pickup-authorizations/<int:authorization_id>/revoke/', PickupAuthorizationRevokeView.as_view(), name='pickup-authorization-revoke'),
    path('pickup/verify/', PickupVerifyView.as_view(), name='pickup-verify'),
    path('pickup/release/', PickupReleaseView.as_view(), name='pickup-release'),
    path('pickup/override/', PickupOverrideView.as_view(), name='pickup-override'),
] + router.urls

