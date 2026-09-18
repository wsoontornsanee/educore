"""Web (session-auth) console pages for apps.finance — mounted under /web/finance/."""
from django.urls import path

from apps.finance.web_views import (
    ReceivablesConsoleView,
    ReconciliationConsoleView,
    BillingConsoleView,
)

urlpatterns = [
    path('billing/', BillingConsoleView.as_view(), name='finance-console-billing'),
    path('reconciliation/', ReconciliationConsoleView.as_view(), name='finance-console-reconciliation'),
    path('receivables/', ReceivablesConsoleView.as_view(), name='finance-console-receivables'),
]
