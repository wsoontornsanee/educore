from django.urls import path

from apps.reporting.views import DailyAttendanceReportView, WalletActivityReportView

urlpatterns = [
    path('wallet-activity/', WalletActivityReportView.as_view(), name='rpt-wallet-activity'),
    path('daily-attendance/', DailyAttendanceReportView.as_view(), name='rpt-daily-attendance'),
]
