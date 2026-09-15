import datetime
import json
import logging
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.pagination import StandardCursorPagination
from apps.core.services import audit
from apps.identity.permissions import HasRequiredPermission
from apps.notifications.models import (
    ChannelType,
    DeliveryStatus,
    NotificationDelivery,
    NotificationIntent,
    NotificationPreference,
    NotificationTemplate,
)
from apps.notifications.serializers import (
    NotificationDeliverySerializer,
    NotificationIntentSerializer,
    NotificationPreferenceSerializer,
    NotificationTemplateSerializer,
)
from educore.middleware.tenancy import get_current_foundation_id

logger = logging.getLogger(__name__)


class NotificationTemplateViewSet(viewsets.ModelViewSet):
    """Admin CRUD for multi-channel message templates (spec/13 §2, NTF-005)."""
    serializer_class = NotificationTemplateSerializer
    pagination_class = StandardCursorPagination
    permission_classes = [HasRequiredPermission]
    required_permission = 'school_config.write'

    def get_queryset(self):
        foundation_id = get_current_foundation_id()
        if not foundation_id:
            return NotificationTemplate.objects.none()
        return NotificationTemplate.objects.filter(
            foundation_id=foundation_id,
            deleted_at__isnull=True
        ).order_by('-created_at')

    def perform_create(self, serializer):
        foundation_id = get_current_foundation_id()
        template = serializer.save(foundation_id=foundation_id)
        audit(
            action='notifications.template.created',
            entity_type='NotificationTemplate',
            entity_id=template.id,
            foundation_id=foundation_id,
            diff={'key': template.key, 'channel': template.channel, 'version': template.version}
        )

    def perform_destroy(self, instance):
        instance.delete()
        audit(
            action='notifications.template.deleted',
            entity_type='NotificationTemplate',
            entity_id=instance.id,
            foundation_id=instance.foundation_id,
            diff={'key': instance.key}
        )


class NotificationDeliveryViewSet(viewsets.ReadOnlyModelViewSet):
    """Admin delivery receipt monitor view (spec/13 §5, NTF-010)."""
    serializer_class = NotificationDeliverySerializer
    pagination_class = StandardCursorPagination
    permission_classes = [HasRequiredPermission]
    required_permission = 'attendance.read'

    def get_queryset(self):
        foundation_id = get_current_foundation_id()
        if not foundation_id:
            return NotificationDelivery.objects.none()

        qs = NotificationDelivery.objects.filter(
            foundation_id=foundation_id,
            deleted_at__isnull=True
        ).select_related('intent').order_by('-created_at')

        # Filter by category
        category = self.request.query_params.get('category')
        if category:
            qs = qs.filter(intent__category=category)

        # Filter by channel
        channel = self.request.query_params.get('channel')
        if channel:
            qs = qs.filter(channel=channel)

        # Filter by status
        status_param = self.request.query_params.get('status')
        if status_param:
            qs = qs.filter(status=status_param)

        # Filter by student_id
        student_id = self.request.query_params.get('student_id')
        if student_id:
            qs = qs.filter(intent__payload__student_id=str(student_id))

        # Filter by date range
        from_date = self.request.query_params.get('from')
        to_date = self.request.query_params.get('to')
        if from_date:
            qs = qs.filter(created_at__date__gte=from_date)
        if to_date:
            qs = qs.filter(created_at__date__lte=to_date)

        return qs


class MyNotificationsView(APIView):
    """User in-app notifications list and mark-as-read (spec/13 §5)."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        foundation_id = get_current_foundation_id()
        intents = NotificationIntent.objects.filter(
            foundation_id=foundation_id,
            recipient_user=request.user,
            deleted_at__isnull=True,
        ).order_by('-created_at')[:50]

        serializer = NotificationIntentSerializer(intents, many=True)
        return Response(serializer.data)


class MarkNotificationReadView(APIView):
    """Mark a notification delivery as read (spec/13 §5)."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk=None):
        foundation_id = get_current_foundation_id()
        delivery = NotificationDelivery.objects.filter(
            id=pk,
            foundation_id=foundation_id,
            deleted_at__isnull=True,
        ).first()

        if not delivery:
            # Check intent
            intent = NotificationIntent.objects.filter(
                id=pk,
                foundation_id=foundation_id,
                recipient_user=request.user,
                deleted_at__isnull=True,
            ).first()
            if not intent:
                return Response({'detail': _('Notifikasi tidak ditemukan')}, status=status.HTTP_404_NOT_FOUND)
            delivery = intent.deliveries.first()

        if delivery:
            delivery.status = DeliveryStatus.READ
            delivery.read_at = timezone.now()
            delivery.save(update_fields=['status', 'read_at', 'updated_at'])

        return Response({'status': 'ok', 'read_at': timezone.now().isoformat()})


