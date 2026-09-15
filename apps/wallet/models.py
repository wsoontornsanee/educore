from decimal import Decimal
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.fields import MoneyField
from apps.core.models import TenantModel
from apps.identity.models import School, Student


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


class MerchantType(models.TextChoices):
    CANTEEN = 'CANTEEN', _('Kantin')
    UNIFORM = 'UNIFORM', _('Seragam')
    STATIONERY = 'STATIONERY', _('Alat Tulis')
    OTHER = 'OTHER', _('Lainnya')


class Merchant(TenantModel):
    """A campus vendor authorized to sell against student wallets (spec/07 §6)."""
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name='merchants')
    name = models.CharField(max_length=128)
    type = models.CharField(max_length=16, choices=MerchantType.choices, default=MerchantType.CANTEEN)
    settlement_account = models.CharField(max_length=64, blank=True, default='')
    commission_bps = models.PositiveIntegerField(default=0, help_text=_("Commission in basis points (100 = 1%)"))
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'merchants'
        indexes = [
            models.Index(fields=['foundation_id', 'school_id', 'is_active']),
        ]

    def __str__(self):
        return f"{self.name} ({self.type})"


class Product(TenantModel):
    """A merchant's sellable item (spec/07 §6)."""
    merchant = models.ForeignKey(Merchant, on_delete=models.PROTECT, related_name='products')
    sku = models.CharField(max_length=64, db_index=True)
    name = models.CharField(max_length=128)
    price = MoneyField()
    category = models.CharField(max_length=64, blank=True, default='')
    nutrition = models.JSONField(default=dict, blank=True, help_text=_("e.g. {calories, sugar_g}"))
    allergens = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'products'
        indexes = [
            models.Index(fields=['foundation_id', 'merchant_id', 'is_active']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'merchant', 'sku'],
                condition=models.Q(deleted_at__isnull=True),
                name='unique_product_sku_per_merchant',
            ),
        ]

    def __str__(self):
        return f"{self.sku} - {self.name}"


class POSTerminalStatus(models.TextChoices):
    ACTIVE = 'ACTIVE', _('Aktif')
    INACTIVE = 'INACTIVE', _('Nonaktif')


class POSTerminal(TenantModel):
    """A physical POS device assigned to a merchant (spec/07 §5)."""
    merchant = models.ForeignKey(Merchant, on_delete=models.PROTECT, related_name='terminals')
    device_id = models.CharField(max_length=128, unique=True, db_index=True)
    name = models.CharField(max_length=128, blank=True, default='')
    status = models.CharField(max_length=16, choices=POSTerminalStatus.choices, default=POSTerminalStatus.ACTIVE)

    class Meta:
        db_table = 'pos_terminals'

    def __str__(self):
        return f"{self.name or self.device_id} ({self.merchant.name})"


class POSTransactionStatus(models.TextChoices):
    COMPLETED = 'COMPLETED', _('Selesai')
    REJECTED = 'REJECTED', _('Ditolak')
    VOIDED = 'VOIDED', _('Dibatalkan')


class POSTransaction(TenantModel):
    """A sale at a POS terminal (spec/07 §5, §6)."""
    merchant = models.ForeignKey(Merchant, on_delete=models.PROTECT, related_name='pos_transactions')
    terminal = models.ForeignKey(POSTerminal, on_delete=models.PROTECT, related_name='pos_transactions')
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name='pos_transactions')
    items = models.JSONField(default=list, help_text=_("List of {sku, name, qty, unit_price}"))
    subtotal = MoneyField()
    commission = MoneyField(default=Decimal('0.00'))
    total = MoneyField()
    occurred_at = models.DateTimeField()
    status = models.CharField(max_length=16, choices=POSTransactionStatus.choices, default=POSTransactionStatus.COMPLETED)
    offline_created = models.BooleanField(default=False)
    client_transaction_id = models.CharField(max_length=128, db_index=True)
    wallet_transaction = models.ForeignKey(
        WalletTransaction, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        db_table = 'pos_transactions'
        indexes = [
            models.Index(fields=['foundation_id', 'merchant_id', 'occurred_at']),
            models.Index(fields=['foundation_id', 'student_id', 'occurred_at']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'terminal', 'client_transaction_id'],
                name='unique_pos_transaction_client_id_per_terminal',
            ),
        ]

    def __str__(self):
        return f"{self.student.nis} @ {self.merchant.name}: {self.total} ({self.status})"
