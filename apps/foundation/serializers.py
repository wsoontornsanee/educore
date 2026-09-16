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
