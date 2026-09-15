from django.core.exceptions import PermissionDenied
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantModel


class BehaviourCategory(models.TextChoices):
    POSITIVE = 'POSITIVE', _('Positif')
    MINOR = 'MINOR', _('Pelanggaran Ringan')
    MAJOR = 'MAJOR', _('Pelanggaran Berat')


class CaseStatus(models.TextChoices):
    OPEN = 'OPEN', _('Terbuka')
    IN_PROGRESS = 'IN_PROGRESS', _('Dalam Penanganan')
    RESOLVED = 'RESOLVED', _('Terselesaikan')
    CLOSED = 'CLOSED', _('Ditutup')


class BehaviourPolicy(TenantModel):
    """Per-school configuration for behaviour points and escalation (spec/10 §4)."""
    school = models.OneToOneField(
        'identity.School',
        on_delete=models.CASCADE,
        related_name='behaviour_policy',
    )
    escalation_negative_threshold = models.IntegerField(
        default=-25,
        help_text=_('Ambang batas akumulasi poin negatif per semester untuk auto-buka behaviour case (LIF-009).'),
    )
    default_counsellor = models.ForeignKey(
        'identity.Staff',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='counselor_behaviour_policies',
        help_text=_('Konselor BK default yang ditugaskan saat threshold terlampaui.'),
    )
    rapor_includes_behaviour = models.BooleanField(
        default=False,
        help_text=_('Apakah ringkasan poin perilaku dicantumkan di buku rapor (LIF-014).'),
    )

    class Meta:
        db_table = 'campus_behaviour_policies'
        verbose_name = _('Kebijakan Perilaku Sekolah')
        verbose_name_plural = _('Kebijakan Perilaku Sekolah')

    def __str__(self):
        return f"BehaviourPolicy(school_id={self.school_id}, threshold={self.escalation_negative_threshold})"


class BehaviourReason(TenantModel):
    """Configurable catalogue of behaviour reasons with point values (spec/10 §2, §4, spec/09 TCH-009)."""
    school = models.ForeignKey(
        'identity.School',
        on_delete=models.CASCADE,
        related_name='behaviour_reasons',
    )
    code = models.CharField(max_length=50)
    label = models.CharField(max_length=255)
    points = models.IntegerField(
        help_text=_('Nilai poin (positif untuk kebaikan, negatif untuk pelanggaran).'),
    )
    category = models.CharField(
        max_length=20,
        choices=BehaviourCategory.choices,
        default=BehaviourCategory.MINOR,
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'campus_behaviour_reasons'
        ordering = ['category', 'code']
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'school', 'code'],
                name='unique_school_behaviour_reason_code',
            ),
        ]

    def __str__(self):
        return f"[{self.code}] {self.label} ({self.points:+d})"


class BehaviourRecord(TenantModel):
    """Recorded student behaviour instance. Immutable / non-deletable (LIF-013)."""
    school = models.ForeignKey(
        'identity.School',
        on_delete=models.CASCADE,
        related_name='behaviour_records',
    )
    student = models.ForeignKey(
        'identity.Student',
        on_delete=models.CASCADE,
        related_name='behaviour_records',
    )
    term = models.ForeignKey(
        'academic.Term',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='behaviour_records',
        help_text=_('Semester akademik saat kejadian (LIF-008).'),
    )
    reason = models.ForeignKey(
        BehaviourReason,
        on_delete=models.PROTECT,
        related_name='records',
    )
    points = models.IntegerField(
        help_text=_('Snapshot nilai poin yang diterapkan.'),
    )
    note = models.TextField(blank=True, default='')
    occurred_at = models.DateTimeField(default=timezone.now)
    recorded_by = models.ForeignKey(
        'identity.User',
        on_delete=models.PROTECT,
        related_name='recorded_behaviour_records',
    )
    acknowledged_by_guardian_at = models.DateTimeField(null=True, blank=True)
    acknowledged_by = models.ForeignKey(
        'identity.Guardian',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='acknowledged_behaviour_records',
    )
    superseded_by = models.OneToOneField(
        'self',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='supersedes_record',
        help_text=_('Rekaman pengganti jika catatan ini dikoreksi (LIF-013).'),
    )
    is_superseded = models.BooleanField(default=False)
    correction_reason = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'campus_behaviour_records'
        ordering = ['-occurred_at', '-id']

    def delete(self, *args, **kwargs):
        raise PermissionDenied("Catatan perilaku tidak dapat dihapus secara fisik (LIF-013). Gunakan perbaikan/koreksi catatan.")

    def __str__(self):
        status_suffix = " [SUPERSEDED]" if self.is_superseded else ""
        return f"BehaviourRecord({self.student_id}, {self.reason.code}, {self.points:+d}{status_suffix})"


class BehaviourCase(TenantModel):
    """Escalated discipline/counselling case (spec/10 §2, §4, LIF-009)."""
    school = models.ForeignKey(
        'identity.School',
        on_delete=models.CASCADE,
        related_name='behaviour_cases',
    )
    student = models.ForeignKey(
        'identity.Student',
        on_delete=models.CASCADE,
        related_name='behaviour_cases',
    )
    term = models.ForeignKey(
        'academic.Term',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='behaviour_cases',
    )
    opened_at = models.DateTimeField(default=timezone.now)
    trigger = models.CharField(max_length=255)
    status = models.CharField(
        max_length=20,
        choices=CaseStatus.choices,
        default=CaseStatus.OPEN,
    )
    assigned_counsellor = models.ForeignKey(
        'identity.Staff',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='assigned_behaviour_cases',
    )
    resolution = models.TextField(blank=True, default='')
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'campus_behaviour_cases'
        ordering = ['-opened_at', '-id']

    def __str__(self):
        return f"BehaviourCase({self.student_id}, {self.status}, trigger={self.trigger})"
