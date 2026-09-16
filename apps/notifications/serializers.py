from rest_framework import serializers
from apps.notifications.models import (
    DevicePushToken,
    NotificationDelivery,
    NotificationIntent,
    NotificationPreference,
    NotificationTemplate,
)


class NotificationTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationTemplate
        fields = [
            'id',
            'foundation_id',
            'school',
            'key',
            'channel',
            'locale',
            'subject',
            'body',
            'variables',
            'version',
            'approval_status',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'foundation_id', 'created_at', 'updated_at']


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationPreference
        fields = [
            'id',
            'foundation_id',
            'user',
            'category',
            'channels',
            'quiet_hours_start',
            'quiet_hours_end',
            'enabled',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'foundation_id', 'user', 'created_at', 'updated_at']


class NotificationDeliverySerializer(serializers.ModelSerializer):
    cost_display = serializers.SerializerMethodField()

    class Meta:
        model = NotificationDelivery
        fields = [
            'id',
            'foundation_id',
            'intent',
            'channel',
            'provider',
            'provider_message_id',
            'recipient_target',
            'rendered_subject',
            'rendered_body',
            'status',
            'error_code',
            'error_message',
            'sent_at',
            'delivered_at',
            'read_at',
            'cost',
            'cost_currency',
            'cost_display',
            'created_at',
        ]
        read_only_fields = ['id', 'foundation_id', 'created_at']

    def get_cost_display(self, obj):
        return f"{obj.cost_currency} {obj.cost}"


class NotificationIntentSerializer(serializers.ModelSerializer):
    deliveries = NotificationDeliverySerializer(many=True, read_only=True)

    class Meta:
        model = NotificationIntent
        fields = [
            'id',
            'foundation_id',
            'school',
            'recipient_user',
            'recipient_phone',
            'recipient_email',
            'recipient_name',
            'category',
            'template_key',
            'payload',
            'priority',
            'scheduled_for',
            'status',
            'dedupe_key',
            'sent_at',
            'cancelled_at',
            'cancellation_reason',
            'deliveries',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'foundation_id', 'created_at', 'updated_at']


class DevicePushTokenSerializer(serializers.ModelSerializer):
    class Meta:
        model = DevicePushToken
        fields = [
            'id',
            'foundation_id',
            'token',
            'platform',
            'is_active',
            'last_used_at',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'foundation_id', 'last_used_at', 'created_at', 'updated_at']
