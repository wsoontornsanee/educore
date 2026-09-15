from django.urls import path

from apps.reporting.views import WalletActivityReportView

urlpatterns = [
    path('wallet-activity/', WalletActivityReportView.as_view(), name='rpt-wallet-activity'),
]
