from django.urls import path
from apps.core.routers import EduCoreRouter

from apps.attendance.pickup_views import (
    PickupAuthorizationRevokeView,
    PickupAuthorizationView,
    PickupOverrideView,
    PickupReleaseView,
    PickupStaffRevokeView,
    PickupVerifyView,
)

from apps.attendance.transport_views import (
    BusEventsView,
    BusLiveView,
    BusRouteDetailView,
    BusRouteListView,
    BusRouteStopsView,
    BusRunEndView,
    BusRunPositionView,
    BusRunStartView,
    BusStopDetailView,
    BusStopStudentDetailView,
    BusStopStudentsView,
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
    path('pickup/authorizations/<int:authorization_id>/revoke/', PickupStaffRevokeView.as_view(), name='pickup-staff-revoke'),
    # School transport (spec/05 §6, §8)
    path('bus/routes/', BusRouteListView.as_view(), name='bus-routes'),
    path('bus/routes/<int:route_id>/', BusRouteDetailView.as_view(), name='bus-route-detail'),
    path('bus/routes/<int:route_id>/stops/', BusRouteStopsView.as_view(), name='bus-route-stops'),
    path('bus/routes/<int:route_id>/runs/', BusRunStartView.as_view(), name='bus-run-start'),
    path('bus/routes/<int:route_id>/live/', BusLiveView.as_view(), name='bus-route-live'),
    path('bus/stops/<int:stop_id>/', BusStopDetailView.as_view(), name='bus-stop-detail'),
    path('bus/stops/<int:stop_id>/students/', BusStopStudentsView.as_view(), name='bus-stop-students'),
    path('bus/stops/<int:stop_id>/students/<int:student_id>/', BusStopStudentDetailView.as_view(), name='bus-stop-student'),
    path('bus/runs/<int:run_id>/position/', BusRunPositionView.as_view(), name='bus-run-position'),
    path('bus/runs/<int:run_id>/end/', BusRunEndView.as_view(), name='bus-run-end'),
    path('bus/events/', BusEventsView.as_view(), name='bus-events'),
] + router.urls

