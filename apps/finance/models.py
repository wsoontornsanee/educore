from decimal import Decimal
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.fields import MoneyField
from apps.core.fields import soft_delete_uniqueness_marker
from apps.core.models import TenantModel
from apps.identity.models import School, Student, User


class FeeCategory(models.TextChoices):
    SPP = 'SPP', _('SPP (Monthly Tuition)')
    ENTRY = 'ENTRY', _('Uang Pangkal (Entry Fee)')
    ACTIVITY = 'ACTIVITY', _('Uang Kegiatan (Activity Fee)')
    EXAM = 'EXAM', _('Uang Ujian (Exam Fee)')
    UNIFORM = 'UNIFORM', _('Seragam (Uniform)')
    BOOK = 'BOOK', _('Buku Paket (Book)')
    OTHER = 'OTHER', _('Lainnya (Other)')


class FeeRecurrence(models.TextChoices):
    MONTHLY = 'MONTHLY', _('Bulanan (Monthly)')
    TERM = 'TERM', _('Per Semester (Term)')
    ANNUAL = 'ANNUAL', _('Tahunan (Annual)')
    ONE_OFF = 'ONE_OFF', _('Sekali Bayar (One-off)')


class DiscountType(models.TextChoices):
    PERCENT = 'PERCENT', _('Persentase (Percent)')
    FIXED = 'FIXED', _('Nominal Tetap (Fixed Amount)')


class DiscountStatus(models.TextChoices):
    DRAFT = 'DRAFT', _('Konsep (Draft)')
    PENDING_APPROVAL = 'PENDING_APPROVAL', _('Menunggu Persetujuan (Pending Approval)')
    APPROVED = 'APPROVED', _('Disetujui (Approved)')
    REJECTED = 'REJECTED', _('Ditolak (Rejected)')


class FeeAssignmentSource(models.TextChoices):
    STUDENT = 'STUDENT', _('Siswa Individu (Individual Student)')
    CLASS = 'CLASS', _('Rombel Kelas (Class Group)')
    GRADE = 'GRADE', _('Tingkat Kelas (Grade Level)')


class FeeType(TenantModel):
    """Catalogue item for fees charged by a school (spec/06 §2)."""
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name='fee_types')
    code = models.CharField(max_length=32, db_index=True, help_text=_("e.g. SPP_REGULER, UANG_GEDUNG"))
    name = models.CharField(max_length=128, help_text=_("Display label, e.g. SPP Bulanan"))
    category = models.CharField(max_length=32, choices=FeeCategory.choices, default=FeeCategory.SPP)
    recurrence = models.CharField(max_length=32, choices=FeeRecurrence.choices, default=FeeRecurrence.MONTHLY)
    default_amount = MoneyField(default=Decimal('0.00'), help_text=_("Default fee amount in standard currency"))
    currency = models.CharField(max_length=3, default='IDR')
    taxable = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'fee_types'
        constraints = [
            models.UniqueConstraint(fields=['foundation_id', 'school', 'code'], name='unique_school_fee_type_code'),
        ]
        indexes = [
            models.Index(fields=['foundation_id', 'school', 'category']),
        ]

    def __str__(self):
        return f"[{self.school.name}] {self.code} - {self.name} ({self.currency} {self.default_amount})"


class FeePlan(TenantModel):
    """Bundled fee schedule for academic years and grade levels (spec/06 §2, FIN-001)."""
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name='fee_plans')
    academic_year = models.CharField(max_length=16, help_text=_("e.g. 2026/2027"))
    name = models.CharField(max_length=128, help_text=_("e.g. Paket SPP & Kegiatan Kelas 7"))
    grade_levels = models.JSONField(default=list, help_text=_("List of grade level strings e.g. ['7', '8', '9']"))
    lines = models.JSONField(default=list, help_text=_("List of fee line items [{fee_type_id, amount, currency}]"))
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'fee_plans'
        indexes = [
            models.Index(fields=['foundation_id', 'school', 'academic_year']),
        ]

    def __str__(self):
        return f"{self.school.name} - {self.name} ({self.academic_year})"


class StudentFeeAssignment(TenantModel):
    """Direct fee assignment or override for a student (FIN-001)."""
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name='fee_assignments')
    fee_type = models.ForeignKey(FeeType, on_delete=models.PROTECT, related_name='student_assignments')
    amount = MoneyField(default=Decimal('0.00'))
    currency = models.CharField(max_length=3, default='IDR')
    start_period = models.CharField(max_length=7, help_text=_("Effective start period YYYY-MM"))
    end_period = models.CharField(max_length=7, null=True, blank=True, help_text=_("Optional end period YYYY-MM"))
    source = models.CharField(max_length=16, choices=FeeAssignmentSource.choices, default=FeeAssignmentSource.STUDENT)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'student_fee_assignments'
        indexes = [
            models.Index(fields=['foundation_id', 'student', 'start_period']),
        ]

    def __str__(self):
        return f"Assignment #{self.id} {self.student.person.full_name} -> {self.fee_type.code}: {self.currency} {self.amount}"


