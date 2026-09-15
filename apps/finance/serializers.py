from rest_framework import serializers
from apps.finance.models import (
    Discount,
    FeePlan,
    FeeType,
    Invoice,
    InvoiceLine,
    LedgerEntry,
    LedgerJournal,
    Payment,
    PaymentAllocation,
    PaymentIntent,
    SiblingDiscountPolicy,
    StudentCreditBalance,
    StudentFeeAssignment,
    StudentVirtualAccount,
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


class InvoiceLineSerializer(serializers.ModelSerializer):
    amount = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)
    discount = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)
    subtotal = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)

    class Meta:
        model = InvoiceLine
        fields = [
            'id',
            'fee_type',
            'code',
            'description',
            'amount',
            'discount',
            'subtotal',
            'currency',
        ]


class InvoiceSerializer(serializers.ModelSerializer):
    subtotal = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)
    discount = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)
    rounding = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)
    total = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)
    paid = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)
    balance_due = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True, read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)
    lines = InvoiceLineSerializer(many=True, read_only=True)

    class Meta:
        model = Invoice
        fields = [
            'id',
            'foundation_id',
            'school',
            'student',
            'number',
            'period',
            'issue_date',
            'due_date',
            'subtotal',
            'discount',
            'rounding',
            'total',
            'paid',
            'balance_due',
            'currency',
            'status',
            'is_overdue',
            'lines',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id',
            'foundation_id',
            'number',
            'paid',
            'balance_due',
            'is_overdue',
            'lines',
            'created_at',
            'updated_at',
        ]


class StudentVirtualAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = StudentVirtualAccount
        fields = [
            'id',
            'student',
            'bank',
            'va_number',
            'is_active',
            'created_at',
        ]
        read_only_fields = ['id', 'created_at']


class PaymentIntentSerializer(serializers.ModelSerializer):
    amount = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)

    class Meta:
        model = PaymentIntent
        fields = [
            'id',
            'school',
            'student',
            'invoice',
            'method',
            'provider',
            'va_bank',
            'va_number',
            'qris_payload',
            'amount',
            'currency',
            'expires_at',
            'status',
            'created_at',
        ]
        read_only_fields = ['id', 'created_at', 'status', 'va_number', 'qris_payload']


class PaymentIntentCreateSerializer(serializers.Serializer):
    invoice_ids = serializers.ListField(child=serializers.IntegerField(), allow_empty=False)
    method = serializers.ChoiceField(choices=['VA', 'QRIS'])
    bank = serializers.CharField(required=False, allow_blank=True, default='BCA')
    provider = serializers.CharField(required=False, default='MOCK')


class PaymentAllocationSerializer(serializers.ModelSerializer):
    amount = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)

    class Meta:
        model = PaymentAllocation
        fields = [
            'id',
            'payment',
            'invoice',
            'invoice_line',
            'amount',
            'currency',
        ]
        read_only_fields = ['id']


class PaymentSerializer(serializers.ModelSerializer):
    amount = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)
    fee = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)
    net = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)
    allocations = PaymentAllocationSerializer(many=True, read_only=True)

    class Meta:
        model = Payment
        fields = [
            'id',
            'foundation_id',
            'school',
            'student',
            'amount',
            'currency',
            'method',
            'channel',
            'reference',
            'external_id',
            'paid_at',
            'settled_at',
            'status',
            'fee',
            'net',
            'receipt_number',
            'received_by',
            'proof_file',
            'allocations',
            'created_at',
        ]
        read_only_fields = [
            'id',
            'foundation_id',
            'reference',
            'receipt_number',
            'paid_at',
            'settled_at',
            'status',
            'fee',
            'net',
            'allocations',
            'created_at',
        ]


class CashPaymentCreateSerializer(serializers.Serializer):
    student_id = serializers.IntegerField()
    amount = serializers.DecimalField(max_digits=18, decimal_places=2)
    invoice_ids = serializers.ListField(child=serializers.IntegerField(), required=False, default=list)
    notes = serializers.CharField(required=False, allow_blank=True, default='')


class ManualPaymentCreateSerializer(serializers.Serializer):
    student_id = serializers.IntegerField()
    amount = serializers.DecimalField(max_digits=18, decimal_places=2)
    invoice_ids = serializers.ListField(child=serializers.IntegerField(), required=False, default=list)
    proof_file = serializers.CharField(required=False, allow_blank=True, default='')
    channel = serializers.CharField(required=False, default='MANUAL_TRANSFER')
    notes = serializers.CharField(required=False, allow_blank=True, default='')


class ManualPaymentVerifySerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=['APPROVE', 'REJECT'])
    reason = serializers.CharField(required=False, allow_blank=True, default='')


class LedgerEntrySerializer(serializers.ModelSerializer):
    debit = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)
    credit = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)

    class Meta:
        model = LedgerEntry
        fields = [
            'id',
            'account_code',
            'account_name',
            'debit',
            'credit',
            'currency',
            'ref_type',
            'ref_id',
            'occurred_at',
        ]


class LedgerJournalSerializer(serializers.ModelSerializer):
    entries = LedgerEntrySerializer(many=True, read_only=True)

    class Meta:
        model = LedgerJournal
        fields = [
            'id',
            'school',
            'number',
            'description',
            'ref_type',
            'ref_id',
            'currency',
            'occurred_at',
            'entries',
            'created_at',
        ]