class MyNotificationPreferencesView(APIView):
    """User preferences for categories, channels, and quiet hours (NTF-001, NTF-002, NTF-013)."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        foundation_id = get_current_foundation_id()
        prefs = NotificationPreference.objects.filter(
            foundation_id=foundation_id,
            user=request.user,
            deleted_at__isnull=True,
        )
        serializer = NotificationPreferenceSerializer(prefs, many=True)
        return Response(serializer.data)

    def put(self, request):
        foundation_id = get_current_foundation_id()
        data = request.data
        category = data.get('category')
        if not category:
            return Response({'detail': _('Kategori wajib diisi')}, status=status.HTTP_400_BAD_REQUEST)

        channels = data.get('channels', [])
        enabled = data.get('enabled', True)
        quiet_hours_start = data.get('quiet_hours_start', '21:00')
        quiet_hours_end = data.get('quiet_hours_end', '06:00')

        pref, created = NotificationPreference.objects.update_or_create(
            foundation_id=foundation_id,
            user=request.user,
            category=category,
            defaults={
                'channels': channels,
                'enabled': enabled,
                'quiet_hours_start': quiet_hours_start,
                'quiet_hours_end': quiet_hours_end,
                'deleted_at': None,
            }
        )

        serializer = NotificationPreferenceSerializer(pref)
        return Response(serializer.data)


@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def whatsapp_webhook_status(request):
    """
    Webhook receiver for WhatsApp Business Cloud API delivery receipts (spec/13 §5, NTF-010).
    
    Receives status callbacks (SENT, DELIVERED, READ, FAILED) and updates NotificationDelivery rows.
    """
    data = request.data
    logger.info(f"Received WhatsApp webhook status update: {data}")

    updated_count = 0

    # 1. Handle Meta Cloud API format
    # {"entry": [{"changes": [{"value": {"statuses": [{"id": "wamid.xxx", "status": "delivered"}]}}]}]}
    statuses = []
    if 'entry' in data:
        for entry in data.get('entry', []):
            for change in entry.get('changes', []):
                val = change.get('value', {})
                for st in val.get('statuses', []):
                    statuses.append(st)
    elif 'statuses' in data:
        statuses = data.get('statuses', [])
    elif 'provider_message_id' in data:
        statuses.append(data)

    for item in statuses:
        msg_id = item.get('id') or item.get('provider_message_id')
        raw_status = str(item.get('status', '')).upper()
        timestamp_str = item.get('timestamp')

        if not msg_id:
            continue

        deliveries = NotificationDelivery.all_tenants.filter(
            provider_message_id=msg_id,
            deleted_at__isnull=True
        )

        for delivery in deliveries:
            if raw_status in ['DELIVERED']:
                delivery.status = DeliveryStatus.DELIVERED
                delivery.delivered_at = timezone.now()
            elif raw_status in ['READ']:
                delivery.status = DeliveryStatus.READ
                delivery.read_at = timezone.now()
            elif raw_status in ['FAILED', 'UNDELIVERED']:
                delivery.status = DeliveryStatus.FAILED
                errors = item.get('errors', [])
                if errors and isinstance(errors, list):
                    delivery.error_code = str(errors[0].get('code', 'WHATSAPP_ERROR'))
                    delivery.error_message = str(errors[0].get('title', 'Delivery failed'))
                else:
                    delivery.error_code = 'DELIVERY_FAILED'

            delivery.save()
            updated_count += 1

    return Response({'status': 'ok', 'updated': updated_count}, status=status.HTTP_200_OK)
