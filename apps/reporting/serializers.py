from rest_framework import serializers

from apps.reporting.models import RptDailyAttendance, RptWalletActivity


class RptWalletActivitySerializer(serializers.ModelSerializer):
    class Meta:
        model = RptWalletActivity
        fields = [
            'id', 'school', 'date', 'currency', 'topups', 'purchases',
            'commission', 'active_wallets', 'computed_at',
        ]
        read_only_fields = fields


class RptDailyAttendanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = RptDailyAttendance
        fields = [
            'id', 'school', 'date', 'class_group', 'present', 'late',
            'sick', 'permitted', 'absent', 'rate_pct', 'computed_at',
        ]
        read_only_fields = fields
