from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.finance.views import (
    DiscountViewSet,
    FeePlanViewSet,
    FeeTypeViewSet,
    InvoiceViewSet,
    LedgerJournalViewSet,
    PaymentIntentViewSet,
    PaymentViewSet,
    PaymentWebhookView,
    SchoolArrearsPolicyView,
    SchoolQrisConfigView,
    SiblingDiscountPolicyViewSet,
    StudentFeeAssignmentViewSet,
    StudentStatementView,
)

router = DefaultRouter()
router.register(r'fee-types', FeeTypeViewSet, basename='fee-types')
router.register(r'fee-plans', FeePlanViewSet, basename='fee-plans')
router.register(r'assignments', StudentFeeAssignmentViewSet, basename='fee-assignments')
router.register(r'discounts', DiscountViewSet, basename='discounts')
router.register(r'sibling-policies', SiblingDiscountPolicyViewSet, basename='sibling-policies')
router.register(r'invoices', InvoiceViewSet, basename='invoices')
router.register(r'payment-intents', PaymentIntentViewSet, basename='payment-intents')
router.register(r'payments', PaymentViewSet, basename='payments')
router.register(r'ledger/journals', LedgerJournalViewSet, basename='ledger-journals')

urlpatterns = [
    path('webhooks/payments/<str:provider>/', PaymentWebhookView.as_view(), name='payment-webhook'),
    path('students/<int:pk>/statement/', StudentStatementView.as_view(), name='student-statement'),
    path('schools/<int:school_id>/qris-config/', SchoolQrisConfigView.as_view(), name='school-qris-config'),
    path('schools/<int:school_id>/arrears-policy/', SchoolArrearsPolicyView.as_view(), name='school-arrears-policy'),
    path('', include(router.urls)),
]

