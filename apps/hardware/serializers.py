from rest_framework import serializers

from apps.hardware.models import Device, DeviceClass, DeviceDirection, DeviceEventStaging, DeviceStatus
from apps.identity.models import School


class DeviceSerializer(serializers.ModelSerializer):
    school_name = serializers.CharField(source='school.name', read_only=True)
    device_class_display = serializers.CharField(source='get_device_class_display', read_only=True)
    direction_display = serializers.CharField(source='get_direction_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = Device
        fields = [
            'id',
            'school',
            'school_name',
            'device_code',
            'name',
            'device_class',
            'device_class_display',
            'direction',
            'direction_display',
            'location',
            'status',
            'status_display',
            'ip_address',
            'mac_address',
            'firmware_version',
            'last_heartbeat_at',
            'today_event_count',
            'config',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'last_heartbeat_at']


class DeviceCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Device
        fields = [
            'school',
            'device_code',
            'name',
            'device_class',
            'direction',
            'location',
            'status',
            'ip_address',
            'mac_address',
            'firmware_version',
            'config',
        ]

    def validate_school(self, value):
        foundation_id = getattr(self.context.get('request'), 'foundation_id', None)
        if foundation_id and str(value.foundation_id) != str(foundation_id):
            raise serializers.ValidationError("Sekolah tidak terdaftar dalam yayasan aktif.")
        return value


class DeviceEventItemSerializer(serializers.Serializer):
    """One raw event within a `POST /device/events` upload (spec/12 §7, HW-005).

    Shape mirrors `attendance.GateEventBatchItemSerializer`'s fields exactly
    (duplicated, not imported, to keep the device-facing staging layer free of
    a module-level dependency on the attendance domain app) so a staged
    payload can be replayed into `ingest_gate_events` unchanged.
    """
    event_uuid = serializers.UUIDField(required=True)
    device_id = serializers.IntegerField(required=True)
    occurred_at = serializers.DateTimeField(required=True)
    raw_uid = serializers.CharField(max_length=128, required=False, allow_blank=True, default='')
    student_id = serializers.IntegerField(required=False, allow_null=True)
    staff_id = serializers.IntegerField(required=False, allow_null=True)
    direction = serializers.ChoiceField(choices=[('IN', 'IN'), ('OUT', 'OUT')], required=False, allow_null=True)
    method = serializers.CharField(max_length=16, required=False, default='RFID')
    confidence = serializers.DecimalField(max_digits=5, decimal_places=4, required=False, allow_null=True)
    photo_key = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')
    replayed = serializers.BooleanField(default=False)


class DeviceEventBatchSerializer(serializers.Serializer):
    school_id = serializers.IntegerField(required=True)
    batch = DeviceEventItemSerializer(many=True, required=True)

    def validate_batch(self, value):
        if not value:
            raise serializers.ValidationError("Daftar event tidak boleh kosong.")
        return value


class DeviceEventStagingSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeviceEventStaging
        fields = ['id', 'event_uuid', 'school', 'device', 'status', 'applied_at', 'error_text', 'created_at']
        read_only_fields = fields


class DeviceHeartbeatSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=DeviceStatus.choices, default=DeviceStatus.ONLINE)
    firmware_version = serializers.CharField(max_length=64, required=False, allow_blank=True)
    today_event_count = serializers.IntegerField(min_value=0, required=False)
    ip_address = serializers.IPAddressField(required=False, allow_null=True)
    mac_address = serializers.CharField(max_length=32, required=False, allow_blank=True)
    metrics = serializers.DictField(required=False)
