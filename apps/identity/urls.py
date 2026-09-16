"""URL routing for Identity, User Profile, and Entitlements (spec/02 §6, §7)."""
from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import (
    CurrentUserView,
    EduCoreTokenObtainPairView,
    EduCoreTokenRefreshView,
    FoundationEntitlementViewSet,
    GuardianChildrenView,
    RequestOtpView,
    VerifyOtpView,
    StaffViewSet,
    StudentViewSet,
)

router = DefaultRouter()
router.register('foundation/entitlements', FoundationEntitlementViewSet, basename='foundation-entitlement')
router.register('staff', StaffViewSet, basename='staff')
router.register('students', StudentViewSet, basename='student')

urlpatterns = [
    path('auth/token/', EduCoreTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('auth/token/refresh/', EduCoreTokenRefreshView.as_view(), name='token_refresh'),
    path('auth/otp/request/', RequestOtpView.as_view(), name='otp-request'),
    path('auth/otp/verify/', VerifyOtpView.as_view(), name='otp-verify'),
    path('me', CurrentUserView.as_view(), name='current-user'),
    path('me/children/', GuardianChildrenView.as_view(), name='guardian-children'),
] + router.urls


