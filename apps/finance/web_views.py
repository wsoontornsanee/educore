"""Keuangan (finance) pages of the web console — server-rendered.

Thin views over existing finance models/services (no new domain logic). Each
page is gated by the SAME RBAC permission key its nav item declares
(apps.identity.nav.NAV_GROUPS), held at any scope, plus a linked Staff
profile — a guardian must never reach the school-side finance console even
if a role mistake grants them finance.* (same reasoning as the permission
slip console). School-scoped staff only see their own schools' records
(apps.finance.scope.staff_school_scope), matching the JSON API.

The read pages are read-only views. Write actions (resolving discrepancies,
approving/rejecting discounts and write-offs, cash entry) are POST-only views
(FinanceActionView subclasses) that call the existing services, flash a
message and redirect back to the page. Approve/reject authority (foundation
admin) is enforced by the services, not by the views.
"""
import logging
import re
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import F, Q, Sum
from django.shortcuts import get_object_or_404, redirect  # noqa: F401
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView, View

from apps.finance.models import (
    DiscrepancyResolution,
    Discount,
    DiscountStatus,
    GatewaySettlementBatch,
    Invoice,
    InvoiceStatus,
    InvoiceWriteOffRequest,
    InvoiceWriteOffStatus,
    Payment,
    PaymentDiscrepancy,
    PaymentStatus,
)
from apps.finance.scope import staff_school_scope
from apps.finance.services.ar_aging import AGING_BUCKETS, get_ar_aging_report
from apps.finance.services.invoicing import (
    approve_discount,
    approve_invoice_write_off,
    reject_discount,
    reject_invoice_write_off,
)
from apps.finance.services.payments import record_cash_payment
from apps.finance.services.reconciliation import resolve_discrepancy
from apps.identity.models import Student
from apps.identity.nav import has_staff_profile
from apps.identity.rbac import has_permission_in_any_scope, is_foundation_admin
from educore.middleware.tenancy import get_current_foundation_id, tenant_context

logger = logging.getLogger(__name__)

PAGE_SIZE = 25
RECENT_PAYMENTS = 20
TOP_DEBTORS = 20
DISCREPANCY_LIMIT = 200
AGING_BUCKET_LABELS = {
    'CURRENT': _('Belum jatuh tempo'),
    '0_30': _('0–30 hari'),
    '31_60': _('31–60 hari'),
    '61_90': _('61–90 hari'),
    '90_PLUS': _('Lebih dari 90 hari'),
}
PERIOD_RE = re.compile(r'^\d{4}-\d{2}$')
OPEN_INVOICE_STATUSES = [InvoiceStatus.ISSUED, InvoiceStatus.PARTIALLY_PAID]

# status -> (status-badge component code, Indonesian label)
INVOICE_BADGES = {
    InvoiceStatus.DRAFT: ('DRAF', _('Konsep')),
    InvoiceStatus.ISSUED: ('TERBIT', _('Terbit')),
    InvoiceStatus.PARTIALLY_PAID: ('SEBAGIAN', _('Sebagian')),
    InvoiceStatus.PAID: ('LUNAS', _('Lunas')),
    InvoiceStatus.CANCELLED: ('BATAL', _('Dibatalkan')),
    InvoiceStatus.WRITTEN_OFF: ('BATAL', _('Dihapusbukukan')),
}
OVERDUE_BADGE = ('JATUH_TEMPO', _('Jatuh tempo'))
PAYMENT_BADGES = {
    PaymentStatus.PENDING: ('PROSES', _('Menunggu')),
    PaymentStatus.PENDING_VERIFICATION: ('PROSES', _('Menunggu verifikasi')),
    PaymentStatus.SETTLED: ('LUNAS', _('Berhasil')),
    PaymentStatus.FAILED: ('GAGAL', _('Gagal')),
    PaymentStatus.CANCELLED: ('BATAL', _('Dibatalkan')),
    PaymentStatus.REJECTED: ('GAGAL', _('Ditolak')),
}