class Discount(TenantModel):
    """Discount or waiver rule applied to tuition/fees (spec/06 §2, FIN-007)."""
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name='discounts')
    fee_type = models.ForeignKey(FeeType, null=True, blank=True, on_delete=models.PROTECT, related_name='discounts')
    type = models.CharField(max_length=16, choices=DiscountType.choices, default=DiscountType.PERCENT)
    value = MoneyField(default=Decimal('0.00'), help_text=_("Percentage (e.g. 10.00) or fixed amount in currency"))
    reason = models.CharField(max_length=255, help_text=_("Alasan diskon/keringanan, e.g. Beasiswa Tahfidz, Prestasi"))
    valid_from = models.DateField()
    valid_to = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=32, choices=DiscountStatus.choices, default=DiscountStatus.DRAFT, db_index=True)
    approved_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='approved_discounts')
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'discounts'
        indexes = [
            models.Index(fields=['foundation_id', 'student', 'status']),
        ]

    def __str__(self):
        val_str = f"{self.value}%" if self.type == DiscountType.PERCENT else f"Rp {self.value}"
        return f"Discount {self.student.person.full_name} - {val_str} ({self.status})"


class SiblingDiscountPolicy(TenantModel):
    """School policy for automatic sibling discounts on SPP (spec/06 §3, FIN-006)."""
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name='sibling_policies')
    child_order = models.PositiveIntegerField(help_text=_("Child order (2 for 2nd child, 3 for 3rd child, etc.)"))
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'), help_text=_("Discount percentage (e.g. 10.00 for 10%)"))
    fee_category = models.CharField(max_length=32, choices=FeeCategory.choices, default=FeeCategory.SPP)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'sibling_discount_policies'
        constraints = [
            models.UniqueConstraint(fields=['foundation_id', 'school', 'child_order', 'fee_category'], name='unique_school_sibling_policy'),
        ]

    def __str__(self):
        return f"{self.school.name} - Child #{self.child_order} -> {self.discount_percent}% off {self.fee_category}"


class InvoiceStatus(models.TextChoices):
    DRAFT = 'DRAFT', _('Konsep (Draft)')
    ISSUED = 'ISSUED', _('Diterbitkan (Issued)')
    PARTIALLY_PAID = 'PARTIALLY_PAID', _('Dibayar Sebagian (Partially Paid)')
    PAID = 'PAID', _('Lunas (Paid)')
    CANCELLED = 'CANCELLED', _('Dibatalkan (Cancelled)')
    WRITTEN_OFF = 'WRITTEN_OFF', _('Dihapusbukukan (Written Off)')


class InvoiceNumberSequence(TenantModel):
    """Atomic gapless invoice sequence counter per school per year (spec/06 §3, FIN-004)."""
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name='invoice_sequences')
    year = models.PositiveIntegerField(help_text=_("Calendar year (e.g. 2026)"))
    last_number = models.PositiveIntegerField(default=0, help_text=_("Latest allocated sequence number"))

    class Meta:
        db_table = 'invoice_number_sequences'
        constraints = [
            models.UniqueConstraint(fields=['foundation_id', 'school', 'year'], name='unique_school_year_invoice_sequence'),
        ]

    def __str__(self):
        return f"{self.school.name} - {self.year} (last: {self.last_number})"


class Invoice(TenantModel):
    """Tuition and fee invoice for a student (spec/06 §2, §3, spec/16)."""
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name='invoices')
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name='invoices')
    number = models.CharField(max_length=64, unique=True, db_index=True, help_text=_("Format: INV/{school_code}/{YYYY}/{NNNNNN}"))
    period = models.CharField(max_length=7, db_index=True, help_text=_("Billing period format YYYY-MM (e.g. 2026-10)"))
    issue_date = models.DateField(default=timezone.now)
    due_date = models.DateField(help_text=_("Payment due date"))
    
    subtotal = MoneyField(default=Decimal('0.00'), help_text=_("Sum of lines before discounts"))
    discount = MoneyField(default=Decimal('0.00'), help_text=_("Total discounts applied"))
    rounding = MoneyField(default=Decimal('0.00'), help_text=_("PEMBULATAN line adjustment (FIN-008c, CUR-019)"))
    total = MoneyField(default=Decimal('0.00'), help_text=_("Final amount payable (subtotal - discount + rounding)"))
    paid = MoneyField(default=Decimal('0.00'), help_text=_("Cumulative settled payments"))
    
    currency = models.CharField(max_length=3, default='IDR', help_text=_("Invoice currency (CUR-002, CUR-008)"))
    status = models.CharField(max_length=32, choices=InvoiceStatus.choices, default=InvoiceStatus.ISSUED, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    active_uniq_marker = soft_delete_uniqueness_marker()

    class Meta:
        db_table = 'invoices'
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'student', 'period', 'active_uniq_marker'],
                name='unique_active_invoice_per_student_period',
            ),
        ]
        indexes = [
            models.Index(fields=['foundation_id', 'school', 'period']),
            models.Index(fields=['foundation_id', 'student', 'status']),
            models.Index(fields=['foundation_id', 'due_date', 'status']),
        ]

    def __str__(self):
        return f"{self.number} - {self.student.person.full_name} ({self.period}): {self.currency} {self.total}"

    @property
    def balance_due(self) -> Decimal:
        """Remaining balance unpaid."""
        return max(Decimal('0.00'), self.total - self.paid)

    @property
    def is_overdue(self) -> bool:
        """Derived overdue state per FIN-009 (not stored as terminal state)."""
        if self.status in [InvoiceStatus.PAID, InvoiceStatus.CANCELLED, InvoiceStatus.WRITTEN_OFF]:
            return False
        return timezone.localdate() > self.due_date


