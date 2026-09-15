"""URL routing for Identity, User Profile, and Entitlements (spec/02 §6, §7)."""
from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import CurrentUserView, FoundationEntitlementViewSet, StaffViewSet, StudentViewSet

router = DefaultRouter()
router.register('foundation/entitlements', FoundationEntitlementViewSet, basename='foundation-entitlement')
router.register('staff', StaffViewSet, basename='staff')
router.register('students', StudentViewSet, basename='student')

urlpatterns = [
    path('me', CurrentUserView.as_view(), name='current-user'),
] + router.urls