class FinanceConsoleGateMixin(LoginRequiredMixin):
    """Login + permission (any scope) + Staff-profile gate shared by every
    finance console view, read or write. Failing the gate is a 403 (the nav
    already hides items from users who'd fail it, so this is only reachable
    by URL). After dispatch passes, `foundation_id` and `school_ids` (None =
    unrestricted, else the caller's school ids) are set."""
    required_permission = None

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            self.foundation_id = get_current_foundation_id() or getattr(request.user, 'foundation_id', None)
            if not (
                self.foundation_id
                and has_permission_in_any_scope(request.user, self.required_permission, self.foundation_id)
                and has_staff_profile(request.user, self.foundation_id)
            ):
                raise PermissionDenied
            self.school_ids = staff_school_scope(request.user, self.foundation_id)
        return super().dispatch(request, *args, **kwargs)

    def scoped(self, model, school_field='school_id'):
        """Undeleted rows of `model` for this foundation, limited to the
        caller's schools (school_field is the ORM path to the school id)."""
        qs = model.all_tenants.filter(foundation_id=self.foundation_id, deleted_at__isnull=True)
        if self.school_ids is not None:
            qs = qs.filter(**{f'{school_field}__in': self.school_ids})
        return qs


class FinanceConsoleView(FinanceConsoleGateMixin, TemplateView):
    """Read pages: subclasses build their context in `build_context`."""
    page_title = ''

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['page_title'] = self.page_title
        ctx.update(self.build_context())
        return ctx

    def build_context(self):
        raise NotImplementedError


class FinanceActionView(FinanceConsoleGateMixin, View):
    """POST-only write action: look up the (scoped) object, call an existing
    service inside the tenant context, flash the outcome, redirect back.

    Subclasses set `required_permission` and implement `get_object` (default:
    no object), `perform` (returns the success message; raises ValueError /
    ValidationError / PermissionDenied for user-facing failures) and
    `redirect_url`. A service PermissionDenied is shown as a flash error, not
    a 403 page: the user was allowed to reach the view."""
    http_method_names = ['post']

    def get_object(self, **url_kwargs):
        return None

    def perform(self, obj):
        raise NotImplementedError

    def redirect_url(self, obj):
        raise NotImplementedError

    def post(self, request, *args, **kwargs):
        obj = self.get_object(**kwargs)
        try:
            with transaction.atomic(), tenant_context(self.foundation_id):
                message = self.perform(obj)
        except PermissionDenied:
            messages.error(request, _('Anda tidak berwenang melakukan tindakan ini.'))
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
        except ValueError as exc:
            messages.error(request, str(exc))
        except Exception:
            # The atomic block already rolled back. Never leak the raw error
            # text (may contain data) to the page; the traceback is logged.
            logger.exception('finance console action failed: %s', type(self).__name__)
            messages.error(request, _('Terjadi kesalahan saat memproses permintaan. Silakan coba lagi.'))
        else:
            messages.success(request, message)
        return redirect(self.redirect_url(obj))


class BillingConsoleView(FinanceConsoleView):
    """Tagihan & pembayaran: filterable invoice list + latest payments."""
    template_name = 'pages/finance_billing.html'
    page_title = _('Tagihan & pembayaran')
    required_permission = 'finance.invoice.read'

    def build_context(self):
        params = self.request.GET
        status = params.get('status', '')
        period = params.get('period', '').strip()
        q = params.get('q', '').strip()

        invoices = self.scoped(Invoice).select_related('school', 'student__person').order_by('-issue_date', '-id')
        if status in InvoiceStatus.values:
            invoices = invoices.filter(status=status)
        else:
            status = ''
        if PERIOD_RE.match(period):
            invoices = invoices.filter(period=period)
        else:
            period = ''
        if q:
            invoices = invoices.filter(
                Q(number__icontains=q) | Q(student__person__full_name__icontains=q) | Q(student__nis__icontains=q)
            )

        # Totals are computed server-side (clients never sum money), one row
        # per currency since invoices can be in different currencies.
        totals = list(
            invoices.order_by().values('currency').annotate(
                billed=Sum('total'),
                collected=Sum('paid'),
                outstanding=Sum(F('total') - F('paid'), filter=Q(status__in=OPEN_INVOICE_STATUSES)),
            ).order_by('currency')
        )

        page = Paginator(invoices, PAGE_SIZE).get_page(params.get('page'))
        today = timezone.localdate()
        rows = []
        for invoice in page.object_list:
            overdue = invoice.status in OPEN_INVOICE_STATUSES and invoice.due_date < today
            badge, label = OVERDUE_BADGE if overdue else INVOICE_BADGES[invoice.status]
            rows.append({'invoice': invoice, 'badge': badge, 'badge_label': label})

        payments = []
        for payment in self.scoped(Payment).select_related('student__person').order_by('-paid_at', '-id')[:RECENT_PAYMENTS]:
            badge, label = PAYMENT_BADGES[payment.status]
            payments.append({'payment': payment, 'badge': badge, 'badge_label': label})

        return {
            'rows': rows,
            'page': page,
            'totals': totals,
            'payments': payments,
            'can_record_cash': has_permission_in_any_scope(self.request.user, 'finance.invoice.write', self.foundation_id),
            'status_choices': [(value, INVOICE_BADGES[value][1]) for value in InvoiceStatus.values],
            'filters': {'status': status, 'period': period, 'q': q},
            'filter_querystring': '&'.join(
                f'{key}={value}' for key, value in (('status', status), ('period', period), ('q', q)) if value
            ),
        }