class InvoiceLine(TenantModel):
    """Line item on an invoice (spec/06 §2)."""
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='lines')
    fee_type = models.ForeignKey(FeeType, null=True, blank=True, on_delete=models.PROTECT, related_name='invoice_lines')
    code = models.CharField(max_length=32, help_text=_("Line code, e.g. SPP_REGULER or PEMBULATAN"))
    description = models.CharField(max_length=255, help_text=_("Display item description"))
    amount = MoneyField(default=Decimal('0.00'), help_text=_("Base line item amount"))
    discount = MoneyField(default=Decimal('0.00'), help_text=_("Discount deduction on this line"))
    subtotal = MoneyField(default=Decimal('0.00'), help_text=_("Net line total (amount - discount)"))
    currency = models.CharField(max_length=3, default='IDR')
    waived = models.BooleanField(default=False, help_text=_("Set when a FIXED-type discount (waiver) is approved against this line (spec/03 FND-007 AC#2, spec/06 §2)"))

    class Meta:
        db_table = 'invoice_lines'
        indexes = [
            models.Index(fields=['foundation_id', 'invoice']),
        ]

    def __str__(self):
        return f"{self.invoice.number} - {self.code}: {self.currency} {self.subtotal}"


class InvoiceInstallmentStatus(models.TextChoices):
    PENDING = 'PENDING', _('Menunggu Pembayaran (Pending)')
    PARTIALLY_PAID = 'PARTIALLY_PAID', _('Dibayar Sebagian (Partially Paid)')
    PAID = 'PAID', _('Lunas (Paid)')
    CANCELLED = 'CANCELLED', _('Dibatalkan (Cancelled)')


class InvoiceInstallment(TenantModel):
    """Payment plan installment schedule for an invoice (spec/06 §6, §8, FIN-030)."""
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='installments')
    installment_no = models.PositiveSmallIntegerField(help_text=_("Installment index starting from 1"))
    due_date = models.DateField(help_text=_("Due date for this installment"))
    amount = MoneyField(default=Decimal('0.00'), help_text=_("Scheduled installment amount (CUR-018)"))
    paid_amount = MoneyField(default=Decimal('0.00'), help_text=_("Cumulative settled payments on this installment"))
    currency = models.CharField(max_length=3, default='IDR')
    status = models.CharField(
        max_length=32,
        choices=InvoiceInstallmentStatus.choices,
        default=InvoiceInstallmentStatus.PENDING,
        db_index=True,
    )
    paid_at = models.DateTimeField(null=True, blank=True)
    notes = models.CharField(max_length=255, blank=True)
    active_uniq_marker = soft_delete_uniqueness_marker()

    class Meta:
        db_table = 'invoice_installments'
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'invoice', 'installment_no', 'active_uniq_marker'],
                name='unique_invoice_installment_no',
            ),
        ]
        indexes = [
            models.Index(fields=['foundation_id', 'invoice', 'status']),
            models.Index(fields=['foundation_id', 'due_date', 'status']),
        ]

    def __str__(self):
        return f"{self.invoice.number} - Cicilan #{self.installment_no}: {self.currency} {self.amount} ({self.status})"

    @property
    def balance_due(self) -> Decimal:
        """Remaining balance unpaid on this installment."""
        return max(Decimal('0.00'), self.amount - self.paid_amount)

    @property
    def is_overdue(self) -> bool:
        if self.status in [InvoiceInstallmentStatus.PAID, InvoiceInstallmentStatus.CANCELLED]:
            return False
        return timezone.localdate() > self.due_date


class PaymentMethod(models.TextChoices):
    VA = 'VA', _('Virtual Account')
    QRIS = 'QRIS', _('QRIS')
    MANUAL = 'MANUAL', _('Transfer Manual (Manual Transfer)')
    CASH = 'CASH', _('Tunai di Sekolah (Cash at School)')


class PaymentStatus(models.TextChoices):
    PENDING = 'PENDING', _('Menunggu Pembayaran (Pending)')
    SETTLED = 'SETTLED', _('Berhasil/Lunas (Settled)')
    FAILED = 'FAILED', _('Gagal (Failed)')
    CANCELLED = 'CANCELLED', _('Dibatalkan (Cancelled)')
    PENDING_VERIFICATION = 'PENDING_VERIFICATION', _('Menunggu Verifikasi (Pending Verification)')
    REJECTED = 'REJECTED', _('Ditolak (Rejected)')


class PaymentIntentStatus(models.TextChoices):
    PENDING = 'PENDING', _('Menunggu Pembayaran (Pending)')
    COMPLETED = 'COMPLETED', _('Selesai (Completed)')
    EXPIRED = 'EXPIRED', _('Kedaluwarsa (Expired)')
    CANCELLED = 'CANCELLED', _('Dibatalkan (Cancelled)')


class AccountCode(models.TextChoices):
    CASH_BANK = '1100', _('Kas / Bank')
    ACCOUNTS_RECEIVABLE = '1200', _('Piutang SPP & Biaya')
    UNEARNED_TUITION = '2100', _('Pendapatan Diterima di Muka')
    STUDENT_CREDIT = '2200', _('Saldo Deposit Siswa')
    TUITION_REVENUE = '4100', _('Pendapatan SPP')
    OTHER_REVENUE = '4200', _('Pendapatan Lain-lain')
    PAYMENT_FEES = '5100', _('Beban Transaksi & Gateway')
    DISCOUNT_EXPENSE = '5200', _('Beban Potongan & Keringanan')
    ROUNDING = '5300', _('Beban / Pendapatan Pembulatan')
    BAD_DEBT_EXPENSE = '5400', _('Beban Piutang Tak Tertagih (Bad Debt)')


