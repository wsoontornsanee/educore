"""Web (session-auth HTMX) routes for apps.wallet — mounted under /web/wallet/."""
from django.urls import path

from .web_views import (
    CanteenConsolePageView,
    ReconciliationCashView,
    ReconciliationInvoiceView,
    ReconciliationResendNoticeView,
    ReconciliationWriteOffView,
    RefundDonatedView,
    RefundPaidView,
)

urlpatterns = [
    path('canteen/', CanteenConsolePageView.as_view(), name='canteen-console-page'),
    path('canteen/refunds/<int:pk>/paid/', RefundPaidView.as_view(), name='canteen-refund-paid'),
    path('canteen/refunds/<int:pk>/donated/', RefundDonatedView.as_view(), name='canteen-refund-donated'),
    path('canteen/reconciliations/<int:pk>/cash/', ReconciliationCashView.as_view(), name='canteen-recon-cash'),
    path('canteen/reconciliations/<int:pk>/invoice/', ReconciliationInvoiceView.as_view(), name='canteen-recon-invoice'),
    path('canteen/reconciliations/<int:pk>/write-off/', ReconciliationWriteOffView.as_view(), name='canteen-recon-writeoff'),
    path('canteen/reconciliations/<int:pk>/resend/', ReconciliationResendNoticeView.as_view(), name='canteen-recon-resend'),
]
