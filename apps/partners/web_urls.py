"""Web (session-auth) URL routes for the Administrasi console's partner-key page — mounted under /web/admin/partners/."""
from django.urls import path

from .web_views import PartnerKeyRevokeView, PartnerKeyRotateView, PartnerKeysView

urlpatterns = [
    path('', PartnerKeysView.as_view(), name='admin-partners'),
    path('<str:key_id>/rotate/', PartnerKeyRotateView.as_view(), name='admin-partner-key-rotate'),
    path('<str:key_id>/revoke/', PartnerKeyRevokeView.as_view(), name='admin-partner-key-revoke'),
]
