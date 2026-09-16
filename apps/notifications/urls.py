from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.notifications.views import (
    DevicePushTokenView,
    MarkNotificationReadView,
    MyNotificationPreferencesView,
    MyNotificationsView,
    NotificationDeliveryViewSet,
    NotificationTemplateViewSet,
    whatsapp_webhook_status,
)

router = DefaultRouter()
router.register(r'notifications/templates', NotificationTemplateViewSet, basename='notification-templates')
router.register(r'notifications/deliveries', NotificationDeliveryViewSet, basename='notification-deliveries')

urlpatterns = [
    path('', include(router.urls)),
    path('me/notifications/', MyNotificationsView.as_view(), name='my-notifications'),
    path('me/notifications/<int:pk>/read/', MarkNotificationReadView.as_view(), name='mark-notification-read'),
    path('me/notification-preferences/', MyNotificationPreferencesView.as_view(), name='my-notification-preferences'),
    path('me/push-tokens/', DevicePushTokenView.as_view(), name='my-push-tokens'),
    path('webhooks/whatsapp/status/', whatsapp_webhook_status, name='whatsapp-webhook-status'),
]