AMOUNT_RE = re.compile(r'[0-9]{1,16}(\.[0-9]{1,2})?')


class CashPaymentView(FinanceActionView):
    """POST: record a cash payment for a student found by NIS within the
    caller's schools. The service allocates to open invoices oldest-first,
    posts the ledger journal and audits. A NIS matching more than one student
    (possible across schools) is refused rather than guessed."""
    required_permission = 'finance.invoice.write'

    def redirect_url(self, obj):
        return reverse('finance-console-billing')

    def _parse_amount(self, raw):
        # Strict ASCII plain-decimal grammar checked BEFORE Decimal(): rejects
        # exponents, NaN/Infinity, underscores, non-ASCII digits, signs, more
        # than two decimals and anything beyond DECIMAL(18,2) (16 integer digits).
        text = raw.strip()
        if not AMOUNT_RE.fullmatch(text):
            raise ValueError(_('Jumlah tidak valid.'))
        amount = Decimal(text)
        if amount <= 0:
            raise ValueError(_('Jumlah harus lebih dari nol dengan maksimal dua desimal.'))
        return amount

    def _find_student(self, nis):
        students = Student.all_tenants.filter(
            foundation_id=self.foundation_id, deleted_at__isnull=True, nis=nis,
        ).select_related('school')
        if self.school_ids is not None:
            students = students.filter(school_id__in=self.school_ids)
        matches = list(students[:2])
        if not matches:
            raise ValueError(_('Siswa dengan NIS tersebut tidak ditemukan.'))
        if len(matches) > 1:
            raise ValueError(_('NIS cocok dengan lebih dari satu siswa. Gunakan API keuangan untuk memilih siswa.'))
        return matches[0]

    def perform(self, obj):
        amount = self._parse_amount(self.request.POST.get('amount', ''))
        student = self._find_student(self.request.POST.get('nis', '').strip())
        payment = record_cash_payment(
            school=student.school,
            student=student,
            amount=amount,
            received_by=self.request.user,
            notes=self.request.POST.get('notes', '').strip()[:255],
        )
        return _('Pembayaran tunai tercatat. No. kwitansi: %(receipt)s') % {'receipt': payment.receipt_number}


class ReconciliationConsoleView(FinanceConsoleView):
    """Rekonsiliasi: gateway settlement batches and the selected batch's
    discrepancies. Batches are foundation-level (one per provider per day),
    not per-school, exactly like the JSON reconciliation API."""
    template_name = 'pages/finance_reconciliation.html'
    page_title = _('Rekonsiliasi')
    required_permission = 'finance.payment.read'

    def build_context(self):
        batches = GatewaySettlementBatch.all_tenants.filter(
            foundation_id=self.foundation_id, deleted_at__isnull=True,
        ).order_by('-settlement_date', 'provider')
        page = Paginator(batches, PAGE_SIZE).get_page(self.request.GET.get('page'))

        selected, discrepancies = None, []
        batch_id = self.request.GET.get('batch', '')
        if batch_id.isdigit():
            selected = batches.filter(id=int(batch_id)).first()
        if selected:
            discrepancies = list(
                PaymentDiscrepancy.all_tenants.filter(
                    foundation_id=self.foundation_id, batch=selected, deleted_at__isnull=True,
                ).order_by('-id')[:DISCREPANCY_LIMIT]
            )
        return {
            'page': page, 'selected': selected, 'discrepancies': discrepancies,
            'can_resolve': has_permission_in_any_scope(self.request.user, 'finance.payment.write', self.foundation_id),
        }


