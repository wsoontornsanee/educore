"""Serializers for POST /analytics/events/ (spec/08 §5)."""
from rest_framework import serializers
from apps.core.models import ANALYTICS_EVENT_NAMES


class AnalyticsEventItemSerializer(serializers.Serializer):
    event_name = serializers.ChoiceField(choices=ANALYTICS_EVENT_NAMES)
    school_id = serializers.IntegerField(required=False, allow_null=True, default=None)
    occurred_at = serializers.DateTimeField()


class AnalyticsEventBatchSerializer(serializers.Serializer):
    events = serializers.ListField(child=serializers.DictField(), allow_empty=False)