class StudentVirtualAccount(TenantModel):
    """Stable per-student Virtual Account mapping per bank (spec/06 §4, FIN-011)."""
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name='virtual_accounts')
    bank = models.CharField(max_length=32, help_text=_("e.g. BCA, MANDIRI, BRI, BNI, PERMATA"))
    va_number = models.CharField(max_length=64, db_index=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'student_virtual_accounts'
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'bank', 'va_number'],
                name='unique_foundation_bank_va_number',
            ),
        ]
        indexes = [
            models.Index(fields=['foundation_id', 'student', 'bank']),
        ]

    def __str__(self):
        return f"VA {self.bank} - {self.student.person.full_name}: {self.va_number}"


class PaymentIntent(TenantModel):
    """Payment intent for initiating VA, QRIS, or gateway payments (spec/06 §2, §4)."""
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name='payment_intents')
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name='payment_intents')
    invoice = models.ForeignKey(Invoice, null=True, blank=True, on_delete=models.PROTECT, related_name='payment_intents')
    
    method = models.CharField(max_length=32, choices=PaymentMethod.choices, default=PaymentMethod.VA)
    provider = models.CharField(max_length=32, default='MIDTRANS', help_text=_("MIDTRANS, XENDIT, MOCK, MANUAL"))
    va_bank = models.CharField(max_length=32, null=True, blank=True, help_text=_("Bank code for VA, e.g. BCA, MANDIRI"))
    va_number = models.CharField(max_length=64, null=True, blank=True)
    qris_payload = models.TextField(null=True, blank=True)
    
    amount = MoneyField(default=Decimal('0.00'))
    currency = models.CharField(max_length=3, default='IDR')
    expires_at = models.DateTimeField()
    status = models.CharField(max_length=32, choices=PaymentIntentStatus.choices, default=PaymentIntentStatus.PENDING, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'payment_intents'
        indexes = [
            models.Index(fields=['foundation_id', 'school', 'status']),
            models.Index(fields=['foundation_id', 'student', 'status']),
            models.Index(fields=['foundation_id', 'va_number']),
        ]

    def __str__(self):
        return f"Intent #{self.id} {self.method} ({self.currency} {self.amount}) - {self.status}"


class PaymentSequence(TenantModel):
    """Gapless sequence numbers for payments and receipts (spec/06 §3, §4)."""
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name='payment_sequences')
    sequence_type = models.CharField(max_length=16, help_text=_("PAYMENT, RECEIPT, JOURNAL"))
    year = models.PositiveIntegerField(help_text=_("Calendar year e.g. 2026"))
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'payment_sequences'
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'school', 'sequence_type', 'year'],
                name='unique_school_sequence_type_year',
            ),
        ]

    def __str__(self):
        return f"{self.school.name} - {self.sequence_type} {self.year}: {self.last_number}"


class Payment(TenantModel):
    """Settled or pending payment record (spec/06 §2, §4)."""
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name='payments')
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name='payments')
    invoice = models.ForeignKey(Invoice, null=True, blank=True, on_delete=models.PROTECT, related_name='payments')
    payment_intent = models.ForeignKey(PaymentIntent, null=True, blank=True, on_delete=models.PROTECT, related_name='payments')
    
    amount = MoneyField(default=Decimal('0.00'))
    currency = models.CharField(max_length=3, default='IDR')
    method = models.CharField(max_length=32, choices=PaymentMethod.choices, default=PaymentMethod.VA)
    channel = models.CharField(max_length=64, help_text=_("e.g. BCA_VA, QRIS_GOPAY, CASHIER, MANDIRI_TRANSFER"))
    reference = models.CharField(max_length=64, unique=True, db_index=True, help_text=_("Unique payment reference e.g. PAY/SDIT01/2026/000001"))
    external_id = models.CharField(max_length=128, null=True, blank=True, db_index=True, help_text=_("Gateway order_id or transaction ID"))
    
    paid_at = models.DateTimeField(default=timezone.now)
    settled_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=32, choices=PaymentStatus.choices, default=PaymentStatus.PENDING, db_index=True)
    
    fee = MoneyField(default=Decimal('0.00'), help_text=_("Gateway or transaction fee"))
    net = MoneyField(default=Decimal('0.00'), help_text=_("Net amount received by school (amount - fee)"))
    
    receipt_number = models.CharField(max_length=64, null=True, blank=True, help_text=_("Receipt number e.g. RCP/SDIT01/2026/000001"))
    received_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='received_payments')
    proof_file = models.CharField(max_length=512, null=True, blank=True, help_text=_("Proof upload file path"))
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'payments'
        constraints = [
            # No condition= needed: MySQL/Postgres/SQLite unique indexes all
            # already treat NULL as never-equal-to-NULL, so a plain constraint
            # on a nullable column is portable and matches the old
            # external_id__isnull=False intent exactly (unlike the
            # soft-delete-scoped constraints elsewhere in this file, which
            # needed a GeneratedField marker — see apps.core.fields).
            models.UniqueConstraint(
                fields=['foundation_id', 'external_id'],
                name='unique_foundation_payment_external_id',
            ),
        ]
        indexes = [
            models.Index(fields=['foundation_id', 'school', 'status']),
            models.Index(fields=['foundation_id', 'student', 'status']),
            models.Index(fields=['foundation_id', 'paid_at']),
        ]

    def __str__(self):
        return f"{self.reference} - {self.student.person.full_name}: {self.currency} {self.amount} ({self.status})"

    def delete(self, using=None, keep_parents=False):
        """FIN-020: A payment MUST NOT be physically deleted. Soft-delete only."""
        if not self.deleted_at:
            self.deleted_at = timezone.now()
            self.save(update_fields=['deleted_at'])


