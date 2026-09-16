"""URL routing for Foundation portal and School management (spec/02, spec/03)."""
from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import (
    CampusComparisonView,
    FoundationApprovalDecideView,
    FoundationApprovalsInboxView,
    FoundationAuditEventView,
    FoundationExportStatusView,
    FoundationExportView,
    FoundationKPIView,
    FoundationSettingsView,
    SchoolViewSet,
)

router = DefaultRouter()
router.register('schools', SchoolViewSet, basename='school')

urlpatterns = [
    path('foundation/settings', FoundationSettingsView.as_view(), name='foundation-settings'),
    path('foundation/kpis', FoundationKPIView.as_view(), name='foundation-kpis'),
    path('foundation/audit', FoundationAuditEventView.as_view(), name='foundation-audit'),
    path('foundation/schools/compare', CampusComparisonView.as_view(), name='foundation-campus-compare'),
    path('foundation/exports', FoundationExportView.as_view(), name='foundation-exports'),
    path('foundation/exports/<int:job_id>', FoundationExportStatusView.as_view(), name='foundation-export-status'),
    path('foundation/approvals', FoundationApprovalsInboxView.as_view(), name='foundation-approvals'),
    path('foundation/approvals/<str:approval_id>/decide', FoundationApprovalDecideView.as_view(), name='foundation-approval-decide'),
] + router.urls

