from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.wallet.views import (
    MerchantViewSet,
    WalletAutoTopupConfigView,
    POSSessionView,
    POSSyncView,
    POSTerminalViewSet,
    POSTransactionViewSet,
    ProductViewSet,
    SettlementStatementDownloadView,
    SpendRuleView,
    WalletDetailView,
    WalletReconciliationInvoiceNowView,
    WalletReconciliationQueueView,
    WalletReconciliationResendNoticeView,
    WalletReconciliationSettleCashView,
    WalletReconciliationWriteOffView,
    WalletRefundMarkDonatedView,
    WalletRefundMarkPaidView,
    WalletRefundQueueView,
    WalletTopupIntentDetailView,
    WalletTopupIntentView,
    WalletTopupView,
    WalletTopupWebhookView,
    WalletTransactionsView,
    StudentNutritionSummaryView,
)

router = DefaultRouter()
router.register(r'merchants', MerchantViewSet, basename='merchants')
router.register(r'products', ProductViewSet, basename='products')
router.register(r'pos/terminals', POSTerminalViewSet, basename='pos-terminals')
router.register(r'pos/transactions', POSTransactionViewSet, basename='pos-transactions')

urlpatterns = [
    path('wallets/<int:student_id>/', WalletDetailView.as_view(), name='wallet-detail'),
    path('wallets/<int:student_id>/transactions/', WalletTransactionsView.as_view(), name='wallet-transactions'),
    path('wallets/<int:student_id>/topup/', WalletTopupView.as_view(), name='wallet-topup'),
    path('wallets/<int:student_id>/topup-intents/', WalletTopupIntentView.as_view(), name='wallet-topup-intent'),
    path('wallets/<int:student_id>/topup-intents/<int:intent_id>/', WalletTopupIntentDetailView.as_view(), name='wallet-topup-intent-detail'),
    path('wallets/<int:student_id>/auto-topup-config/', WalletAutoTopupConfigView.as_view(), name='wallet-auto-topup-config'),
    path('webhooks/wallet-topup/<str:provider>/', WalletTopupWebhookView.as_view(), name='wallet-topup-webhook'),
    path('wallets/<int:student_id>/rules/', SpendRuleView.as_view(), name='wallet-rules'),
    path('students/<int:student_id>/nutrition-summary/', StudentNutritionSummaryView.as_view(), name='student-nutrition-summary'),
    path('wallets/<int:student_id>/nutrition-summary/', StudentNutritionSummaryView.as_view(), name='wallet-nutrition-summary'),
    path('settlements/<int:settlement_id>/download/', SettlementStatementDownloadView.as_view(), name='settlement-download'),
    path('pos/sessions/', POSSessionView.as_view(), name='pos-sessions'),
    path('pos/sync/', POSSyncView.as_view(), name='pos-sync'),
    path('wallet-reconciliations/', WalletReconciliationQueueView.as_view(), name='wallet-reconciliation-queue'),
    path('wallet-reconciliations/<int:case_id>/settle-cash/', WalletReconciliationSettleCashView.as_view(), name='wallet-reconciliation-settle-cash'),
    path('wallet-reconciliations/<int:case_id>/invoice-now/', WalletReconciliationInvoiceNowView.as_view(), name='wallet-reconciliation-invoice-now'),
    path('wallet-reconciliations/<int:case_id>/write-off/', WalletReconciliationWriteOffView.as_view(), name='wallet-reconciliation-write-off'),
    path('wallet-reconciliations/<int:case_id>/resend-notice/', WalletReconciliationResendNoticeView.as_view(), name='wallet-reconciliation-resend-notice'),
    path('wallet-refunds/', WalletRefundQueueView.as_view(), name='wallet-refund-queue'),
    path('wallet-refunds/<int:refund_id>/mark-paid/', WalletRefundMarkPaidView.as_view(), name='wallet-refund-mark-paid'),
    path('wallet-refunds/<int:refund_id>/mark-donated/', WalletRefundMarkDonatedView.as_view(), name='wallet-refund-mark-donated'),
    path('', include(router.urls)),
]
