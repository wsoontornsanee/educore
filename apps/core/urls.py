from django.urls import path

from apps.core.views import ConfirmUploadView, InitiateUploadView

urlpatterns = [
    path('uploads/', InitiateUploadView.as_view(), name='files-initiate-upload'),
    path('uploads/<int:pk>/confirm/', ConfirmUploadView.as_view(), name='files-confirm-upload'),
]
