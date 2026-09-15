from rest_framework.routers import DefaultRouter

from apps.attendance.views import (
    AttendanceDayViewSet,
    AttendanceRuleViewSet,
    CredentialViewSet,
    GateEventViewSet,
)

router = DefaultRouter()
router.register('credentials', CredentialViewSet, basename='credential')
router.register('gate/events', GateEventViewSet, basename='gate-event')
router.register('attendance/daily', AttendanceDayViewSet, basename='attendance-daily')
router.register('attendance/rules', AttendanceRuleViewSet, basename='attendance-rule')

urlpatterns = router.urls
