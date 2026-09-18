"""Web (session-auth) console pages for apps.finance — mounted under /web/finance/."""
from django.urls import path

from apps.finance.web_views import (
    DiscountDecisionView,
    DiscrepancyResolveView,
    WriteOffDecisionView,
    ReceivablesConsoleView,
    ReconciliationConsoleView,
    BillingConsoleView,
)

urlpatterns = [
    path('billing/', BillingConsoleView.as_view(), name='finance-console-billing'),
    path('reconciliation/', ReconciliationConsoleView.as_view(), name='finance-console-reconciliation'),
    path('receivables/', ReceivablesConsoleView.as_view(), name='finance-console-receivables'),
    path('reconciliation/discrepancies/<int:pk>/resolve/', DiscrepancyResolveView.as_view(),
         name='finance-console-discrepancy-resolve'),
    path('receivables/discounts/<int:pk>/approve/', DiscountDecisionView.as_view(decision='approve'),
         name='finance-console-discount-approve'),
    path('receivables/discounts/<int:pk>/reject/', DiscountDecisionView.as_view(decision='reject'),
         name='finance-console-discount-reject'),
    path('receivables/write-offs/<int:pk>/approve/', WriteOffDecisionView.as_view(decision='approve'),
         name='finance-console-writeoff-approve'),
    path('receivables/write-offs/<int:pk>/reject/', WriteOffDecisionView.as_view(decision='reject'),
         name='finance-console-writeoff-reject'),
]
