"""Web (session-auth HTMX) routes for apps.wallet — mounted under /web/wallet/."""
from django.urls import path

from .web_finance_views import (
    ReconciliationCashView,
    ReconciliationInvoiceView,
    ReconciliationQueuePageView,
    ReconciliationResendView,
    ReconciliationWriteOffView,
    RefundMarkDonatedView,
    RefundMarkPaidView,
    RefundQueuePageView,
)
from .web_views import CanteenConsolePageView

urlpatterns = [
    path('canteen/', CanteenConsolePageView.as_view(), name='canteen-console-page'),
    path('canteen/refunds/', RefundQueuePageView.as_view(), name='wallet-refund-queue'),
    path('canteen/refunds/<int:pk>/paid/', RefundMarkPaidView.as_view(), name='wallet-refund-paid'),
    path('canteen/refunds/<int:pk>/donated/', RefundMarkDonatedView.as_view(), name='wallet-refund-donated'),
    path('canteen/reconciliations/', ReconciliationQueuePageView.as_view(), name='wallet-reconciliation-queue'),
    path('canteen/reconciliations/<int:pk>/cash/', ReconciliationCashView.as_view(), name='wallet-reconciliation-cash'),
    path('canteen/reconciliations/<int:pk>/invoice/', ReconciliationInvoiceView.as_view(), name='wallet-reconciliation-invoice'),
    path('canteen/reconciliations/<int:pk>/write-off/', ReconciliationWriteOffView.as_view(), name='wallet-reconciliation-write-off'),
    path('canteen/reconciliations/<int:pk>/resend/', ReconciliationResendView.as_view(), name='wallet-reconciliation-resend'),
]
