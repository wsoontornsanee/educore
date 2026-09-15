from rest_framework import serializers

from apps.reporting.models import RptWalletActivity


class RptWalletActivitySerializer(serializers.ModelSerializer):
    class Meta:
        model = RptWalletActivity
        fields = [
            'id', 'school', 'date', 'currency', 'topups', 'purchases',
            'commission', 'active_wallets', 'computed_at',
        ]
        read_only_fields = fields
