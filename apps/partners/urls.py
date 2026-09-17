"""URL routing for the partner API surface (spec/18 §7) and its
foundation-admin management surface.

Partner endpoints: /api/v1/partner/...   (HMAC key auth, no session)
Admin endpoints:   /api/v1/partner-admin/...  (JWT/session, foundation admin)
"""
from django.urls import path

from apps.partners import admin_views, api_views

urlpatterns = [
    # Partner-facing (HMAC)
    path('partner/foundations', api_views.PartnerFoundationListView.as_view()),
    path('partner/schools', api_views.PartnerSchoolListView.as_view()),
    path('partner/staff', api_views.PartnerStaffListView.as_view()),
    path('partner/payroll/runs', api_views.PartnerPayrollRunListView.as_view()),
    path('partner/payroll/runs/<int:run_id>/lines', api_views.PartnerPayrollRunLinesView.as_view()),
    path('partner/payroll/runs/<int:run_id>/acknowledge', api_views.PartnerPayrollAcknowledgeView.as_view()),
    path('partner/invoices', api_views.PartnerInvoiceListView.as_view()),
    path('partner/attendance/daily', api_views.PartnerAttendanceDailyView.as_view()),
    path('partner/events', api_views.PartnerEventListView.as_view()),
    path('partner/webhooks', api_views.PartnerWebhookRegisterView.as_view()),

    # Foundation-admin management
    path('partner-admin/keys', admin_views.PartnerAdminKeyListView.as_view()),
    path('partner-admin/keys/<str:key_id>', admin_views.PartnerAdminKeyDetailView.as_view()),
    path('partner-admin/payroll/runs', admin_views.PartnerAdminPayrollRunListCreateView.as_view()),
    path('partner-admin/payroll/runs/<int:run_id>/approve', admin_views.PartnerAdminPayrollRunApproveView.as_view()),
]
