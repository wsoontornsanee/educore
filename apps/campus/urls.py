from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    BehaviourCaseViewSet,
    BehaviourPolicyView,
    BehaviourReasonViewSet,
    BehaviourRecordViewSet,
    CounsellingSessionViewSet,
    StudentBehaviourSummaryView,
)

router = DefaultRouter()
router.register(r'behaviour-reasons', BehaviourReasonViewSet, basename='behaviour-reasons')
router.register(r'behaviour-records', BehaviourRecordViewSet, basename='behaviour-records')
router.register(r'behaviour-cases', BehaviourCaseViewSet, basename='behaviour-cases')
router.register(r'counselling/sessions', CounsellingSessionViewSet, basename='counselling-sessions')

urlpatterns = [
    path('schools/<int:school_id>/behaviour-policy/', BehaviourPolicyView.as_view(), name='behaviour-policy'),
    path('students/<int:student_id>/behaviour/', StudentBehaviourSummaryView.as_view(), name='student-behaviour-summary'),
    path('', include(router.urls)),
]
