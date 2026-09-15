from rest_framework import serializers

from apps.wallet.models import SpendRule, Wallet, WalletTransaction


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
