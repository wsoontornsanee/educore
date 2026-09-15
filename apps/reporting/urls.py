from django.urls import path

from apps.reporting.views import (
    AcademicPerformanceReportView,
    DailyAttendanceReportView,
    WalletActivityReportView,
)

urlpatterns = [
    path('wallet-activity/', WalletActivityReportView.as_view(), name='rpt-wallet-activity'),
    path('daily-attendance/', DailyAttendanceReportView.as_view(), name='rpt-daily-attendance'),
    path('academic-performance/', AcademicPerformanceReportView.as_view(), name='rpt-academic-performance'),
]
