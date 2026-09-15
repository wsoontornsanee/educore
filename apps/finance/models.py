from decimal import Decimal
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.fields import MoneyField
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
