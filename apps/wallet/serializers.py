from rest_framework import serializers

from apps.wallet.models import (
    Merchant,
    MerchantSettlement,
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
        fields = ['id', 'foundation_id', 'wallet', 'type', 'amount', 'balance_after', 'reference', 'occurred_at', 'status']
        read_only_fields = fields


class TopupSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=18, decimal_places=2)
    method = serializers.ChoiceField(choices=['CASH', 'MANUAL'])
    idempotency_key = serializers.CharField(max_length=128)
    reference = serializers.CharField(required=False, allow_blank=True, default='')


class SpendRuleSerializer(serializers.ModelSerializer):
    daily_limit = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True, allow_null=True, required=False)

    class Meta:
        model = SpendRule
        fields = [
            'id', 'foundation_id', 'student', 'daily_limit', 'blocked_categories',
            'blocked_products', 'allowed_window_start', 'allowed_window_end', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'foundation_id', 'student', 'created_at', 'updated_at']


class MerchantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Merchant
        fields = ['id', 'foundation_id', 'school', 'name', 'type', 'settlement_account', 'commission_bps', 'is_active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'foundation_id', 'created_at', 'updated_at']


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
            'voided_at', 'void_reason', 'created_at',
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