class PaymentAllocation(TenantModel):
    """Allocation of payment to an invoice and optional invoice line (spec/06 §2, §4)."""
    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name='allocations')
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name='allocations')
    invoice_line = models.ForeignKey(InvoiceLine, null=True, blank=True, on_delete=models.PROTECT, related_name='allocations')
    amount = MoneyField(default=Decimal('0.00'))
    currency = models.CharField(max_length=3, default='IDR')

    class Meta:
        db_table = 'payment_allocations'
        indexes = [
            models.Index(fields=['foundation_id', 'payment']),
            models.Index(fields=['foundation_id', 'invoice']),
        ]

    def __str__(self):
        return f"Alloc {self.payment.reference} -> {self.invoice.number}: {self.currency} {self.amount}"


class StudentCreditBalance(TenantModel):
    """Credit balance for a student resulting from overpayments (spec/06 §4, FIN-015)."""
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name='credit_balances')
    balance = MoneyField(default=Decimal('0.00'))
    currency = models.CharField(max_length=3, default='IDR')

    class Meta:
        db_table = 'student_credit_balances'
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'student', 'currency'],
                name='unique_foundation_student_currency_credit_balance',
            ),
        ]

    def __str__(self):
        return f"Credit {self.student.person.full_name}: {self.currency} {self.balance}"


class LedgerJournal(TenantModel):
    """Double-entry general ledger journal header (spec/06 §2, §5, spec/16 CUR-020)."""
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name='ledger_journals')
    number = models.CharField(max_length=64, unique=True, db_index=True, help_text=_("Format: JRN/{school_code}/{YYYY}/{NNNNNN}"))
    description = models.CharField(max_length=255)
    ref_type = models.CharField(max_length=32, help_text=_("e.g. INVOICE, PAYMENT, REVENUE_RECOGNITION, REFUND"))
    ref_id = models.CharField(max_length=64, help_text=_("Primary identifier of the triggering entity"))
    currency = models.CharField(max_length=3, default='IDR')
    occurred_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'ledger_journals'
        indexes = [
            models.Index(fields=['foundation_id', 'school', 'occurred_at']),
            models.Index(fields=['foundation_id', 'ref_type', 'ref_id']),
        ]

    def __str__(self):
        return f"{self.number} ({self.ref_type} #{self.ref_id}) - {self.description}"


class LedgerEntry(TenantModel):
    """Double-entry ledger entry line item (spec/06 §2, §5)."""
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name='ledger_entries')
    journal = models.ForeignKey(LedgerJournal, on_delete=models.CASCADE, related_name='entries')
    account_code = models.CharField(max_length=32, db_index=True, help_text=_("e.g. 1100, 1200, 2100, 4100"))
    account_name = models.CharField(max_length=128)
    debit = MoneyField(default=Decimal('0.00'))
    credit = MoneyField(default=Decimal('0.00'))
    currency = models.CharField(max_length=3, default='IDR')
    ref_type = models.CharField(max_length=32, null=True, blank=True)
    ref_id = models.CharField(max_length=64, null=True, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'ledger_entries'
        indexes = [
            models.Index(fields=['foundation_id', 'school', 'account_code']),
            models.Index(fields=['foundation_id', 'journal']),
            models.Index(fields=['foundation_id', 'occurred_at']),
        ]

    def __str__(self):
        return f"Entry {self.account_code} ({self.account_name}): Dr {self.debit} / Cr {self.credit} {self.currency}"


class FiscalPeriodStatus(models.TextChoices):
    OPEN = 'OPEN', _('Terbuka')
    CLOSED = 'CLOSED', _('Ditutup')


class FiscalPeriod(TenantModel):
    """Monthly fiscal period tracking and ledger locking (spec/06 §5, §8, FIN-025)."""
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name='fiscal_periods')
    period = models.CharField(max_length=7, db_index=True, help_text=_("Format YYYY-MM, e.g. 2026-08"))
    status = models.CharField(max_length=16, choices=FiscalPeriodStatus.choices, default=FiscalPeriodStatus.OPEN, db_index=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT, related_name='closed_fiscal_periods')
    reopened_at = models.DateTimeField(null=True, blank=True)
    reopened_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT, related_name='reopened_fiscal_periods')
    total_journals = models.PositiveIntegerField(default=0)
    total_debit = MoneyField(default=Decimal('0.00'))
    total_credit = MoneyField(default=Decimal('0.00'))
    closing_notes = models.TextField(blank=True, default='')
    active_uniq_marker = soft_delete_uniqueness_marker()

    class Meta:
        db_table = 'finance_fiscal_periods'
        indexes = [
            models.Index(fields=['foundation_id', 'school', 'period']),
            models.Index(fields=['foundation_id', 'status']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'school', 'period', 'active_uniq_marker'],
                name='unique_school_fiscal_period',
            ),
        ]

    def __str__(self):
        return f"{self.school.name} - Period {self.period} ({self.status})"


class SchoolQrisConfig(TenantModel):
    """A school's own static QRIS code (spec/06 §4 FIN-010) — the kind printed or
    exported directly from the school's bank, not a per-transaction dynamic gateway
    QRIS. A guardian scans it, pays any amount through their own banking/e-wallet
    app, then submits a receipt through the existing manual-transfer-with-proof flow
    (channel='STATIC_QRIS') for finance to verify — no gateway call involved at all.
    """
    school = models.OneToOneField(School, on_delete=models.PROTECT, related_name='qris_config')
    qris_image_key = models.CharField(max_length=512, blank=True, default='', help_text=_("MEDIA_ROOT-relative key of the uploaded static QRIS image"))
    qris_payload = models.TextField(blank=True, default='', help_text=_("Raw QRIS string, if the school has it in addition to/instead of an image"))
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'school_qris_configs'

    def __str__(self):
        return f"{self.school.name} static QRIS ({'active' if self.is_active else 'inactive'})"


