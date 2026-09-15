from decimal import Decimal
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.fields import MoneyField
from apps.core.models import TenantModel
from apps.identity.models import School


class RptWalletActivity(TenantModel):
    """Daily wallet activity rollup per school (spec/15 §2).

    Rebuilt by `refresh_reporting` (never written to directly by transactional code) —
    reports MUST read from here, never from WalletTransaction/POSTransaction directly
    (spec/15 §2: "All report queries MUST hit the rpt_* tables").
    """
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name='wallet_activity_reports')
    date = models.DateField()
    currency = models.CharField(max_length=3, default='IDR')
    topups = MoneyField(default=Decimal('0.00'))
    purchases = MoneyField(default=Decimal('0.00'))
    commission = MoneyField(default=Decimal('0.00'))
    active_wallets = models.PositiveIntegerField(default=0)
    computed_at = models.DateTimeField(help_text=_("RPT-005: data freshness timestamp"))

    class Meta:
        db_table = 'rpt_wallet_activity'
        indexes = [
            models.Index(fields=['foundation_id', 'school_id', 'date']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'school', 'date', 'currency'],
                condition=models.Q(deleted_at__isnull=True),
                name='unique_rpt_wallet_activity_per_school_day',
            ),
        ]

    def __str__(self):
        return f"{self.school.name} - {self.date} ({self.currency})"
