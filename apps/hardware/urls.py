from django.urls import path
from rest_framework.routers import DefaultRouter

from apps.hardware.views import DeviceEventIngestView, DeviceSyncView, DeviceViewSet

router = DefaultRouter()
router.register('devices', DeviceViewSet, basename='device')

urlpatterns = [
    # Direct spec routes (spec/12 §7)
    path('device/sync', DeviceSyncView.as_view(), name='device-sync'),
    path('device/sync/', DeviceSyncView.as_view(), name='device-sync-slash'),
    path('device/events', DeviceEventIngestView.as_view(), name='device-events'),
    path('device/events/', DeviceEventIngestView.as_view(), name='device-events-slash'),
] + router.urls

