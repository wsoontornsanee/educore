"""Web (session-auth HTMX) routes for apps.wallet — mounted under /web/wallet/."""
from django.urls import path

from .web_qr_views import (
    CanteenQRPageView,
    MerchantQRFlagClearView,
    MerchantQRSwitchView,
    QRDisputeResolveWebView,
    QRTerminalCancelView,
    QRTerminalPageView,
    QRTerminalResultView,
    QRTerminalSessionView,
    QRTerminalVoidView,
)
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
    path('canteen/qr/', CanteenQRPageView.as_view(), name='canteen-qr-page'),
    path('canteen/qr/merchants/<int:pk>/switch/', MerchantQRSwitchView.as_view(), name='canteen-qr-switch'),
    path('canteen/qr/merchants/<int:pk>/flag/clear/', MerchantQRFlagClearView.as_view(), name='canteen-qr-flag-clear'),
    path('canteen/qr/disputes/<int:pk>/resolve/', QRDisputeResolveWebView.as_view(), name='canteen-qr-dispute-resolve'),
    path('canteen/qr/terminal/<int:terminal_id>/', QRTerminalPageView.as_view(), name='canteen-qr-terminal'),
    path('canteen/qr/terminal/<int:terminal_id>/session/', QRTerminalSessionView.as_view(), name='canteen-qr-terminal-session'),
    path('canteen/qr/terminal/<int:terminal_id>/session/<int:session_id>/result/', QRTerminalResultView.as_view(), name='canteen-qr-terminal-result'),
    path('canteen/qr/terminal/<int:terminal_id>/session/<int:session_id>/cancel/', QRTerminalCancelView.as_view(), name='canteen-qr-terminal-cancel'),
    path('canteen/qr/terminal/<int:terminal_id>/session/<int:session_id>/void/', QRTerminalVoidView.as_view(), name='canteen-qr-terminal-void'),
]
