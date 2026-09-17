"""Partner & Vendor Integration API models (spec/18-partner-vendor-api.md).

Credential, webhook, event, and (minimal) payroll run storage for the
partner-facing REST surface. Every model is foundation-scoped (PVA-002):
a partner credential never spans foundations.
"""
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.fields import MoneyField, soft_delete_uniqueness_marker
from apps.core.models import TenantModel


class PartnerApiKey(TenantModel):
    """Key-id/secret credential issued to one partner for one foundation (§2).

    The secret is stored Fernet-encrypted at rest and is only ever returned
    in plaintext once, in the response to the issue/rotate call that created
    it — it is never transmittable again afterwards (the partner authenticates
    with an HMAC derived from it).

    Rotation (PVA-012): at most 2 simultaneously ACTIVE keys per foundation.
    On rotation the superseded key stays readable until `read_only_at`
    (issue + 23 days) and stops working entirely at `expires_at`
    (issue + 30 days).
    """

    STATUS_ACTIVE = 'ACTIVE'
    STATUS_REVOKED = 'REVOKED'
    STATUS_CHOICES = [(STATUS_ACTIVE, 'Active'), (STATUS_REVOKED, 'Revoked')]

    # PVA-001/PVA-011: only these four domains are exposed; roster.pii is an
    # add-on grant, never implied by roster.read (PVA-030).
    SCOPE_ROSTER_READ = 'roster.read'
    SCOPE_ROSTER_PII = 'roster.pii'
    SCOPE_FINANCE_READ = 'finance.read'
    SCOPE_PAYROLL_READ = 'payroll.read'
    SCOPE_PAYROLL_WRITE = 'payroll.write'
    SCOPE_ATTENDANCE_READ = 'attendance.read'

    ALLOWED_SCOPES = (
        SCOPE_ROSTER_READ,
        SCOPE_ROSTER_PII,
        SCOPE_FINANCE_READ,
        SCOPE_PAYROLL_READ,
        SCOPE_PAYROLL_WRITE,
        SCOPE_ATTENDANCE_READ,
    )

    label = models.CharField(max_length=128, help_text=_("Human-readable partner/integration name"))
    key_id = models.CharField(max_length=64, unique=True, db_index=True, help_text=_("Public key identifier, e.g. ak_live_7Qp2R9"))
    secret_encrypted = models.TextField(help_text=_("Fernet-encrypted HMAC secret; never returned after issue time"))
    scopes = models.JSONField(default=list, help_text=_("Granted scope strings, subset of ALLOWED_SCOPES"))
    school_ids = models.JSONField(default=list, help_text=_("School IDs in scope; empty list = every school in the foundation"))
    ip_allowlist = models.JSONField(default=list, help_text=_("Optional CIDR/address list; empty = unrestricted (REQUIRED for payroll.write, PVA-013)"))

    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_ACTIVE, db_index=True)
    rotated_from_id = models.BigIntegerField(null=True, blank=True, help_text=_("key_id (numeric pk) of the key this one supersedes"))
    read_only_at = models.DateTimeField(null=True, blank=True, help_text=_("PVA-012: superseded key becomes read-only (day 23 of overlap)"))
    expires_at = models.DateTimeField(null=True, blank=True, help_text=_("PVA-012: superseded key stops working (day 30 of overlap)"))
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'partner_api_keys'
        indexes = [
            models.Index(fields=['foundation_id', 'status']),
        ]

    def __str__(self):
        return f"{self.key_id} ({self.label}) - {self.status}"

    @property
    def is_active(self) -> bool:
        now = timezone.now()
        return (
            self.status == self.STATUS_ACTIVE
            and not self.is_deleted
            and (self.expires_at is None or self.expires_at > now)
        )

    @property
    def is_read_only(self) -> bool:
        return self.read_only_at is not None and self.read_only_at <= timezone.now()

    def has_scope(self, scope: str) -> bool:
        return scope in (self.scopes or [])


class PartnerWebhookEndpoint(TenantModel):
    """A partner's HTTPS receiving endpoint (§6).

    A partner registers exactly one endpoint per environment; registering a
    new one for the same (foundation, environment) deactivates the previous.
    Deliveries are signed with `signing_secret_encrypted`, which is distinct
    from any API key secret.
    """

    url = models.URLField(max_length=500, help_text=_("Partner's HTTPS endpoint (https:// enforced)"))
    environment = models.CharField(max_length=16, default='LIVE', help_text=_("LIVE or TEST; one active endpoint per environment"))
    signing_secret_encrypted = models.TextField(help_text=_("Fernet-encrypted secret used to sign outgoing webhook deliveries"))
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = 'partner_webhook_endpoints'
        indexes = [
            models.Index(fields=['foundation_id', 'environment', 'is_active']),
        ]

    def __str__(self):
        return f"{self.url} ({self.environment}, active={self.is_active})"


