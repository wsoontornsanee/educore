"""URL routing for Identity, User Profile, and Entitlements (spec/02 §6, §7)."""
from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import CurrentUserView, FoundationEntitlementViewSet

router = DefaultRouter()
router.register('foundation/entitlements', FoundationEntitlementViewSet, basename='foundation-entitlement')

urlpatterns = [
    path('me', CurrentUserView.as_view(), name='current-user'),
] + router.urls
