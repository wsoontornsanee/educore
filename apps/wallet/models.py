from decimal import Decimal
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.fields import MoneyField
from apps.core.fields import soft_delete_uniqueness_marker
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
    requires_reconciliation = models.BooleanField(
        default=False, help_text=_("Set when an offline overspend was accepted and took the wallet negative (WAL-017)")
    )

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
    RECONCILE_REQUIRED = 'RECONCILE_REQUIRED', _('Perlu Rekonsiliasi')


class WalletTopupMethod(models.TextChoices):
    CASH = 'CASH', _('Tunai di Kantor Sekolah')
    MANUAL = 'MANUAL', _('Penyesuaian Manual')
    VA = 'VA', _('Virtual Account')
    QRIS = 'QRIS', _('QRIS')


class WalletTransaction(TenantModel):
    """An immutable wallet ledger entry (spec/07 §2, WAL-001, WAL-003)."""
    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name='transactions')
    type = models.CharField(max_length=16, choices=WalletTransactionType.choices)
    amount = MoneyField(help_text=_("Signed delta: positive for TOPUP/REFUND, negative for PURCHASE/TRANSFER_OUT"))
    balance_after = MoneyField()
    reference = models.CharField(max_length=128, blank=True, default='')
    occurred_at = models.DateTimeField()
    status = models.CharField(max_length=24, choices=WalletTransactionStatus.choices, default=WalletTransactionStatus.COMPLETED)
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
    qr_charge_enabled = models.BooleanField(
        default=True, help_text=_("Guardian switch for student-entered QR Charge (QRS-002)"),
    )

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
    qr_self_amount_enabled = models.BooleanField(
        default=False, help_text=_("Student-entered QR Charge accepted at this merchant (QRS-001)"),
    )
    qr_self_amount_ack_by = models.ForeignKey(
        'identity.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
        help_text=_("Admin who acknowledged the operator-verifies-amount statement (QRS-001)"),
    )
    qr_self_amount_ack_at = models.DateTimeField(null=True, blank=True)
    static_qr_enabled = models.BooleanField(
        default=False, help_text=_("Printed static QR decals accepted at this merchant; separate from QR Charge itself (QRS-008)"),
    )
    static_qr_max = MoneyField(
        default=Decimal('25000.00'), help_text=_("Per-charge cap for static decals; the lower of this and the school cap applies"),
    )
    static_qr_ack_by = models.ForeignKey('identity.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    static_qr_ack_at = models.DateTimeField(null=True, blank=True)
    qr_dispute_flagged_at = models.DateTimeField(
        null=True, blank=True,
        help_text=_("Raised when 3+ QR disputes are upheld in 30 days; cleared on school-admin review (QRS-028)"),
    )

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
    active_uniq_marker = soft_delete_uniqueness_marker()

    class Meta:
        db_table = 'products'
        indexes = [
            models.Index(fields=['foundation_id', 'merchant_id', 'is_active']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'merchant', 'sku', 'active_uniq_marker'],
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


class POSEntryMode(models.TextChoices):
    OPERATOR = 'OPERATOR', _('Diinput petugas')
    SELF_ENTERED = 'SELF_ENTERED', _('Diinput siswa')


class POSTerminalSessionKeyStatus(models.TextChoices):
    ACTIVE = 'ACTIVE', _('Aktif')
    REVOKED = 'REVOKED', _('Dicabut')


class POSTerminalSessionKey(TenantModel):
    """A symmetric secret a terminal uses to sign offline-minted QR session
    tokens locally, with no server round-trip (QRS-022/023, spec 18 §6).

    Mirrors apps.partners.PartnerApiKey: the plaintext secret is returned
    exactly once, at issue/rotate time, and stored Fernet-encrypted at rest
    from then on (apps/wallet/crypto.py). `key_id` travels inside the token
    so the server knows which secret to verify against without guessing.

    Rotation: revoking a key does not immediately invalidate transactions
    already signed with it — a terminal can be offline for days, so a
    revoked key stays valid for VERIFICATION (never for new minting) until
    `grace_until`, giving in-flight offline sales a window to sync cleanly.
    """
    GRACE_PERIOD_DAYS = 7

    terminal = models.ForeignKey(POSTerminal, on_delete=models.CASCADE, related_name='session_keys')
    key_id = models.CharField(max_length=64, unique=True, db_index=True)
    secret_encrypted = models.TextField(help_text=_("Fernet-encrypted HMAC secret; never returned after issue time"))
    status = models.CharField(max_length=16, choices=POSTerminalSessionKeyStatus.choices, default=POSTerminalSessionKeyStatus.ACTIVE)
    revoked_at = models.DateTimeField(null=True, blank=True)
    grace_until = models.DateTimeField(null=True, blank=True, help_text=_("Revoked key still verifies until this time"))

    class Meta:
        db_table = 'pos_terminal_session_keys'
        indexes = [
            models.Index(fields=['foundation_id', 'terminal_id', 'status']),
        ]

    def __str__(self):
        return f"{self.key_id} ({self.terminal.device_id}) - {self.status}"

    @property
    def is_verifiable(self) -> bool:
        if self.status == POSTerminalSessionKeyStatus.ACTIVE:
            return True
        return bool(self.grace_until and timezone.now() <= self.grace_until)


class POSTransaction(TenantModel):
    """A sale at a POS terminal (spec/07 §5, §6)."""
    merchant = models.ForeignKey(Merchant, on_delete=models.PROTECT, related_name='pos_transactions')
    terminal = models.ForeignKey(
        POSTerminal, on_delete=models.PROTECT, null=True, blank=True, related_name='pos_transactions',
        help_text=_("Null for a static-decal charge, which has no terminal (QRS-040 keys it on qr_decal)"),
    )
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
    entry_mode = models.CharField(max_length=16, choices=POSEntryMode.choices, default=POSEntryMode.OPERATOR)
    qr_session = models.ForeignKey('POSQRSession', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    qr_decal = models.ForeignKey('POSQRDecal', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    confirmation_code = models.CharField(max_length=4, blank=True, default='')
    qr_offline_key = models.ForeignKey(
        POSTerminalSessionKey, on_delete=models.PROTECT, null=True, blank=True, related_name='+',
        help_text=_("Set when this sale was authenticated by an offline-minted QR session token"),
    )
    qr_offline_nonce = models.CharField(
        max_length=64, blank=True, default='',
        help_text=_("Nonce from the offline-minted QR token; unique per terminal to reject replays (WAL-015)"),
    )

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
            models.UniqueConstraint(
                fields=['foundation_id', 'terminal', 'qr_offline_nonce'],
                condition=~models.Q(qr_offline_nonce=''),
                name='unique_pos_transaction_qr_offline_nonce_per_terminal',
            ),
        ]

    def __str__(self):
        return f"{self.student.nis} @ {self.merchant.name}: {self.total} ({self.status})"


class POSQRSession(TenantModel):
    """One-time QR shown by a terminal for a student-entered charge (spec 18 §3, QRS-005/006).

    The token handed to the student is signed and encodes only this row's id and
    nonce — never an amount, student or balance. Single use is enforced by
    ``consumed_at`` under a row lock, not by the token alone.
    """
    merchant = models.ForeignKey(Merchant, on_delete=models.PROTECT, related_name='qr_sessions')
    terminal = models.ForeignKey(POSTerminal, on_delete=models.PROTECT, related_name='qr_sessions')
    nonce = models.CharField(max_length=32)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
    consumed_by_student = models.ForeignKey(Student, on_delete=models.PROTECT, null=True, blank=True, related_name='+')

    class Meta:
        db_table = 'pos_qr_sessions'
        indexes = [
            models.Index(fields=['foundation_id', 'terminal_id', 'expires_at']),
        ]

    def __str__(self):
        return f"QR session {self.pk} @ {self.terminal_id}"


class POSPaymentPointStatus(models.TextChoices):
    ACTIVE = 'ACTIVE', _('Aktif')
    CLOSED = 'CLOSED', _('Ditutup')


class POSPaymentPoint(TenantModel):
    """A named counter (drinks cart, photocopy desk) that takes static-decal QR payments (spec 18 §3b)."""
    merchant = models.ForeignKey(Merchant, on_delete=models.PROTECT, related_name='payment_points')
    name = models.CharField(max_length=128)
    location = models.CharField(max_length=128, blank=True, default='')
    status = models.CharField(max_length=8, choices=POSPaymentPointStatus.choices, default=POSPaymentPointStatus.ACTIVE)

    class Meta:
        db_table = 'pos_payment_points'
        indexes = [models.Index(fields=['foundation_id', 'merchant_id', 'status'])]

    def __str__(self):
        return f"{self.name} ({self.merchant.name})"


class POSQRDecalStatus(models.TextChoices):
    ACTIVE = 'ACTIVE', _('Aktif')
    SUPERSEDED = 'SUPERSEDED', _('Digantikan')
    REVOKED = 'REVOKED', _('Dicabut')


class POSQRDecal(TenantModel):
    """One printed static QR sheet, bound to exactly one payment point (QRS-031).

    The QR carries a signed token of this row's id and nonce — no amount, student or URL (QRS-034).
    """
    payment_point = models.ForeignKey(POSPaymentPoint, on_delete=models.PROTECT, related_name='decals')
    human_id = models.CharField(max_length=16, help_text=_("Short id printed on the sheet, e.g. KU-03"))
    nonce = models.CharField(max_length=32)
    status = models.CharField(max_length=12, choices=POSQRDecalStatus.choices, default=POSQRDecalStatus.ACTIVE)
    printed_by = models.ForeignKey('identity.User', on_delete=models.PROTECT, related_name='+')
    printed_at = models.DateTimeField()
    expires_on = models.DateField(null=True, blank=True)
    superseded_at = models.DateTimeField(null=True, blank=True)
    grace_until = models.DateTimeField(null=True, blank=True)
    revoke_reason = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        db_table = 'pos_qr_decals'
        indexes = [models.Index(fields=['foundation_id', 'payment_point_id', 'status'])]

    def __str__(self):
        return f"{self.human_id} ({self.status})"


class QRDisputeStatus(models.TextChoices):
    OPEN = 'OPEN', _('Terbuka')
    UPHELD = 'UPHELD', _('Dikabulkan')
    REJECTED = 'REJECTED', _('Ditolak')


class QRDispute(TenantModel):
    """A guardian's dispute of a self-entered QR charge (spec 18 §7, QRS-026).

    Opening one never reverses anything; an upheld outcome is a separate REFUND or
    ADJUSTMENT ledger line recorded in ``resolution_transaction``.
    """
    pos_transaction = models.ForeignKey(POSTransaction, on_delete=models.PROTECT, related_name='disputes')
    merchant = models.ForeignKey(Merchant, on_delete=models.PROTECT, related_name='qr_disputes')
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name='+')
    opened_by = models.ForeignKey('identity.User', on_delete=models.PROTECT, related_name='+')
    reason = models.CharField(max_length=500)
    status = models.CharField(max_length=16, choices=QRDisputeStatus.choices, default=QRDisputeStatus.OPEN)
    resolved_by = models.ForeignKey('identity.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_note = models.CharField(max_length=500, blank=True, default='')
    refund_amount = MoneyField(null=True, blank=True)
    resolution_transaction = models.ForeignKey(
        WalletTransaction, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )

    class Meta:
        db_table = 'pos_qr_disputes'
        indexes = [
            models.Index(fields=['foundation_id', 'merchant_id', 'status']),
            models.Index(fields=['foundation_id', 'student_id']),
        ]

    def __str__(self):
        return f"Dispute #{self.pk} on POS {self.pos_transaction_id} ({self.status})"


class MerchantSettlementStatus(models.TextChoices):
    PENDING = 'PENDING', _('Menunggu')
    PAID = 'PAID', _('Dibayar')


class MerchantSettlement(TenantModel):
    """A settlement run for one merchant over one period (spec/07 §6, WAL-021 to WAL-022)."""
    merchant = models.ForeignKey(Merchant, on_delete=models.PROTECT, related_name='settlements')
    period_start = models.DateField()
    period_end = models.DateField()
    gross = MoneyField(default=Decimal('0.00'))
    commission = MoneyField(default=Decimal('0.00'))
    net = MoneyField(default=Decimal('0.00'))
    status = models.CharField(max_length=16, choices=MerchantSettlementStatus.choices, default=MerchantSettlementStatus.PENDING)
    paid_at = models.DateTimeField(null=True, blank=True)
    statement_pdf_key = models.CharField(max_length=255, blank=True, default='')
    active_uniq_marker = soft_delete_uniqueness_marker()

    class Meta:
        db_table = 'merchant_settlements'
        indexes = [
            models.Index(fields=['foundation_id', 'merchant_id', 'period_start']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'merchant', 'period_start', 'period_end', 'active_uniq_marker'],
                name='unique_settlement_per_merchant_period',
            ),
        ]

    def __str__(self):
        return f"{self.merchant.name} {self.period_start}..{self.period_end}: net {self.net} ({self.status})"


class WalletReconciliationTrigger(models.TextChoices):
    OFFLINE_OVERSPEND = 'OFFLINE_OVERSPEND', _('Kelebihan Belanja Luring')
    SYNC_DRIFT = 'SYNC_DRIFT', _('Selisih Sinkronisasi')
    MANUAL_ADJUSTMENT = 'MANUAL_ADJUSTMENT', _('Penyesuaian Manual')


class WalletReconciliationStatus(models.TextChoices):
    OPEN = 'OPEN', _('Terbuka')
    SETTLED = 'SETTLED', _('Diselesaikan')
    WRITTEN_OFF = 'WRITTEN_OFF', _('Dihapusbukukan')
    INVOICED = 'INVOICED', _('Ditagihkan')


class WalletReconciliation(TenantModel):
    """A single debt case opened when an offline overspend takes a wallet negative (spec/17, REC-001).

    One row per accepted overspend transaction (REC-004); notices combine all OPEN
    rows for a wallet, never one message per row.
    """
    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name='reconciliations')
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name='wallet_reconciliations')
    trigger = models.CharField(max_length=24, choices=WalletReconciliationTrigger.choices)
    shortfall = MoneyField(help_text=_("Absolute value of the negative balance at detection (REC-002)"))
    currency = models.CharField(max_length=3, default='IDR')
    balance_at_detection = MoneyField()
    pos_transaction = models.ForeignKey(
        'wallet.POSTransaction', on_delete=models.SET_NULL, null=True, blank=True, related_name='reconciliations'
    )
    detected_at = models.DateTimeField()
    detected_by_job = models.CharField(max_length=64, blank=True, default='')
    status = models.CharField(max_length=16, choices=WalletReconciliationStatus.choices, default=WalletReconciliationStatus.OPEN)
    settled_at = models.DateTimeField(null=True, blank=True)
    settled_by_transaction = models.ForeignKey(
        WalletTransaction, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    notice_sent_at = models.DateTimeField(null=True, blank=True)
    reminder_sent_at = models.DateTimeField(null=True, blank=True)
    invoiced_at = models.DateTimeField(null=True, blank=True)
    invoice = models.ForeignKey('finance.Invoice', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    written_off_by = models.ForeignKey('identity.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    write_off_reason = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        db_table = 'wallet_reconciliations'
        indexes = [
            models.Index(fields=['foundation_id', 'wallet_id', 'status']),
            models.Index(fields=['foundation_id', 'status', 'detected_at']),
        ]

    def __str__(self):
        return f"{self.student.nis} - {self.currency} {self.shortfall} ({self.status})"


class WalletRefundStatus(models.TextChoices):
    PENDING = 'PENDING', _('Menunggu')
    PAID = 'PAID', _('Dibayar')
    DONATED = 'DONATED', _('Didonasikan')


class WalletRefundRequest(TenantModel):
    """A residual wallet balance queued for refund on student exit (spec/07 §7, WAL-026/027).

    A PENDING row is never auto-resolved by anything — the balance remains the
    guardian's liability on the books indefinitely until a bendahara acts on it.
    """
    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name='refund_requests')
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name='wallet_refund_requests')
    amount = MoneyField()
    currency = models.CharField(max_length=3, default='IDR')
    status = models.CharField(max_length=16, choices=WalletRefundStatus.choices, default=WalletRefundStatus.PENDING)
    requested_at = models.DateTimeField()
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey('identity.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    guardian_bank_name = models.CharField(max_length=64, blank=True, default='')
    guardian_bank_account_number = models.CharField(max_length=64, blank=True, default='')
    guardian_account_holder_name = models.CharField(max_length=128, blank=True, default='')
    donation_consent = models.BooleanField(default=False)
    notes = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        db_table = 'wallet_refund_requests'
        indexes = [
            models.Index(fields=['foundation_id', 'status']),
            models.Index(fields=['foundation_id', 'wallet_id', 'status']),
        ]

    def __str__(self):
        return f"{self.student.nis} - {self.currency} {self.amount} ({self.status})"


class WalletTopupIntentStatus(models.TextChoices):
    PENDING = 'PENDING', _('Menunggu Pembayaran')
    SETTLED = 'SETTLED', _('Selesai')
    EXPIRED = 'EXPIRED', _('Kedaluwarsa')
    FAILED = 'FAILED', _('Gagal')


class WalletTopupIntent(TenantModel):
    """A pending gateway-based wallet top-up (WAL-005: VA/QRIS).

    Reuses apps.finance's stable per-student VA allocator and generic
    PaymentProvider.create_qris/verify_webhook/parse_webhook (via lazy import) —
    those are amount/student-parameterized, not Invoice-specific. A separate
    intent model (rather than extending finance.PaymentIntent) avoids forcing an
    optional/nullable Invoice relationship onto a model that is structurally
    invoice-coupled everywhere else it is used.
    """
    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name='topup_intents')
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name='wallet_topup_intents')
    method = models.CharField(max_length=8, choices=WalletTopupMethod.choices)
    provider = models.CharField(max_length=16, default='MOCK')
    amount = MoneyField()
    currency = models.CharField(max_length=3, default='IDR')
    va_bank = models.CharField(max_length=16, blank=True, default='')
    va_number = models.CharField(max_length=32, blank=True, default='')
    qris_payload = models.CharField(max_length=255, blank=True, default='')
    external_id = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=16, choices=WalletTopupIntentStatus.choices, default=WalletTopupIntentStatus.PENDING)
    expires_at = models.DateTimeField()
    settled_at = models.DateTimeField(null=True, blank=True)
    wallet_transaction = models.ForeignKey(WalletTransaction, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    class Meta:
        db_table = 'wallet_topup_intents'
        indexes = [
            models.Index(fields=['foundation_id', 'wallet_id', 'status']),
        ]

    def __str__(self):
        return f"{self.student.nis} - {self.method} {self.currency} {self.amount} ({self.status})"


class WalletAutoTopupConfig(TenantModel):
    """Guardian-configured auto-top-up (WAL-005, WAL-006): when the wallet balance
    drops below threshold_amount, the system automatically generates a new VA/QRIS
    WalletTopupIntent for topup_amount. This is semi-automatic, not a silent
    auto-debit — VA/QRIS have no rail for that; the guardian still completes the
    transfer through their own banking app, same as any other top-up. Only VA/QRIS
    are supported methods here (the gateway top-up methods create_wallet_topup_intent
    already handles), not CASH/MANUAL.
    """
    wallet = models.OneToOneField(Wallet, on_delete=models.PROTECT, related_name='auto_topup_config')
    is_active = models.BooleanField(default=False, help_text=_("Explicit opt-in required (WAL-006)"))
    threshold_amount = MoneyField(help_text=_("Auto-top-up triggers when balance drops below this"))
    topup_amount = MoneyField(help_text=_("Amount requested each time auto-top-up triggers"))
    method = models.CharField(max_length=8, choices=WalletTopupMethod.choices, default=WalletTopupMethod.VA)
    bank = models.CharField(max_length=16, blank=True, default='')

    class Meta:
        db_table = 'wallet_auto_topup_configs'

    def __str__(self):
        return f"{self.wallet.student.nis} auto-topup ({'active' if self.is_active else 'inactive'})"
