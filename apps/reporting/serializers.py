from rest_framework import serializers

from apps.reporting.models import (
    RptAcademicPerformance,
    RptActiveStudent,
    RptDailyAttendance,
    RptDailyFinance,
    RptWalletActivity,
)


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


class RptAcademicPerformanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = RptAcademicPerformance
        fields = [
            'id', 'school', 'term', 'class_group', 'subject',
            'avg_score', 'band_distribution', 'computed_at',
        ]
        read_only_fields = fields


class RptActiveStudentSerializer(serializers.ModelSerializer):
    class Meta:
        model = RptActiveStudent
        fields = ['id', 'school', 'month', 'active_count', 'computed_at']
        read_only_fields = fields


class RptDailyFinanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = RptDailyFinance
        fields = [
            'id', 'school', 'date', 'currency', 'billed', 'collected',
            'outstanding', 'payments_count', 'fees', 'computed_at',
        ]
        read_only_fields = fields
