from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    BehaviourCaseViewSet,
    BehaviourPolicyView,
    BehaviourReasonViewSet,
    BehaviourRecordViewSet,
    ClinicPolicyView,
    ClinicVisitViewSet,
    CounsellingSessionViewSet,
    LibraryItemViewSet,
    LoanViewSet,
    MedicationStockAlertView,
    MedicationStockViewSet,
    OverdueLoansView,
    StudentBehaviourSummaryView,
    StudentHealthProfileView,
    StudentMedicalAlertView,
)

router = DefaultRouter()
router.register(r'behaviour-reasons', BehaviourReasonViewSet, basename='behaviour-reasons')
router.register(r'behaviour-records', BehaviourRecordViewSet, basename='behaviour-records')
router.register(r'behaviour-cases', BehaviourCaseViewSet, basename='behaviour-cases')
router.register(r'counselling/sessions', CounsellingSessionViewSet, basename='counselling-sessions')
router.register(r'library/items', LibraryItemViewSet, basename='library-items')
router.register(r'library/loans', LoanViewSet, basename='library-loans')
router.register(r'clinic-visits', ClinicVisitViewSet, basename='clinic-visits')
router.register(r'medication-stock', MedicationStockViewSet, basename='medication-stock')

urlpatterns = [
    path('schools/<int:school_id>/behaviour-policy/', BehaviourPolicyView.as_view(), name='behaviour-policy'),
    path('schools/<int:school_id>/clinic-policy/', ClinicPolicyView.as_view(), name='clinic-policy'),
    path('schools/<int:school_id>/medication-stock/alerts/', MedicationStockAlertView.as_view(), name='medication-stock-alerts'),
    path('students/<int:student_id>/behaviour/', StudentBehaviourSummaryView.as_view(), name='student-behaviour-summary'),
    path('students/<int:student_id>/health-profile/', StudentHealthProfileView.as_view(), name='student-health-profile'),
    path('students/<int:student_id>/medical-alert/', StudentMedicalAlertView.as_view(), name='student-medical-alert'),
    path('library/overdue', OverdueLoansView.as_view(), name='library-overdue'),
    path('', include(router.urls)),
]
