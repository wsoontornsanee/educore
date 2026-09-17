"""Web authentication URL routes for apps.identity — mounted under /web/auth/."""
from django.urls import path
from .web_views import (
    FoundationMicrosoftTenantSettingsView,
    WebLoginView,
    WebLogoutView,
    WebSSOLoginView,
)

urlpatterns = [
    path('login/', WebLoginView.as_view(), name='web-login'),
    path('sso/login/', WebSSOLoginView.as_view(), name='web-sso-login'),
    path('sso/microsoft-tenant/', FoundationMicrosoftTenantSettingsView.as_view(), name='web-sso-microsoft-tenant'),
    path('logout/', WebLogoutView.as_view(), name='web-logout'),
]