class ConvenienceFeeAllocation(models.TextChoices):
    ABSORBED_BY_SCHOOL = 'ABSORBED_BY_SCHOOL', _('Ditanggung Sekolah (Absorbed by School)')
    PASSED_TO_PARENT = 'PASSED_TO_PARENT', _('Dibebankan ke Orang Tua (Passed to Parent)')


class ConvenienceFeeType(models.TextChoices):
    FIXED = 'FIXED', _('Nominal Tetap (Fixed Amount)')
    PERCENTAGE = 'PERCENTAGE', _('Persentase (Percentage)')


class SchoolConvenienceFeePolicy(TenantModel):
    """Per-school gateway convenience fee allocation and schedule (spec/06 FIN-017).

    Resolves [Open Decision] Payment Gateway Convenience Fee Allocation Policy:
    platform default is PASSED_TO_PARENT, but each school can override via this
    config. When no row exists for a school, get_effective_convenience_fee_policy
    falls back to PASSED_TO_PARENT with fee_value=0 — i.e. no fee is actually
    charged until a school explicitly configures a non-zero fee_value, which is
    the safe default (a school never gets silently charged a fee amount nobody set).
    """
    school = models.OneToOneField(School, on_delete=models.PROTECT, related_name='convenience_fee_policy')
    allocation = models.CharField(
        max_length=32,
        choices=ConvenienceFeeAllocation.choices,
        default=ConvenienceFeeAllocation.PASSED_TO_PARENT,
    )
    fee_type = models.CharField(
        max_length=16,
        choices=ConvenienceFeeType.choices,
        default=ConvenienceFeeType.FIXED,
    )
    fee_value = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text=_("Fixed amount (in the invoice's currency) or percentage (e.g. 1.50 for 1.5%), per fee_type"),
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'school_convenience_fee_policies'

    def __str__(self):
        return f"{self.school.name} convenience fee ({self.allocation}, {self.fee_type} {self.fee_value})"


class BankStatementFileFormat(models.TextChoices):
    MT940 = 'MT940', _('SWIFT MT940')
    CAMT053 = 'CAMT053', _('ISO 20022 CAMT.053')


class BankSftpConfig(TenantModel):
    """Connection metadata for automated host-to-host SFTP pull of daily bank
    statement files (spec/14 CMP-024, CMP-026) — one row per school per bank.

    Stores CONNECTION METADATA ONLY, never secret material (password/private
    key). This codebase has no per-tenant secrets vault yet; until one exists,
    the actual credential is resolved at pull time from an environment
    variable named f'BANK_SFTP_KEY_{config.id}' (see
    apps.finance.services.bank_sftp_pull), which is itself a stopgap — real
    secrets management is tracked as a follow-up Open Item, not solved here.
    """
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name='bank_sftp_configs')
    bank_code = models.CharField(max_length=32, help_text=_("e.g. BCA, MANDIRI, BRI"))
    host = models.CharField(max_length=255)
    port = models.PositiveIntegerField(default=22)
    username = models.CharField(max_length=128)
    remote_directory = models.CharField(max_length=512, default='/', help_text=_("Remote directory to list/pull statement files from"))
    file_format = models.CharField(max_length=16, choices=BankStatementFileFormat.choices, default=BankStatementFileFormat.MT940)
    is_active = models.BooleanField(default=True)
    active_uniq_marker = soft_delete_uniqueness_marker()

    class Meta:
        db_table = 'bank_sftp_configs'
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'school', 'bank_code', 'active_uniq_marker'],
                name='unique_foundation_school_bank_sftp_config',
            ),
        ]

    def __str__(self):
        return f"{self.school.name} SFTP {self.bank_code} ({self.host}:{self.port})"


DEFAULT_ARREARS_LADDER_DAYS = [-3, 0, 3, 7, 14, 30]


class SchoolArrearsPolicy(TenantModel):
    """School policy for automated arrears reminder ladder (spec/06 §6, FIN-026).
    Configures reminder offsets in days relative to invoice due_date.
    Offsets: negative = before due (e.g. -3 is T-3 days), 0 = on due date (T-0),
    positive = overdue (e.g. +3, +7, +14, +30).
    """
    school = models.OneToOneField(School, on_delete=models.PROTECT, related_name='arrears_policy')
    ladder_days = models.JSONField(
        default=list,
        blank=True,
        help_text=_("Offsets in days relative to due date, e.g. [-3, 0, 3, 7, 14, 30]"),
    )
    is_active = models.BooleanField(default=True)
    payment_deep_link_base = models.CharField(
        max_length=255,
        default="/pay/{invoice_id}",
        help_text=_("Base URL or template for payment link in reminders (FIN-028)"),
    )

    class Meta:
        db_table = 'school_arrears_policies'

    def __str__(self):
        return f"{self.school.name} Arrears Policy ({'active' if self.is_active else 'inactive'})"

    def get_effective_ladder_days(self) -> list[int]:
        """Returns configured ladder days or canonical defaults per FIN-026."""
        if self.ladder_days and isinstance(self.ladder_days, list):
            try:
                return sorted([int(x) for x in self.ladder_days])
            except (ValueError, TypeError):
                pass
        return DEFAULT_ARREARS_LADDER_DAYS


