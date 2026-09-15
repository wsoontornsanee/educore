from rest_framework import serializers

from apps.hardware.models import Device, DeviceClass, DeviceDirection, DeviceStatus
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


class DeviceHeartbeatSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=DeviceStatus.choices, default=DeviceStatus.ONLINE)
    firmware_version = serializers.CharField(max_length=64, required=False, allow_blank=True)
    today_event_count = serializers.IntegerField(min_value=0, required=False)
    ip_address = serializers.IPAddressField(required=False, allow_null=True)
    mac_address = serializers.CharField(max_length=32, required=False, allow_blank=True)
    metrics = serializers.DictField(required=False)
