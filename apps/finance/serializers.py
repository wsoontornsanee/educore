from decimal import Decimal
from rest_framework import serializers
from apps.finance.models import (
    BankSftpConfig,
    BankStatementFileFormat,
    ConvenienceFeeAllocation,
    ConvenienceFeeType,
    Discount,
    FeePlan,
    FeeType,
    Invoice,
    InvoiceInstallment,
    InvoiceLine,
    InvoiceWriteOffRequest,
    LedgerEntry,
    LedgerJournal,
    Payment,
    PaymentAllocation,
    PaymentIntent,
    SchoolArrearsPolicy,
    SchoolConvenienceFeePolicy,
    SchoolQrisConfig,
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


class InvoiceInstallmentSerializer(serializers.ModelSerializer):
    amount = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)
    paid_amount = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)
    balance_due = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True, read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)

    class Meta:
        model = InvoiceInstallment
        fields = [
            'id',
            'installment_no',
            'due_date',
            'amount',
            'paid_amount',
            'balance_due',
            'currency',
            'status',
            'paid_at',
            'is_overdue',
            'notes',
        ]
        read_only_fields = ['id', 'installment_no', 'paid_amount', 'balance_due', 'status', 'paid_at', 'is_overdue']


class InstallmentScheduleItemSerializer(serializers.Serializer):
    due_date = serializers.DateField()
    amount = serializers.DecimalField(max_digits=18, decimal_places=2)


class CreateInstallmentPlanSerializer(serializers.Serializer):
    count = serializers.IntegerField(required=False, min_value=1)
    first_due_date = serializers.DateField(required=False)
    interval_days = serializers.IntegerField(required=False, default=30, min_value=1)
    schedule = InstallmentScheduleItemSerializer(many=True, required=False)
    notes = serializers.CharField(required=False, allow_blank=True, default='')

    def validate(self, data):
        if not data.get('count') and not data.get('schedule'):
            raise serializers.ValidationError("Harap tentukan jumlah cicilan (count) atau jadwal (schedule).")
        return data


class InvoiceSerializer(serializers.ModelSerializer):
    subtotal = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)
    discount = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)
    rounding = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)
    total = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)
    paid = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)
    balance_due = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True, read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)
    lines = InvoiceLineSerializer(many=True, read_only=True)
    installments = InvoiceInstallmentSerializer(many=True, read_only=True)

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
            'installments',
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
            'installments',
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
    base_amount = serializers.SerializerMethodField()
    convenience_fee_amount = serializers.SerializerMethodField()

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
            'base_amount',
            'convenience_fee_amount',
            'currency',
            'expires_at',
            'status',
            'created_at',
        ]

    def get_base_amount(self, obj):
        return obj.metadata.get('base_amount', str(obj.amount))

    def get_convenience_fee_amount(self, obj):
        return obj.metadata.get('convenience_fee_amount', '0.00')
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


class SchoolQrisConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = SchoolQrisConfig
        fields = ['id', 'school', 'qris_image_key', 'qris_payload', 'is_active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'school', 'qris_image_key', 'created_at', 'updated_at']


class SchoolQrisConfigUpdateSerializer(serializers.Serializer):
    qris_image = serializers.FileField(required=False)
    qris_payload = serializers.CharField(required=False, allow_blank=True, default='')
    is_active = serializers.BooleanField(required=False, default=True)


class SchoolConvenienceFeePolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = SchoolConvenienceFeePolicy
        fields = ['id', 'school', 'allocation', 'fee_type', 'fee_value', 'is_active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'school', 'created_at', 'updated_at']


class SchoolConvenienceFeePolicyUpdateSerializer(serializers.Serializer):
    allocation = serializers.ChoiceField(choices=ConvenienceFeeAllocation.choices, default=ConvenienceFeeAllocation.PASSED_TO_PARENT)
    fee_type = serializers.ChoiceField(choices=ConvenienceFeeType.choices, default=ConvenienceFeeType.FIXED)
    fee_value = serializers.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'))
    is_active = serializers.BooleanField(required=False, default=True)


class BankSftpConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = BankSftpConfig
        fields = ['id', 'school', 'bank_code', 'host', 'port', 'username', 'remote_directory', 'file_format', 'is_active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'school', 'created_at', 'updated_at']


class BankSftpConfigUpdateSerializer(serializers.Serializer):
    bank_code = serializers.CharField(max_length=32)
    host = serializers.CharField(max_length=255)
    port = serializers.IntegerField(required=False, default=22)
    username = serializers.CharField(max_length=128, required=False, allow_blank=True, default='')
    remote_directory = serializers.CharField(max_length=512, required=False, default='/')
    file_format = serializers.ChoiceField(choices=BankStatementFileFormat.choices, default=BankStatementFileFormat.MT940)
    is_active = serializers.BooleanField(required=False, default=True)


class PaymentProofUploadSerializer(serializers.Serializer):
    file = serializers.FileField()


class SchoolArrearsPolicySerializer(serializers.ModelSerializer):
    effective_ladder_days = serializers.SerializerMethodField()

    class Meta:
        model = SchoolArrearsPolicy
        fields = [
            'id',
            'school',
            'ladder_days',
            'effective_ladder_days',
            'is_active',
            'payment_deep_link_base',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'school', 'effective_ladder_days', 'created_at', 'updated_at']

    def get_effective_ladder_days(self, obj):
        return obj.get_effective_ladder_days()


class SchoolArrearsPolicyUpdateSerializer(serializers.Serializer):
    ladder_days = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        allow_empty=True,
    )
    is_active = serializers.BooleanField(required=False)
    payment_deep_link_base = serializers.CharField(required=False, max_length=255, allow_blank=False)

    def validate_ladder_days(self, value):
        if value is not None:
            return sorted(value)
        return value


class InvoiceWriteOffRequestSerializer(serializers.ModelSerializer):
    amount = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)
    invoice_number = serializers.CharField(source='invoice.number', read_only=True)
    school_name = serializers.CharField(source='school.name', read_only=True)
    requested_by_name = serializers.CharField(source='requested_by.full_name', read_only=True, default='')
    approved_by_name = serializers.CharField(source='approved_by.full_name', read_only=True, default='')

    class Meta:
        model = InvoiceWriteOffRequest
        fields = [
            'id',
            'foundation_id',
            'school',
            'school_name',
            'invoice',
            'invoice_number',
            'amount',
            'reason',
            'status',
            'requested_by',
            'requested_by_name',
            'approved_by',
            'approved_by_name',
            'resolved_at',
            'rejection_reason',
            'journal',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id',
            'foundation_id',
            'school_name',
            'invoice_number',
            'status',
            'requested_by',
            'requested_by_name',
            'approved_by',
            'approved_by_name',
            'resolved_at',
            'rejection_reason',
            'journal',
            'created_at',
            'updated_at',
        ]


class InvoiceWriteOffRequestCreateSerializer(serializers.Serializer):
    invoice_id = serializers.IntegerField()
    reason = serializers.CharField(max_length=500)
    amount = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, min_value=Decimal('0.01'))


class InvoiceWriteOffResolveSerializer(serializers.Serializer):
    notes = serializers.CharField(required=False, allow_blank=True, default='')

