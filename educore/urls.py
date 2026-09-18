"""URL configuration for educore project."""
from django.contrib import admin
from django.urls import include, path

from apps.calendar_sync.views import CalendarOAuthCallbackView
from apps.identity.web_views import (
    ConsoleInboxView,
    FoundationMicrosoftTenantSettingsView,
    FoundationOverviewLandingView,
    SchoolAdminTodayLandingView,
    TeacherAgendaLandingView,
    FinanceBillingLandingView,
    WebConsoleHomeView,
    WebLoginView,
)

urlpatterns = [
    path('admin/', admin.site.urls),
    # Base API route: /api/v1/ per spec/01 §8.1
    path('api/v1/', include('apps.identity.urls')),
    path('api/v1/', include('apps.foundation.urls')),
    path('api/v1/files/', include('apps.core.urls')),
    path('api/v1/', include('apps.core.analytics_urls')),
    path('api/v1/', include('apps.hardware.urls')),
    path('api/v1/', include('apps.attendance.urls')),
    path('api/v1/', include('apps.notifications.urls')),
    path('api/v1/finance/', include('apps.finance.urls')),
    path('api/v1/academic/', include('apps.academic.urls')),
    path('api/v1/', include('apps.wallet.urls')),
    path('api/v1/reporting/', include('apps.reporting.urls')),
    path('api/v1/campus/', include('apps.campus.urls')),
    # Partner & Vendor Integration API (spec/18) — HMAC-key partner surface
    # and its foundation-admin management surface.
    path('api/v1/', include('apps.partners.urls')),
    # Calendar sync (spec/14 §6): API under /api/v1/, plus the public OAuth
    # callback the providers redirect back to (AllowAny, signed-state gated).
    path('api/v1/', include('apps.calendar_sync.urls')),
    path('web/auth/calendar/callback/', CalendarOAuthCallbackView.as_view(),
         name='calendar-oauth-callback'),
    # Session-auth web (HTMX) pages — HTML responses, distinct from /api/v1/ JSON
    path('web/auth/', include('apps.identity.web_urls')),
    path('web/login/', WebLoginView.as_view(), name='login'),
    path('web/home/', WebConsoleHomeView.as_view(), name='web-console-home'),
    path('web/home/inbox/', ConsoleInboxView.as_view(), name='console-inbox'),
    path('web/home/overview/', FoundationOverviewLandingView.as_view(), name='console-home-overview'),
    path('web/home/today/', SchoolAdminTodayLandingView.as_view(), name='console-home-today'),
    path('web/home/agenda/', TeacherAgendaLandingView.as_view(), name='console-home-agenda'),
    path('web/home/billing/', FinanceBillingLandingView.as_view(), name='console-home-billing'),
    path('web/foundation/settings/microsoft-tenant/', FoundationMicrosoftTenantSettingsView.as_view(), name='web-foundation-ms-tenant-settings'),
    path('web/academic/', include('apps.academic.web_urls')),
    path('web/status/', include('apps.status.web_urls')),
    path('web/', include('apps.core.web_urls')),
    # Language switch (django.views.i18n.set_language) — POST target for the
    # marketing site's ID/EN toggle. Not under /api/v1/: it's a browser-only,
    # session-cookie-setting form post, not a JSON API.
    path('i18n/', include('django.conf.urls.i18n')),
    # Public marketing website — no auth, no tenancy, root-mounted
    path('', include('apps.marketing.urls')),
    # Public service status page — no auth, no tenancy, non-tenant apps.status models
    path('status/', include('apps.status.urls')),
]
