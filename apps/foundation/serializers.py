"""Serializers for Foundation portal and School management (spec/02, spec/03)."""
from rest_framework import serializers
from apps.core.models import AuditEvent
from apps.identity.models import Foundation, School
from .models import RptFoundationKPI

class SchoolSerializer(serializers.ModelSerializer):
    """Serializer for School CRUD operations."""
    class Meta:
        model = School
        fields = [
            'id',
            'foundation_id',
            'name',
            'npsn',
            'level',
            'curriculum',
            'timezone',
            'base_currency',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'foundation_id', 'created_at', 'updated_at']

class FoundationSettingsSerializer(serializers.ModelSerializer):
    """Serializer for Foundation governance settings."""
    class Meta:
        model = Foundation
        fields = [
            'id',
            'legal_name',
            'brand_name',
            'npwp',
            'address',
            'timezone',
            'reporting_currency',
            'approval_threshold',
            'plan_tier',
            'status',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'plan_tier', 'status', 'created_at', 'updated_at']

class FoundationKPISerializer(serializers.ModelSerializer):
    """Serializer for Foundation Dashboard KPIs."""
    collection_rate_pct = serializers.DecimalField(max_digits=5, decimal_places=2, read_only=True)

    class Meta:
        model = RptFoundationKPI
        fields = [
            'id',
            'foundation_id',
            'school_id',
            'period_start',
            'period_end',
            'billed',
            'collected',
            'outstanding',
            'collection_rate_pct',
            'ar_0_30',
            'ar_31_60',
            'ar_61_90',
            'ar_90_plus',
            'campus_spend',
            'active_students',
            'avg_attendance_pct',
            'currency',
            'reporting_currency',
            'fx_rate_date',
            'computed_at',
        ]

class AuditEventSerializer(serializers.ModelSerializer):
    """Serializer for the Audit Explorer (FND-010)."""
    class Meta:
        model = AuditEvent
        fields = [
            'id',
            'actor_id',
            'role',
            'foundation_id',
            'school_id',
            'ip_address',
            'action',
            'entity_type',
            'entity_id',
            'diff',
            'timestamp',
        ]


class CampusComparisonSchoolSerializer(serializers.Serializer):
    """Per-school aggregated KPI and ranking data for campus comparison (spec/03 §2, FND-004)."""
    rank = serializers.IntegerField(read_only=True)
    school_id = serializers.IntegerField(read_only=True)
    school_name = serializers.CharField(read_only=True)
    npsn = serializers.CharField(read_only=True, allow_blank=True)
    level = serializers.CharField(read_only=True)
    currency = serializers.CharField(read_only=True)
    billed = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)
    collected = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)
    collection_rate_pct = serializers.DecimalField(max_digits=5, decimal_places=2, coerce_to_string=True)
    outstanding = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)
    ar_0_30 = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)
    ar_31_60 = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)
    ar_61_90 = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)
    ar_90_plus = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)
    campus_spend = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)
    active_students = serializers.IntegerField(read_only=True)
    avg_attendance_pct = serializers.DecimalField(max_digits=5, decimal_places=2, coerce_to_string=True)


class CampusComparisonSummarySerializer(serializers.Serializer):
    """Consolidated summary totals across compared schools in reporting currency (FND-005b)."""
    total_schools = serializers.IntegerField(read_only=True)
    billed = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)
    collected = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)
    collection_rate_pct = serializers.DecimalField(max_digits=5, decimal_places=2, coerce_to_string=True)
    outstanding = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)
    campus_spend = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)
    active_students = serializers.IntegerField(read_only=True)
    avg_attendance_pct = serializers.DecimalField(max_digits=5, decimal_places=2, coerce_to_string=True)
    reporting_currency = serializers.CharField(read_only=True)


class EnrolmentPipelineGradeBreakdownSerializer(serializers.Serializer):
    """Breakdown by grade within a campus (spec/03 §2)."""
    grade_level = serializers.IntegerField(allow_null=True)
    grade_name = serializers.CharField()
    prospects = serializers.IntegerField()
    accepted = serializers.IntegerField()
    active = serializers.IntegerField()
    churned = serializers.IntegerField()
    conversion_rate_pct = serializers.FloatField()
    retention_rate_pct = serializers.FloatField()


class EnrolmentPipelineCampusItemSerializer(serializers.Serializer):
    """Per-campus enrolment pipeline metrics (spec/03 §2, §5)."""
    school_id = serializers.IntegerField()
    school_name = serializers.CharField()
    npsn = serializers.CharField(allow_blank=True)
    level = serializers.CharField()
    prospects = serializers.IntegerField()
    accepted = serializers.IntegerField()
    active = serializers.IntegerField()
    churned = serializers.IntegerField()
    conversion_rate_pct = serializers.FloatField()
    retention_rate_pct = serializers.FloatField()
    grades = EnrolmentPipelineGradeBreakdownSerializer(many=True, required=False)


class EnrolmentPipelineGradeItemSerializer(serializers.Serializer):
    """Per-grade enrolment pipeline metrics across campuses (spec/03 §2, §5)."""
    grade_level = serializers.IntegerField(allow_null=True)
    grade_name = serializers.CharField()
    prospects = serializers.IntegerField()
    accepted = serializers.IntegerField()
    active = serializers.IntegerField()
    churned = serializers.IntegerField()
    conversion_rate_pct = serializers.FloatField()
    retention_rate_pct = serializers.FloatField()
    campuses = serializers.ListField(child=serializers.DictField(), required=False)


class EnrolmentPipelineSummarySerializer(serializers.Serializer):
    """Consolidated summary for foundation enrolment pipeline (spec/03 §2)."""
    total_schools = serializers.IntegerField()
    total_prospects = serializers.IntegerField()
    total_accepted = serializers.IntegerField()
    total_active = serializers.IntegerField()
    total_churned = serializers.IntegerField()
    conversion_rate_pct = serializers.FloatField()
    retention_rate_pct = serializers.FloatField()