class InvoiceWriteOffStatus(models.TextChoices):
    PENDING = 'PENDING', _('Menunggu Persetujuan (Pending Approval)')
    APPROVED = 'APPROVED', _('Disetujui (Approved)')
    REJECTED = 'REJECTED', _('Ditolak (Rejected)')


class InvoiceWriteOffRequest(TenantModel):
    """Foundation approval workflow for bad debt write-offs (spec/06 §6, FIN-031)."""
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name='write_off_requests')
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name='write_off_requests')
    amount = MoneyField(help_text=_("Nominal piutang yang dihapusbukukan"))
    currency = models.CharField(max_length=3, default='IDR')
    reason = models.TextField(help_text=_("Alasan penghapusbukuan piutang tak tertagih"))
    status = models.CharField(
        max_length=16,
        choices=InvoiceWriteOffStatus.choices,
        default=InvoiceWriteOffStatus.PENDING,
        db_index=True,
    )
    requested_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='submitted_write_off_requests',
    )
    approved_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='approved_write_off_requests',
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True, default='')
    journal = models.ForeignKey(
        'LedgerJournal',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='write_off_requests',
    )

    class Meta:
        db_table = 'invoice_write_off_requests'
        indexes = [
            models.Index(fields=['foundation_id', 'school_id', 'status']),
            models.Index(fields=['foundation_id', 'invoice_id']),
        ]

    def __str__(self):
        return f"Write-off #{self.id} for Invoice {self.invoice.number} ({self.status})"


# ---------------------------------------------------------------------------
# Step 8.1 — Gateway Reconciliation (FIN-024)
# ---------------------------------------------------------------------------

class SettlementBatchStatus(models.TextChoices):
    PENDING = 'PENDING', _('Menunggu Proses (Pending)')
    PROCESSING = 'PROCESSING', _('Sedang Diproses (Processing)')
    COMPLETED = 'COMPLETED', _('Selesai (Completed)')
    FAILED = 'FAILED', _('Gagal (Failed)')
    PARTIAL = 'PARTIAL', _('Sebagian Berhasil (Partial)')


class GatewaySettlementBatch(TenantModel):
    """Daily settlement batch fetched from a payment gateway (spec/06 FIN-024).

    One record per provider per settlement date.  Tracks counts of matched,
    missing, and mismatched transactions so the finance team can audit
    the daily reconciliation run without trawling individual discrepancies.
    """
    provider = models.CharField(
        max_length=32,
        db_index=True,
        help_text=_("Payment provider: MIDTRANS, XENDIT, MOCK"),
    )
    settlement_date = models.DateField(
        db_index=True,
        help_text=_("The settlement calendar date this batch covers (YYYY-MM-DD)"),
    )
    status = models.CharField(
        max_length=16,
        choices=SettlementBatchStatus.choices,
        default=SettlementBatchStatus.PENDING,
        db_index=True,
    )
    fetched_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("Timestamp when gateway data was successfully fetched"),
    )
    total_records = models.PositiveIntegerField(
        default=0,
        help_text=_("Total settlement records returned by the gateway"),
    )
    matched_count = models.PositiveIntegerField(
        default=0,
        help_text=_("Records matched to an existing Payment and auto-settled"),
    )
    missing_count = models.PositiveIntegerField(
        default=0,
        help_text=_("Gateway records with no matching Payment in EduCore"),
    )
    mismatch_count = models.PositiveIntegerField(
        default=0,
        help_text=_("Records where gateway amount differs from Payment amount"),
    )
    error_message = models.TextField(
        blank=True,
        default='',
        help_text=_("Error traceback or message if the batch run failed"),
    )
    raw_summary = models.JSONField(
        default=dict,
        blank=True,
        help_text=_("Provider-specific aggregate totals from gateway response"),
    )
    active_uniq_marker = soft_delete_uniqueness_marker()

    class Meta:
        db_table = 'gateway_settlement_batches'
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'provider', 'settlement_date', 'active_uniq_marker'],
                name='unique_foundation_provider_settlement_date',
            ),
        ]
        indexes = [
            models.Index(fields=['foundation_id', 'settlement_date']),
            models.Index(fields=['foundation_id', 'provider', 'status']),
        ]

    def __str__(self):
        return (
            f"Batch {self.provider} {self.settlement_date} "
            f"({self.status}) — {self.matched_count}/{self.total_records} matched"
        )


class DiscrepancyType(models.TextChoices):
    MISSING_IN_SYSTEM = 'MISSING_IN_SYSTEM', _('Ada di Gateway, Tidak Ada di Sistem (Missing in System)')
    AMOUNT_MISMATCH = 'AMOUNT_MISMATCH', _('Jumlah Tidak Sesuai (Amount Mismatch)')
    DUPLICATE = 'DUPLICATE', _('Duplikat (Duplicate Entry)')
    EXTRA_IN_SYSTEM = 'EXTRA_IN_SYSTEM', _('Ada di Sistem, Tidak Ada di Gateway (Extra in System)')


class DiscrepancyResolution(models.TextChoices):
    PENDING = 'PENDING', _('Menunggu Tindak Lanjut (Pending Review)')
    AUTO_SETTLED = 'AUTO_SETTLED', _('Diselesaikan Otomatis (Auto-settled)')
    MANUAL_SETTLED = 'MANUAL_SETTLED', _('Diselesaikan Manual (Manually Settled)')
    WAIVED = 'WAIVED', _('Diabaikan (Waived)')
    ESCALATED = 'ESCALATED', _('Diteruskan ke Tim Keuangan (Escalated)')


