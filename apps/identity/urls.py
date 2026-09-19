"""URL routing for Identity, User Profile, and Entitlements (spec/02 §6, §7)."""
from django.urls import path
from apps.core.routers import EduCoreRouter
from .views import (
    CurrentUserView,
    SpendingPinResetView,
    SpendingPinView,
    EduCoreTokenObtainPairView,
    EduCoreTokenRefreshView,
    FoundationEntitlementViewSet,
    GuardianChildrenView,
    MicrosoftTenantConfigView,
    RequestOtpView,
    SocialLinkView,
    SocialLinksListView,
    SocialLoginView,
    VerifyOtpView,
    StaffViewSet,
    StaffRoleDetailView,
    StaffRoleListView,
    StudentViewSet,
)

router = EduCoreRouter()
router.register('foundation/entitlements', FoundationEntitlementViewSet, basename='foundation-entitlement')
router.register('staff', StaffViewSet, basename='staff')
router.register('students', StudentViewSet, basename='student')

urlpatterns = [
    path('staff/<int:staff_id>/roles/', StaffRoleListView.as_view(), name='api-staff-roles'),
    path(
        'staff/<int:staff_id>/roles/<int:assignment_id>/',
        StaffRoleDetailView.as_view(),
        name='api-staff-role-detail',
    ),
    path('auth/token/', EduCoreTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('auth/token/refresh/', EduCoreTokenRefreshView.as_view(), name='token_refresh'),
    path('auth/otp/request/', RequestOtpView.as_view(), name='otp-request'),
    path('auth/otp/verify/', VerifyOtpView.as_view(), name='otp-verify'),
    path('auth/sso/login/', SocialLoginView.as_view(), name='sso-login'),
    path('auth/sso/link/', SocialLinkView.as_view(), name='sso-link'),
    path('auth/sso/links/', SocialLinksListView.as_view(), name='sso-links'),
    path('auth/sso/microsoft-tenant/', MicrosoftTenantConfigView.as_view(), name='sso-microsoft-tenant'),
    path('me', CurrentUserView.as_view(), name='current-user'),
    path('me/pin/', SpendingPinView.as_view(), name='me-pin'),
    path('me/pin/reset/', SpendingPinResetView.as_view(), name='me-pin-reset'),
    path('me/children/', GuardianChildrenView.as_view(), name='guardian-children'),
] + router.urls


