"""Web authentication URL routes for apps.identity — mounted under /web/auth/."""
from django.urls import path
from .web_views import WebLoginView, WebSSOLoginView, WebLogoutView

urlpatterns = [
    path('login/', WebLoginView.as_view(), name='web-login'),
    path('sso/login/', WebSSOLoginView.as_view(), name='web-sso-login'),
    path('logout/', WebLogoutView.as_view(), name='web-logout'),
]
