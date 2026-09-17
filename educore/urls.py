"""URL configuration for educore project."""
from django.contrib import admin
from django.urls import include, path

from apps.identity.web_views import FoundationMicrosoftTenantSettingsView, WebLoginView

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
    path('api/v1/', include('apps.campus.urls')),
    path('api/v1/campus/', include('apps.campus.urls')),
    # Partner & Vendor Integration API (spec/18) — HMAC-key partner surface
    # and its foundation-admin management surface.
    path('api/v1/', include('apps.partners.urls')),
    # Session-auth web (HTMX) pages — HTML responses, distinct from /api/v1/ JSON
    path('web/auth/', include('apps.identity.web_urls')),
    path('web/login/', WebLoginView.as_view(), name='login'),
    path('web/foundation/settings/microsoft-tenant/', FoundationMicrosoftTenantSettingsView.as_view(), name='web-foundation-ms-tenant-settings'),
    path('web/academic/', include('apps.academic.web_urls')),
    # Public marketing website — no auth, no tenancy, root-mounted
    path('', include('apps.marketing.urls')),
]
