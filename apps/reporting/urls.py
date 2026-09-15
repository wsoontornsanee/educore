from django.urls import path

from apps.reporting.views import (
    AcademicPerformanceReportView,
    ActiveStudentsReportView,
    DailyAttendanceReportView,
    DailyFinanceReportView,
    WalletActivityReportView,
)

urlpatterns = [
    path('wallet-activity/', WalletActivityReportView.as_view(), name='rpt-wallet-activity'),
    path('daily-attendance/', DailyAttendanceReportView.as_view(), name='rpt-daily-attendance'),
    path('academic-performance/', AcademicPerformanceReportView.as_view(), name='rpt-academic-performance'),
    path('active-students/', ActiveStudentsReportView.as_view(), name='rpt-active-students'),
    path('daily-finance/', DailyFinanceReportView.as_view(), name='rpt-daily-finance'),
]
