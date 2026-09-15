from rest_framework import serializers
from apps.finance.models import (
    Discount,
    FeePlan,
    FeeType,
    SiblingDiscountPolicy,
    StudentFeeAssignment,
)


class FeeTypeSerializer(serializers.ModelSerializer):
    default_amount = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)

    class Meta:
        model = FeeType
        fields = [
            'id',
            'foundation_id',
            'school',
            'code',
            'name',
            'category',
            'recurrence',
            'default_amount',
            'currency',
            'taxable',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'foundation_id', 'created_at', 'updated_at']


class FeePlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = FeePlan
        fields = [
            'id',
            'foundation_id',
            'school',
            'academic_year',
            'name',
            'grade_levels',
            'lines',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'foundation_id', 'created_at', 'updated_at']


class StudentFeeAssignmentSerializer(serializers.ModelSerializer):
    amount = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)

    class Meta:
        model = StudentFeeAssignment
        fields = [
            'id',
            'foundation_id',
            'student',
            'fee_type',
            'amount',
            'currency',
            'start_period',
            'end_period',
            'source',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'foundation_id', 'created_at', 'updated_at']


class DiscountSerializer(serializers.ModelSerializer):
    value = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)

    class Meta:
        model = Discount
        fields = [
            'id',
            'foundation_id',
            'student',
            'fee_type',
            'type',
            'value',
            'reason',
            'valid_from',
            'valid_to',
            'status',
            'approved_by',
            'approved_at',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'foundation_id', 'status', 'approved_by', 'approved_at', 'created_at', 'updated_at']


class SiblingDiscountPolicySerializer(serializers.ModelSerializer):
    discount_percent = serializers.DecimalField(max_digits=5, decimal_places=2, coerce_to_string=True)

    class Meta:
        model = SiblingDiscountPolicy
        fields = [
            'id',
            'foundation_id',
            'school',
            'child_order',
            'discount_percent',
            'fee_category',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'foundation_id', 'created_at', 'updated_at']
