from rest_framework import serializers

from apps.wallet.models import (
    POSPaymentPoint,
    QRDispute,
    Merchant,
    MerchantSettlement,
    WalletAutoTopupConfig,
    POSTerminal,
    POSTransaction,
    Product,
    SpendRule,
    Wallet,
    WalletTransaction,
)


class WalletSerializer(serializers.ModelSerializer):
    balance = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)
    daily_limit = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True, allow_null=True)

    class Meta:
        model = Wallet
        fields = ['id', 'foundation_id', 'student', 'balance', 'currency', 'status', 'daily_limit', 'created_at', 'updated_at']
        read_only_fields = ['id', 'foundation_id', 'balance', 'currency', 'status', 'created_at', 'updated_at']


class WalletTransactionSerializer(serializers.ModelSerializer):
    amount = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)
    balance_after = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)

    class Meta:
        model = WalletTransaction
        fields = [
            'id', 'foundation_id', 'wallet', 'type', 'amount', 'balance_after', 'reference', 'occurred_at', 'status',
            'entry_mode',
        ]
        read_only_fields = fields

    entry_mode = serializers.SerializerMethodField()

    def get_entry_mode(self, obj):
        # QRS-003: annotated by the history view (one Exists subquery, no per-row lookup).
        return 'SELF_ENTERED' if getattr(obj, 'self_entered', False) else 'OPERATOR'


class TopupSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=18, decimal_places=2)
    method = serializers.ChoiceField(choices=['CASH', 'MANUAL'])
    idempotency_key = serializers.CharField(max_length=128)
    reference = serializers.CharField(required=False, allow_blank=True, default='')


class TopupIntentCreateSerializer(serializers.Serializer):
    method = serializers.ChoiceField(choices=['VA', 'QRIS'])
    amount = serializers.DecimalField(max_digits=18, decimal_places=2)
    bank = serializers.CharField(required=False, allow_blank=True, default='')
    provider = serializers.CharField(required=False, allow_blank=True, default='MOCK')


class TopupIntentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    method = serializers.CharField()
    provider = serializers.CharField()
    amount = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)
    currency = serializers.CharField()
    va_bank = serializers.CharField()
    va_number = serializers.CharField()
    qris_payload = serializers.CharField()
    external_id = serializers.CharField()
    status = serializers.CharField()
    expires_at = serializers.DateTimeField()


class WalletAutoTopupConfigUpdateSerializer(serializers.Serializer):
    is_active = serializers.BooleanField()
    threshold_amount = serializers.DecimalField(max_digits=18, decimal_places=2)
    topup_amount = serializers.DecimalField(max_digits=18, decimal_places=2)
    method = serializers.ChoiceField(choices=['VA', 'QRIS'], required=False, default='VA')
    bank = serializers.CharField(required=False, allow_blank=True, default='')


class WalletAutoTopupConfigSerializer(serializers.ModelSerializer):
    threshold_amount = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)
    topup_amount = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)

    class Meta:
        model = WalletAutoTopupConfig
        fields = ['id', 'wallet', 'is_active', 'threshold_amount', 'topup_amount', 'method', 'bank', 'created_at', 'updated_at']
        read_only_fields = ['id', 'wallet', 'created_at', 'updated_at']


class SpendRuleSerializer(serializers.ModelSerializer):
    daily_limit = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True, allow_null=True, required=False)

    class Meta:
        model = SpendRule
        fields = [
            'id', 'foundation_id', 'student', 'daily_limit', 'blocked_categories',
            'blocked_products', 'allowed_window_start', 'allowed_window_end', 'qr_charge_enabled',
            'qr_charge_available', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'foundation_id', 'student', 'qr_charge_available', 'created_at', 'updated_at']

    qr_charge_available = serializers.SerializerMethodField()

    def get_qr_charge_available(self, obj):
        # QRS-017: the guardian app explains why QR is off while itemised blocks are set.
        return bool(obj.qr_charge_enabled and not obj.blocked_categories and not obj.blocked_products)


class MerchantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Merchant
        fields = [
            'id', 'foundation_id', 'school', 'name', 'type', 'settlement_account', 'commission_bps', 'is_active',
            'qr_self_amount_enabled', 'qr_self_amount_ack_at', 'static_qr_enabled', 'static_qr_ack_at',
            'static_qr_max', 'qr_dispute_flagged_at', 'created_at', 'updated_at',
        ]
        # QR Charge and static QR are switched only through POST /merchants/:id/{qr-charge,static-qr}/,
        # which record the acknowledgement.
        read_only_fields = [
            'id', 'foundation_id', 'qr_self_amount_enabled', 'qr_self_amount_ack_at', 'static_qr_enabled',
            'static_qr_ack_at', 'qr_dispute_flagged_at', 'created_at', 'updated_at',
        ]

    def validate_static_qr_max(self, value):
        if value <= 0:
            raise serializers.ValidationError("Must be greater than zero.")
        return value


class ProductSerializer(serializers.ModelSerializer):
    price = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)

    class Meta:
        model = Product
        fields = ['id', 'foundation_id', 'merchant', 'sku', 'name', 'price', 'category', 'nutrition', 'allergens', 'is_active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'foundation_id', 'created_at', 'updated_at']


class POSTerminalSerializer(serializers.ModelSerializer):
    class Meta:
        model = POSTerminal
        fields = ['id', 'foundation_id', 'merchant', 'device_id', 'name', 'status', 'created_at', 'updated_at']
        read_only_fields = ['id', 'foundation_id', 'created_at', 'updated_at']


class POSTransactionSerializer(serializers.ModelSerializer):
    subtotal = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)
    commission = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)
    total = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)

    class Meta:
        model = POSTransaction
        fields = [
            'id', 'foundation_id', 'merchant', 'terminal', 'student', 'items', 'subtotal', 'commission',
            'total', 'occurred_at', 'status', 'offline_created', 'client_transaction_id',
            'voided_at', 'void_reason', 'created_at', 'entry_mode', 'confirmation_code',
        ]
        read_only_fields = fields


class POSTransactionCreateSerializer(serializers.Serializer):
    terminal_id = serializers.IntegerField()
    student_id = serializers.IntegerField()
    items = serializers.ListField(child=serializers.DictField())
    client_transaction_id = serializers.CharField(max_length=128)
    occurred_at = serializers.DateTimeField(required=False)


class POSTransactionVoidSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')


class MerchantSettlementSerializer(serializers.ModelSerializer):
    gross = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)
    commission = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)
    net = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)

    class Meta:
        model = MerchantSettlement
        fields = [
            'id', 'foundation_id', 'merchant', 'period_start', 'period_end', 'gross',
            'commission', 'net', 'status', 'paid_at', 'statement_pdf_key', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class MerchantSettlementRunSerializer(serializers.Serializer):
    period_start = serializers.DateField()
    period_end = serializers.DateField()


class POSSyncQuerySerializer(serializers.Serializer):
    terminal_id = serializers.IntegerField()
    cursor = serializers.DateTimeField(required=False, allow_null=True)


class POSSessionSerializer(serializers.Serializer):
    terminal_id = serializers.IntegerField()


class OfflinePOSTransactionSerializer(serializers.Serializer):
    client_transaction_id = serializers.CharField(max_length=128)
    student_id = serializers.IntegerField()
    items = serializers.ListField(child=serializers.DictField())
    occurred_at = serializers.DateTimeField(required=False)


class POSBatchCreateSerializer(serializers.Serializer):
    terminal_id = serializers.IntegerField()
    transactions = OfflinePOSTransactionSerializer(many=True)


class ReconciliationCashSettleSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=18, decimal_places=2)
    reference = serializers.CharField(required=False, allow_blank=True, default='')


class ReconciliationWriteOffSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)


class RefundMarkPaidSerializer(serializers.Serializer):
    bank_name = serializers.CharField(max_length=64, required=False, allow_blank=True, default='')
    account_number = serializers.CharField(max_length=64, required=False, allow_blank=True, default='')
    account_holder_name = serializers.CharField(max_length=128, required=False, allow_blank=True, default='')
    reference = serializers.CharField(required=False, allow_blank=True, default='')


