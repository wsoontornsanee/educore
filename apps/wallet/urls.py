from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.wallet.views import (
    MerchantViewSet,
    POSSessionView,
    POSSyncView,
    POSTerminalViewSet,
    POSTransactionViewSet,
    ProductViewSet,
    SpendRuleView,
    WalletDetailView,
    WalletReconciliationInvoiceNowView,
    WalletReconciliationQueueView,
    WalletReconciliationResendNoticeView,
    WalletReconciliationSettleCashView,
    WalletReconciliationWriteOffView,
    WalletTopupView,
    WalletTransactionsView,
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
    path('wallets/<int:student_id>/rules/', SpendRuleView.as_view(), name='wallet-rules'),
    path('pos/sessions/', POSSessionView.as_view(), name='pos-sessions'),
    path('pos/sync/', POSSyncView.as_view(), name='pos-sync'),
    path('wallet-reconciliations/', WalletReconciliationQueueView.as_view(), name='wallet-reconciliation-queue'),
    path('wallet-reconciliations/<int:case_id>/settle-cash/', WalletReconciliationSettleCashView.as_view(), name='wallet-reconciliation-settle-cash'),
    path('wallet-reconciliations/<int:case_id>/invoice-now/', WalletReconciliationInvoiceNowView.as_view(), name='wallet-reconciliation-invoice-now'),
    path('wallet-reconciliations/<int:case_id>/write-off/', WalletReconciliationWriteOffView.as_view(), name='wallet-reconciliation-write-off'),
    path('wallet-reconciliations/<int:case_id>/resend-notice/', WalletReconciliationResendNoticeView.as_view(), name='wallet-reconciliation-resend-notice'),
    path('', include(router.urls)),
]
