from django.urls import include, path
from apps.core.routers import EduCoreRouter

from apps.finance.views import (
    ArAgingView,
    BankSftpConfigListView,
    DiscountViewSet,
    FeePlanViewSet,
    FeeTypeViewSet,
    FiscalPeriodViewSet,
    InvoiceViewSet,
    InvoiceWriteOffRequestViewSet,
    LedgerJournalViewSet,
    PaymentIntentViewSet,
    PaymentViewSet,
    PaymentWebhookView,
    ReconciliationBankStatementUploadView,
    ReconciliationBatchDetailView,
    ReconciliationBatchListView,
    ReconciliationDiscrepancyResolveView,
    RefundViewSet,
    SchoolArrearsPolicyView,
    SchoolConvenienceFeePolicyView,
    SchoolQrisConfigView,
    SiblingDiscountPolicyViewSet,
    StudentFeeAssignmentViewSet,
    StudentStatementView,
    AccurateAccountingExportView,
    JurnalAccountingExportView,
    ExternalAccountMappingViewSet,
)

router = EduCoreRouter()
router.register(r'fee-types', FeeTypeViewSet, basename='fee-types')
router.register(r'fee-plans', FeePlanViewSet, basename='fee-plans')
router.register(r'assignments', StudentFeeAssignmentViewSet, basename='fee-assignments')
router.register(r'discounts', DiscountViewSet, basename='discounts')
router.register(r'sibling-policies', SiblingDiscountPolicyViewSet, basename='sibling-policies')
router.register(r'invoices', InvoiceViewSet, basename='invoices')
router.register(r'payment-intents', PaymentIntentViewSet, basename='payment-intents')
router.register(r'payments', PaymentViewSet, basename='payments')
router.register(r'refunds', RefundViewSet, basename='refunds')
router.register(r'write-offs', InvoiceWriteOffRequestViewSet, basename='write-offs')
router.register(r'ledger/journals', LedgerJournalViewSet, basename='ledger-journals')
router.register(r'periods', FiscalPeriodViewSet, basename='fiscal-periods')
router.register(r'accounting/mappings', ExternalAccountMappingViewSet, basename='accounting-mappings')


urlpatterns = [
    path('ar-aging/', ArAgingView.as_view(), name='ar-aging'),
    path('webhooks/payments/<str:provider>/', PaymentWebhookView.as_view(), name='payment-webhook'),
    path('students/<int:pk>/statement/', StudentStatementView.as_view(), name='student-statement'),
    path('schools/<int:school_id>/qris-config/', SchoolQrisConfigView.as_view(), name='school-qris-config'),
    path('schools/<int:school_id>/arrears-policy/', SchoolArrearsPolicyView.as_view(), name='school-arrears-policy'),
    path('schools/<int:school_id>/convenience-fee-policy/', SchoolConvenienceFeePolicyView.as_view(), name='school-convenience-fee-policy'),
    path('schools/<int:school_id>/bank-sftp-configs/', BankSftpConfigListView.as_view(), name='school-bank-sftp-configs'),
    # FIN-024 Gateway Reconciliation
    path('reconciliation/batches/', ReconciliationBatchListView.as_view(), name='reconciliation-batch-list'),
    path('reconciliation/batches/<int:pk>/', ReconciliationBatchDetailView.as_view(), name='reconciliation-batch-detail'),
    path('reconciliation/discrepancies/<int:pk>/resolve/', ReconciliationDiscrepancyResolveView.as_view(), name='reconciliation-discrepancy-resolve'),
    path('reconciliation/bank-statements/upload/', ReconciliationBankStatementUploadView.as_view(), name='reconciliation-bank-statement-upload'),
    # Accounting Export (spec/14 §6)
    path('accounting/export/accurate/', AccurateAccountingExportView.as_view(), name='accounting-export-accurate'),
    path('accounting/export/jurnal/', JurnalAccountingExportView.as_view(), name='accounting-export-jurnal'),
    path('', include(router.urls)),
]


