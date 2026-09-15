from rest_framework import serializers

from apps.attendance.models import (
    AttendanceDay,
    AttendanceRule,
    AttendanceSource,
    AttendanceStatus,
    Credential,
    CredentialStatus,
    CredentialType,
    GateDirection,
    GateEvent,
    GateEventStatus,
    GateMethod,
)
from apps.identity.models import Student, Staff


class CredentialSerializer(serializers.ModelSerializer):
    holder_type = serializers.CharField(read_only=True)
    holder_name = serializers.CharField(read_only=True)
    is_valid_now = serializers.BooleanField(read_only=True)
    student_nis = serializers.CharField(source='student.nis', read_only=True)
    student_nisn = serializers.CharField(source='student.nisn', read_only=True)
    staff_nip = serializers.CharField(source='staff.nip', read_only=True)

    class Meta:
        model = Credential
        fields = [
            'id',
            'student',
            'student_nis',
            'student_nisn',
            'staff',
            'staff_nip',
            'holder_type',
            'holder_name',
            'type',
            'uid',
            'card_number',
            'status',
            'is_valid_now',
            'issued_at',
            'revoked_at',
            'revoked_reason',
            'expires_at',
            'is_used',
            'replacement_fee_posted',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id',
            'status',
            'is_valid_now',
            'issued_at',
            'revoked_at',
            'revoked_reason',
            'expires_at',
            'is_used',
            'replacement_fee_posted',
            'created_at',
            'updated_at',
        ]


class CredentialIssueSerializer(serializers.Serializer):
    student_id = serializers.IntegerField(required=False, allow_null=True)
    staff_id = serializers.IntegerField(required=False, allow_null=True)
    type = serializers.ChoiceField(choices=CredentialType.choices, default=CredentialType.RFID)
    uid = serializers.CharField(max_length=128, required=False, allow_blank=True)
    card_number = serializers.CharField(max_length=64, required=False, allow_blank=True)
    expires_in_minutes = serializers.IntegerField(default=15, min_value=1, max_value=60)

    def validate(self, attrs):
        student_id = attrs.get('student_id')
        staff_id = attrs.get('staff_id')
        cred_type = attrs.get('type')
        uid = attrs.get('uid')

        if not student_id and not staff_id:
            raise serializers.ValidationError("Kredensial harus ditetapkan kepada siswa atau staf.")
        if student_id and staff_id:
            raise serializers.ValidationError("Kredensial tidak dapat ditetapkan kepada siswa dan staf secara bersamaan.")

        if cred_type in [CredentialType.RFID, CredentialType.NFC] and not uid:
            raise serializers.ValidationError("UID kartu wajib diisi untuk kredensial RFID atau NFC.")

        return attrs


class CredentialRevokeSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255, required=True)
    post_replacement_fee = serializers.BooleanField(default=False)


class CredentialVerifySerializer(serializers.Serializer):
    uid = serializers.CharField(max_length=128, required=True)
    mark_used = serializers.BooleanField(default=False)


class GateEventBatchItemSerializer(serializers.Serializer):
    event_uuid = serializers.UUIDField(required=True)
    device_id = serializers.IntegerField(required=True)
    occurred_at = serializers.DateTimeField(required=True)
    raw_uid = serializers.CharField(max_length=128, required=False, allow_blank=True, default='')
    student_id = serializers.IntegerField(required=False, allow_null=True)
    staff_id = serializers.IntegerField(required=False, allow_null=True)
    direction = serializers.ChoiceField(choices=GateDirection.choices, required=False, allow_null=True)
    method = serializers.ChoiceField(choices=GateMethod.choices, default=GateMethod.RFID)
    confidence = serializers.DecimalField(max_digits=5, decimal_places=4, required=False, allow_null=True)
    photo_key = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')
    replayed = serializers.BooleanField(default=False)


class GateEventBatchIngestSerializer(serializers.Serializer):
    school_id = serializers.IntegerField(required=False, allow_null=True)
    events = GateEventBatchItemSerializer(many=True, required=True)

    def validate_events(self, value):
        if not value:
            raise serializers.ValidationError("Daftar event tidak boleh kosong.")
        return value


class GateEventSerializer(serializers.ModelSerializer):
    student_nis = serializers.CharField(source='student.nis', read_only=True)
    student_name = serializers.CharField(source='student.person.full_name', read_only=True)
    staff_nip = serializers.CharField(source='staff.nip', read_only=True)
    staff_name = serializers.CharField(source='staff.person.full_name', read_only=True)
    device_name = serializers.CharField(source='device.name', read_only=True)

    class Meta:
        model = GateEvent
        fields = [
            'id',
            'event_uuid',
            'school',
            'device',
            'device_name',
            'student',
            'student_nis',
            'student_name',
            'staff',
            'staff_nip',
            'staff_name',
            'credential',
            'raw_uid',
            'direction',
            'occurred_at',
            'method',
            'confidence',
            'photo_key',
            'status',
            'reject_reason',
            'is_duplicate_scan',
            'replayed',
            'created_at',
        ]
        read_only_fields = fields


class AttendanceRuleSerializer(serializers.ModelSerializer):
    school_name = serializers.CharField(source='school.name', read_only=True)

    class Meta:
        model = AttendanceRule
        fields = [
            'id',
            'school',
            'school_name',
            'late_after_time',
            'absent_cutoff_time',
            'debounce_seconds',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class AttendanceDaySerializer(serializers.ModelSerializer):
    student_nis = serializers.CharField(source='student.nis', read_only=True)
    student_name = serializers.CharField(source='student.person.full_name', read_only=True)
    school_name = serializers.CharField(source='school.name', read_only=True)

    class Meta:
        model = AttendanceDay
        fields = [
            'id',
            'school',
            'school_name',
            'student',
            'student_nis',
            'student_name',
            'date',
            'status',
            'first_in_at',
            'last_out_at',
            'source',
            'note',
            'is_override',
            'original_status',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id',
            'school',
            'student',
            'date',
            'first_in_at',
            'last_out_at',
            'source',
            'original_status',
            'created_at',
            'updated_at',
        ]


class AttendanceDayOverrideSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=AttendanceStatus.choices, required=True)
    note = serializers.CharField(max_length=255, required=True, min_length=1)


class ManualCheckInSerializer(serializers.Serializer):
    student_id = serializers.IntegerField(required=True)
    direction = serializers.ChoiceField(choices=GateDirection.choices, default=GateDirection.IN)
    occurred_at = serializers.DateTimeField(required=False, allow_null=True)
    reason = serializers.CharField(max_length=255, required=True, min_length=1)


