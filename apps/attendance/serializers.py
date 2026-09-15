from rest_framework import serializers

from apps.attendance.models import Credential, CredentialStatus, CredentialType
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
