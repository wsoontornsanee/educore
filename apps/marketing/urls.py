"""URL routes for the public marketing website (no auth, no /api/v1 prefix)."""
from django.urls import path

from . import views

app_name = 'marketing'

urlpatterns = [
    path('', views.HomeView.as_view(), name='home'),
    path('unduh/', views.DownloadsView.as_view(), name='downloads'),
    path('mitra-api/', views.PartnerApiView.as_view(), name='partner-api'),
]