class PaymentDiscrepancy(TenantModel):
    """An individual reconciliation discrepancy between a gateway settlement
    record and the corresponding Payment in EduCore (spec/06 FIN-024).

    Created by the reconciliation service for every record that cannot be
    auto-resolved.  A finance user reviews and resolves each discrepancy via
    the admin UI or the API.
    """
    batch = models.ForeignKey(
        GatewaySettlementBatch,
        on_delete=models.CASCADE,
        related_name='discrepancies',
        help_text=_("Parent batch this discrepancy belongs to"),
    )
    payment = models.ForeignKey(
        Payment,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='discrepancies',
        help_text=_("Matched Payment record, if found (null for MISSING_IN_SYSTEM)"),
    )
    external_id = models.CharField(
        max_length=128,
        db_index=True,
        help_text=_("Gateway transaction or reference ID"),
    )
    discrepancy_type = models.CharField(
        max_length=32,
        choices=DiscrepancyType.choices,
        db_index=True,
    )
    gateway_amount = MoneyField(
        default=0,
        help_text=_("Gross amount reported by the gateway"),
    )
    gateway_fee = MoneyField(
        default=0,
        help_text=_("Fee reported by the gateway"),
    )
    gateway_net = MoneyField(
        default=0,
        help_text=_("Net amount reported by the gateway (amount - fee)"),
    )
    system_amount = MoneyField(
        null=True,
        blank=True,
        help_text=_("Amount recorded in the EduCore Payment record (if matched)"),
    )
    currency = models.CharField(max_length=3, default='IDR')
    channel = models.CharField(max_length=64, blank=True, default='')
    bank = models.CharField(max_length=32, blank=True, default='')
    gateway_settled_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("Timestamp of settlement according to the gateway"),
    )
    resolution = models.CharField(
        max_length=32,
        choices=DiscrepancyResolution.choices,
        default=DiscrepancyResolution.PENDING,
        db_index=True,
    )
    resolved_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='resolved_discrepancies',
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_notes = models.TextField(blank=True, default='')
    raw_gateway_record = models.JSONField(
        default=dict,
        blank=True,
        help_text=_("Unmodified gateway settlement record for audit trail"),
    )

    class Meta:
        db_table = 'payment_discrepancies'
        indexes = [
            models.Index(fields=['foundation_id', 'batch', 'discrepancy_type']),
            models.Index(fields=['foundation_id', 'external_id']),
            models.Index(fields=['foundation_id', 'resolution']),
        ]

    def __str__(self):
        return (
            f"Discrepancy [{self.discrepancy_type}] ext={self.external_id} "
            f"gateway={self.currency} {self.gateway_amount} ({self.resolution})"
        )


class RefundStatus(models.TextChoices):
    PENDING_APPROVAL = 'PENDING_APPROVAL', _('Menunggu Persetujuan (Pending Approval)')
    APPROVED = 'APPROVED', _('Disetujui (Approved)')
    REJECTED = 'REJECTED', _('Ditolak (Rejected)')
    EXECUTED = 'EXECUTED', _('Berhasil Dikirim (Executed)')
    CANCELLED = 'CANCELLED', _('Dibatalkan (Cancelled)')


class Refund(TenantModel):
    """Refund request, approval, and execution tracking (spec/06 §2, §7, FIN-020, FIN-032..FIN-034)."""
    payment = models.ForeignKey(
        Payment,
        on_delete=models.PROTECT,
        related_name='refunds',
        help_text=_("The settled payment being refunded"),
    )
    school = models.ForeignKey(
        School,
        on_delete=models.PROTECT,
        related_name='refunds',
    )
    student = models.ForeignKey(
        Student,
        on_delete=models.PROTECT,
        related_name='refunds',
    )
    amount = MoneyField(default=Decimal('0.00'), help_text=_("Nominal pengembalian dana"))
    currency = models.CharField(max_length=3, default='IDR')
    reason = models.TextField(help_text=_("Alasan pengembalian dana"))

    destination_bank_name = models.CharField(max_length=64, help_text=_("Nama bank penerima (e.g. BCA, MANDIRI)"))
    destination_account_number = models.CharField(max_length=64, help_text=_("Nomor rekening tujuan"))
    destination_account_holder = models.CharField(max_length=128, help_text=_("Nama pemilik rekening tujuan"))

    status = models.CharField(
        max_length=32,
        choices=RefundStatus.choices,
        default=RefundStatus.PENDING_APPROVAL,
        db_index=True,
    )
    requested_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='requested_refunds',
    )
    approved_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='approved_refunds',
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True, default='')

    payout_reference = models.CharField(
        max_length=128,
        blank=True,
        default='',
        help_text=_("Gateway payout ID atau referensi transfer bank"),
    )
    payout_proof_file = models.CharField(
        max_length=512,
        blank=True,
        default='',
        help_text=_("File path bukti transfer pengembalian dana"),
    )
    executed_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='executed_refunds',
    )
    executed_at = models.DateTimeField(null=True, blank=True)

    journal = models.ForeignKey(
        'LedgerJournal',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='refunds',
        help_text=_("Compensating double-entry ledger journal (FIN-020)"),
    )

    class Meta:
        db_table = 'refunds'
        indexes = [
            models.Index(fields=['foundation_id', 'school', 'status']),
            models.Index(fields=['foundation_id', 'payment']),
            models.Index(fields=['foundation_id', 'student']),
            models.Index(fields=['foundation_id', 'created_at']),
        ]

    def __str__(self):
        return f"Refund #{self.id} for Payment #{self.payment_id}: {self.currency} {self.amount} ({self.status})"

