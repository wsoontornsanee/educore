from decimal import Decimal
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.fields import MoneyField
from apps.core.models import TenantModel
from apps.identity.models import Student


class WalletStatus(models.TextChoices):
    ACTIVE = 'ACTIVE', _('Aktif')
    FROZEN = 'FROZEN', _('Dibekukan')
    CLOSED = 'CLOSED', _('Ditutup')


class Wallet(TenantModel):
    """A student's stored-value campus wallet (spec/07 §2, §3)."""
    student = models.OneToOneField(Student, on_delete=models.PROTECT, related_name='wallet')
    balance = MoneyField(default=Decimal('0.00'))
    currency = models.CharField(max_length=3, default='IDR')
    status = models.CharField(max_length=16, choices=WalletStatus.choices, default=WalletStatus.ACTIVE)
    daily_limit = MoneyField(null=True, blank=True, help_text=_("Guardian-configured daily spend limit (WAL-009)"))

    class Meta:
        db_table = 'wallets'
        indexes = [
            models.Index(fields=['foundation_id', 'status']),
        ]

    def __str__(self):
        return f"{self.student.nis} - {self.currency} {self.balance} ({self.status})"


class WalletTransactionType(models.TextChoices):
    TOPUP = 'TOPUP', _('Isi Ulang')
    PURCHASE = 'PURCHASE', _('Pembelian')
    REFUND = 'REFUND', _('Pengembalian')
    ADJUSTMENT = 'ADJUSTMENT', _('Penyesuaian')
    TRANSFER_OUT = 'TRANSFER_OUT', _('Transfer Keluar')


class WalletTransactionStatus(models.TextChoices):
    COMPLETED = 'COMPLETED', _('Selesai')
    REJECTED = 'REJECTED', _('Ditolak')


class WalletTopupMethod(models.TextChoices):
    CASH = 'CASH', _('Tunai di Kantor Sekolah')
    MANUAL = 'MANUAL', _('Penyesuaian Manual')


class WalletTransaction(TenantModel):
    """An immutable wallet ledger entry (spec/07 §2, WAL-001, WAL-003)."""
    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name='transactions')
    type = models.CharField(max_length=16, choices=WalletTransactionType.choices)
    amount = MoneyField(help_text=_("Signed delta: positive for TOPUP/REFUND, negative for PURCHASE/TRANSFER_OUT"))
    balance_after = MoneyField()
    reference = models.CharField(max_length=128, blank=True, default='')
    occurred_at = models.DateTimeField()
    status = models.CharField(max_length=16, choices=WalletTransactionStatus.choices, default=WalletTransactionStatus.COMPLETED)
    idempotency_key = models.CharField(max_length=128, db_index=True)

    class Meta:
        db_table = 'wallet_transactions'
        indexes = [
            models.Index(fields=['foundation_id', 'wallet_id', 'occurred_at']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'wallet', 'idempotency_key'],
                name='unique_wallet_transaction_idempotency_key',
            ),
        ]

    def __str__(self):
        return f"{self.wallet.student.nis} - {self.type} {self.amount} @ {self.occurred_at}"


class SpendRule(TenantModel):
    """Guardian-configured spending controls for a student's wallet (spec/07 §4)."""
    student = models.OneToOneField(Student, on_delete=models.PROTECT, related_name='spend_rule')
    daily_limit = MoneyField(null=True, blank=True)
    blocked_categories = models.JSONField(default=list, blank=True)
    blocked_products = models.JSONField(default=list, blank=True, help_text=_("List of product SKUs"))
    allowed_window_start = models.TimeField(null=True, blank=True)
    allowed_window_end = models.TimeField(null=True, blank=True)

    class Meta:
        db_table = 'spend_rules'

    def __str__(self):
        return f"SpendRule for {self.student.nis}"