class PartnerEvent(TenantModel):
    """Outbound integration event (§6).

    Webhook is the primary delivery channel; every event — delivered or not —
    stays queryable via GET /partner/events?since= so nothing is silently
    lost. A delivery that fails 5 retries (exponential backoff, <= 6 hours)
    is marked EXHAUSTED but remains pollable.
    """

    STATUS_PENDING = 'PENDING'
    STATUS_DELIVERED = 'DELIVERED'
    STATUS_EXHAUSTED = 'EXHAUSTED'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending delivery'),
        (STATUS_DELIVERED, 'Delivered'),
        (STATUS_EXHAUSTED, 'Retries exhausted (pollable only)'),
    ]

    event_type = models.CharField(max_length=64, db_index=True, help_text=_("e.g. finance.payment.settled"))
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    attempts = models.PositiveIntegerField(default=0, help_text=_("Failed delivery attempts so far"))
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    next_attempt_at = models.DateTimeField(null=True, blank=True, db_index=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'partner_events'
        indexes = [
            models.Index(fields=['foundation_id', 'event_type']),
            models.Index(fields=['foundation_id', 'status', 'next_attempt_at']),
        ]

    def __str__(self):
        return f"#{self.id} {self.event_type} ({self.status})"


class PayrollRun(TenantModel):
    """Minimal payroll run store backing the partner payroll endpoints (§7).

    No payroll domain module exists anywhere in this codebase (spec/11 sketches
    the tables; apps/foundation/approvals.py's payroll branch is a no-op).
    spec/18 §7 (PVA-032) explicitly defers the state model to this
    implementation task, so the deliberate minimal model here is:

        DRAFT -> APPROVED -> ACKNOWLEDGED
                     |
                     +--> (future) PAID, owned by the eventual HR/payroll module

    - APPROVED is set by a foundation admin on /api/v1/partner-admin/payroll/runs/:id/approve;
      it emits `payroll.run.approved` and opens the acknowledgement window
      (acknowledge_deadline = approval + EDUCORE_PARTNER_PAYROLL_ACK_DAYS).
    - ACKNOWLEDGED is set by a partner key with payroll.write on
      POST /partner/payroll/runs/:id/acknowledge.
    - Anything not in APPROVED (or past the deadline) returns 409
      PAYROLL_RUN_LOCKED — the acknowledgement window has closed.
    """

    STATUS_DRAFT = 'DRAFT'
    STATUS_APPROVED = 'APPROVED'
    STATUS_ACKNOWLEDGED = 'ACKNOWLEDGED'
    STATUS_PAID = 'PAID'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'),
        (STATUS_APPROVED, 'Approved - awaiting partner acknowledgement'),
        (STATUS_ACKNOWLEDGED, 'Acknowledged by partner'),
        (STATUS_PAID, 'Paid (reserved for future payroll module)'),
    ]

    school = models.ForeignKey('identity.School', on_delete=models.PROTECT, related_name='payroll_runs')
    period = models.CharField(max_length=7, db_index=True, help_text=_("Payroll period YYYY-MM (e.g. 2026-08)"))
    currency = models.CharField(max_length=3, default='IDR')
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_DRAFT, db_index=True)

    gross_amount = MoneyField(default=0)
    deduction_amount = MoneyField(default=0)
    net_amount = MoneyField(default=0)

    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.CharField(max_length=64, null=True, blank=True)
    acknowledge_deadline = models.DateTimeField(null=True, blank=True, help_text=_("Approval + acknowledgement window; past this, ack is locked"))
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    acknowledged_by = models.CharField(max_length=64, null=True, blank=True, help_text=_("Partner key_id that acknowledged"))

    active_uniq_marker = soft_delete_uniqueness_marker()

    class Meta:
        db_table = 'payroll_runs'
        constraints = [
            models.UniqueConstraint(
                fields=['foundation_id', 'school', 'period', 'active_uniq_marker'],
                name='unique_active_payroll_run_per_school_period',
            ),
        ]
        indexes = [
            models.Index(fields=['foundation_id', 'school', 'status']),
            models.Index(fields=['foundation_id', 'period']),
        ]

    def __str__(self):
        return f"PayrollRun {self.period} - {self.school.name} ({self.status})"

    @property
    def acknowledgement_locked(self) -> bool:
        """True once the run has moved past (or out of) the ack window (PVA-032)."""
        if self.status != self.STATUS_APPROVED:
            return True
        deadline = self.acknowledge_deadline
        return deadline is not None and deadline <= timezone.now()


class PayrollRunLine(TenantModel):
    """Per-staff line item of a payroll run (money: DECIMAL(18,2) + run currency)."""

    run = models.ForeignKey(PayrollRun, on_delete=models.PROTECT, related_name='lines')
    staff_id = models.BigIntegerField(db_index=True, help_text=_("apps.identity.Staff ID"))
    staff_name = models.CharField(max_length=128)
    gross_amount = MoneyField(default=0)
    deduction_amount = MoneyField(default=0)
    net_amount = MoneyField(default=0)

    class Meta:
        db_table = 'payroll_run_lines'
        constraints = [
            models.UniqueConstraint(
                fields=['run', 'staff_id'],
                name='unique_staff_per_payroll_run',
            ),
        ]
        indexes = [
            models.Index(fields=['foundation_id', 'run']),
        ]

    def __str__(self):
        return f"{self.run_id} - {self.staff_name}: {self.net_amount}"
