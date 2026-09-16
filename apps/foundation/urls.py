"""URL routing for Foundation portal and School management (spec/02, spec/03)."""
from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import CampusComparisonView, FoundationKPIView, FoundationSettingsView, SchoolViewSet

router = DefaultRouter()
router.register('schools', SchoolViewSet, basename='school')

urlpatterns = [
    path('foundation/settings', FoundationSettingsView.as_view(), name='foundation-settings'),
    path('foundation/kpis', FoundationKPIView.as_view(), name='foundation-kpis'),
    path('foundation/schools/compare', CampusComparisonView.as_view(), name='foundation-campus-compare'),
] + router.urls