class RefundMarkDonatedSerializer(serializers.Serializer):
    donation_consent = serializers.BooleanField()


class StudentNutritionSummaryItemSerializer(serializers.Serializer):
    sku = serializers.CharField()
    name = serializers.CharField()
    qty = serializers.IntegerField()
    unit_price = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)
    calories = serializers.IntegerField()
    sugar_g = serializers.DecimalField(max_digits=10, decimal_places=2, coerce_to_string=True)
    allergens = serializers.ListField(child=serializers.CharField())
    is_healthy = serializers.BooleanField()
    occurred_at = serializers.DateTimeField()


class DailyNutritionSummarySerializer(serializers.Serializer):
    date = serializers.DateField()
    total_calories = serializers.IntegerField()
    total_sugar_g = serializers.DecimalField(max_digits=10, decimal_places=2, coerce_to_string=True)
    items_count = serializers.IntegerField()
    healthy_count = serializers.IntegerField()


class StudentNutritionSummarySerializer(serializers.Serializer):
    student_id = serializers.IntegerField()
    from_date = serializers.DateField()
    to_date = serializers.DateField()
    total_calories = serializers.IntegerField()
    total_sugar_g = serializers.DecimalField(max_digits=10, decimal_places=2, coerce_to_string=True)
    total_items = serializers.IntegerField()
    healthy_items_count = serializers.IntegerField()
    allergens = serializers.ListField(child=serializers.CharField())
    daily_breakdown = DailyNutritionSummarySerializer(many=True)
    items = StudentNutritionSummaryItemSerializer(many=True)


class QRSessionCreateSerializer(serializers.Serializer):
    terminal_id = serializers.IntegerField()


class QRStudentTokenSerializer(serializers.Serializer):
    student_id = serializers.IntegerField()
    token = serializers.CharField(max_length=512)


class QRChargeSerializer(QRStudentTokenSerializer):
    amount = serializers.DecimalField(max_digits=18, decimal_places=2)
    idempotency_key = serializers.CharField(max_length=100)


class MerchantQRChargeSerializer(serializers.Serializer):
    enabled = serializers.BooleanField()
    acknowledged = serializers.BooleanField(required=False, default=False)


class QRDisputeOpenSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500)


class QRDisputeResolveSerializer(serializers.Serializer):
    outcome = serializers.ChoiceField(choices=['UPHELD', 'REJECTED'])
    note = serializers.CharField(max_length=500, required=False, allow_blank=True, default='')
    refund_amount = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, allow_null=True, default=None)


class QRDisputeSerializer(serializers.ModelSerializer):
    refund_amount = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True, allow_null=True)

    class Meta:
        model = QRDispute
        fields = [
            'id', 'pos_transaction', 'merchant', 'student', 'reason', 'status', 'resolved_at',
            'resolution_note', 'refund_amount', 'created_at',
        ]
        read_only_fields = fields


class PaymentPointCreateSerializer(serializers.Serializer):
    merchant_id = serializers.IntegerField()
    name = serializers.CharField(max_length=128)
    location = serializers.CharField(max_length=128, required=False, allow_blank=True, default='')


class DecalPrintSerializer(serializers.Serializer):
    expires_on = serializers.DateField(required=False, allow_null=True, default=None)


class DecalRevokeSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')


class PaymentPointSerializer(serializers.ModelSerializer):
    decals = serializers.SerializerMethodField()

    class Meta:
        model = POSPaymentPoint
        fields = ['id', 'merchant', 'name', 'location', 'status', 'decals']
        read_only_fields = fields

    def get_decals(self, obj):
        return [
            {
                'id': d.id, 'human_id': d.human_id, 'status': d.status, 'printed_at': d.printed_at,
                'expires_on': d.expires_on, 'grace_until': d.grace_until,
            }
            for d in obj.decals.filter(deleted_at__isnull=True).order_by('-printed_at')
        ]