class DiscrepancyResolveView(FinanceActionView):
    """POST: settle / waive / escalate a pending gateway discrepancy. Batches
    are foundation-level (not per-school), same as the read page and API."""
    required_permission = 'finance.payment.write'
    ALLOWED_RESOLUTIONS = (
        DiscrepancyResolution.MANUAL_SETTLED,
        DiscrepancyResolution.WAIVED,
        DiscrepancyResolution.ESCALATED,
    )

    def get_object(self, pk):
        return get_object_or_404(
            PaymentDiscrepancy.all_tenants.filter(foundation_id=self.foundation_id, deleted_at__isnull=True),
            pk=pk,
        )

    def perform(self, discrepancy):
        resolution = self.request.POST.get('resolution', '')
        if resolution not in self.ALLOWED_RESOLUTIONS:
            raise ValueError(_('Pilihan penyelesaian tidak valid.'))
        resolve_discrepancy(
            discrepancy_id=discrepancy.id,
            resolution=resolution,
            resolved_by=self.request.user,
            foundation_id=self.foundation_id,
            notes=self.request.POST.get('notes', '').strip()[:255],
        )
        return _('Selisih berhasil diperbarui.')

    def redirect_url(self, discrepancy):
        return f"{reverse('finance-console-reconciliation')}?batch={discrepancy.batch_id}#discrepancies"


class ReceivablesConsoleView(FinanceConsoleView):
    """Piutang & keringanan: AR aging (existing FIN-029 report service),
    biggest debtors, and discounts/write-offs awaiting approval."""
    template_name = 'pages/finance_receivables.html'
    page_title = _('Piutang & keringanan')
    required_permission = 'finance.invoice.read'

    def build_context(self):
        report = get_ar_aging_report(
            foundation_id=self.foundation_id,
            school_ids=self.school_ids,
        )
        debtors = sorted(report['by_student'], key=lambda s: Decimal(s['total_outstanding']), reverse=True)[:TOP_DEBTORS]

        pending_discounts = self.scoped(Discount, 'student__school_id').filter(
            status=DiscountStatus.PENDING_APPROVAL,
        ).select_related('student__person', 'fee_type').order_by('-created_at')
        pending_write_offs = self.scoped(InvoiceWriteOffRequest).filter(
            status=InvoiceWriteOffStatus.PENDING,
        ).select_related('invoice__student__person').order_by('-created_at')

        return {
            'report': report,
            'bucket_rows': [(AGING_BUCKET_LABELS[bucket], report['summary'][bucket]) for bucket in AGING_BUCKETS],
            'debtors': debtors,
            'pending_discounts': list(pending_discounts),
            'pending_write_offs': list(pending_write_offs),
            'can_decide': (
                is_foundation_admin(self.request.user, self.foundation_id)
                and has_permission_in_any_scope(self.request.user, 'finance.invoice.write', self.foundation_id)
            ),
        }


class _DecisionView(FinanceActionView):
    """Approve/reject pair. `decision` is fixed per URL via as_view(decision=...).
    Approval and rejection authority (foundation admin) is enforced by the
    services; the buttons are only *shown* to foundation admins."""
    required_permission = 'finance.invoice.write'
    decision = None  # 'approve' | 'reject'

    def redirect_url(self, obj):
        return reverse('finance-console-receivables')


class DiscountDecisionView(_DecisionView):
    def get_object(self, pk):
        return get_object_or_404(self.scoped(Discount, 'student__school_id'), pk=pk)

    def perform(self, discount):
        reason = self.request.POST.get('reason', '').strip()
        if self.decision == 'approve':
            approve_discount(discount, self.request.user, reason=reason)
            return _('Keringanan disetujui.')
        reject_discount(discount, self.request.user, reason=reason)
        return _('Keringanan ditolak.')


class WriteOffDecisionView(_DecisionView):
    def get_object(self, pk):
        return get_object_or_404(self.scoped(InvoiceWriteOffRequest), pk=pk)

    def perform(self, write_off):
        if self.decision == 'approve':
            approve_invoice_write_off(request_obj=write_off, user=self.request.user)
            return _('Penghapusbukuan disetujui.')
        notes = self.request.POST.get('notes', '').strip()
        reject_invoice_write_off(request_obj=write_off, user=self.request.user, notes=notes)
        return _('Penghapusbukuan ditolak.')
