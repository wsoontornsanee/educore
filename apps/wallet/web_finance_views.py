"""Wallet finance queues of the web console: refund requests (WAL-026) and
wallet reconciliation cases (REC-025/026).

Reached from the Kantin & dompet page. Read = finance.payment.read, act =
finance.payment.write — the same keys as the JSON queue and action views
(WalletRefundQueueView, WalletReconciliation*View), whose services every
action calls, so state checks and audit stay in one place.

Unlike those JSON views (which look a case/refund up foundation-wide), the web
actions are ceilinged to the schools the acting user holds
finance.payment.write in, and a case outside them is a 404. Every action is a
POST that flashes a message and redirects back (POST/redirect/GET), matching
the Keuangan console.
"""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views import View

from apps.identity.console_access import ConsolePermissionMixin, accessible_school_ids, permitted_schools
from apps.identity.nav import has_staff_profile
from educore.middleware.tenancy import tenant_context

from .models import WalletReconciliation, WalletReconciliationStatus, WalletRefundRequest, WalletRefundStatus
from .services import (
    get_reconciliation_queue,
    get_refund_queue,
    get_school_reconciliation_exposure,
    invoice_reconciliation_case,
    mark_refund_donated,
    mark_refund_paid,
    resend_reconciliation_notice,
    settle_reconciliation_with_cash,
    write_off_reconciliation_case,
)

READ_PERMISSION = 'finance.payment.read'
WRITE_PERMISSION = 'finance.payment.write'


class WalletFinanceMixin(ConsolePermissionMixin):
    """Login + permission (any scope) + Staff profile. A denied user is sent
    to console home (never a raw 403 page), like the Administrasi pages."""

    def _is_allowed(self, user):
        return super()._is_allowed(user) and has_staff_profile(user, self.foundation_id)


def _selected_school(request, schools):
    raw = request.GET.get('school_id')
    if not raw:
        return schools[0] if schools else None
    try:
        school_id = int(raw)
    except ValueError:
        raise Http404
    school = next((s for s in schools if s.id == school_id), None)
    if school is None:
        raise Http404
    return school


class _QueuePageView(WalletFinanceMixin, View):
    required_permission = READ_PERMISSION
    template_name = None
    status_enum = None
    default_status = None

    def load_queue(self, school, status):
        raise NotImplementedError

    def get(self, request):
        schools = list(permitted_schools(request.user, self.foundation_id, READ_PERMISSION))
        school = _selected_school(request, schools)
        status = request.GET.get('status', self.default_status)
        if status not in self.status_enum.values:
            status = self.default_status
        context = {'schools': schools, 'school': school, 'status': status, 'status_choices': self.status_enum.choices}
        if school is not None:
            can_write = school.id in {s.id for s in permitted_schools(request.user, self.foundation_id, WRITE_PERMISSION)}
            with tenant_context(self.foundation_id):  # the queue services use tenant-scoped managers
                context.update(can_write=can_write, **self.load_queue(school, status))
        return render(request, self.template_name, context)


class RefundQueuePageView(_QueuePageView):
    """GET /web/wallet/canteen/refunds/ — residual-balance refund requests."""
    template_name = 'pages/wallet_refunds.html'
    status_enum = WalletRefundStatus
    default_status = WalletRefundStatus.PENDING

    def load_queue(self, school, status):
        return {'rows': get_refund_queue(school, status=status)}


class ReconciliationQueuePageView(_QueuePageView):
    """GET /web/wallet/canteen/reconciliations/ — offline-overspend debt cases."""
    template_name = 'pages/wallet_reconciliations.html'
    status_enum = WalletReconciliationStatus
    default_status = WalletReconciliationStatus.OPEN

    def load_queue(self, school, status):
        return {
            'rows': get_reconciliation_queue(school, status=status),
            'exposure': get_school_reconciliation_exposure(school),
        }


def _parse_amount(raw):
    """Positive Decimal with at most 2 decimal places, else None."""
    try:
        amount = Decimal((raw or '').strip().replace(',', '.'))
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount <= 0 or amount != amount.quantize(Decimal('0.01')):
        return None
    return amount


class _ActionView(WalletFinanceMixin, View):
    """POST-only: decide one row, flash the outcome, redirect back to its queue."""
    http_method_names = ['post']
    required_permission = WRITE_PERMISSION
    model = None
    redirect_name = None
    pending_status = None
    success_message = ''

    def get_object(self, pk):
        ceiling = accessible_school_ids(self.request.user, self.foundation_id, WRITE_PERMISSION)
        qs = self.model.all_tenants.filter(
            id=pk, foundation_id=self.foundation_id, deleted_at__isnull=True,
        ).select_related('student__person', 'wallet')
        if ceiling is not None:
            qs = qs.filter(student__school_id__in=ceiling)
        obj = qs.first()
        if obj is None:
            raise Http404
        return obj

    def perform(self, obj, post):
        """Run the service; raise ValueError/ValidationError with a user message to refuse."""
        raise NotImplementedError

    def post(self, request, pk):
        obj = self.get_object(pk)
        try:
            with tenant_context(self.foundation_id):
                if obj.status != self.pending_status:
                    raise ValueError('INVALID_STATE')
                self.perform(obj, request.POST)
        except (ValueError, ValidationError) as exc:
            messages.error(request, self._message(exc))
        else:
            messages.success(request, self.success_message)
        return redirect(f"{reverse(self.redirect_name)}?school_id={obj.student.school_id}")

    @staticmethod
    def _message(exc):
        text = exc.messages[0] if isinstance(exc, ValidationError) else str(exc)
        if text.startswith('INVALID_STATE'):
            return _("Item ini sudah diproses dan tidak dapat diubah lagi.")
        if text.startswith('CONSENT_REQUIRED'):
            return _("Persetujuan wali wajib dicentang untuk mendonasikan saldo.")
        return text


class RefundMarkPaidView(_ActionView):
    model = WalletRefundRequest
    redirect_name = 'wallet-refund-queue'
    pending_status = WalletRefundStatus.PENDING
    success_message = _("Pengembalian dana dicatat sebagai dibayar.")

    def perform(self, obj, post):
        mark_refund_paid(
            obj, post.get('bank_name', '').strip()[:64], post.get('account_number', '').strip()[:64],
            post.get('account_holder_name', '').strip()[:128], post.get('reference', '').strip(),
            actor=self.request.user,
        )


class RefundMarkDonatedView(_ActionView):
    model = WalletRefundRequest
    redirect_name = 'wallet-refund-queue'
    pending_status = WalletRefundStatus.PENDING
    success_message = _("Saldo didonasikan ke sekolah.")

    def perform(self, obj, post):
        mark_refund_donated(obj, self.request.user, post.get('donation_consent') == 'on')


class _ReconciliationActionView(_ActionView):
    model = WalletReconciliation
    redirect_name = 'wallet-reconciliation-queue'
    pending_status = WalletReconciliationStatus.OPEN


class ReconciliationCashView(_ReconciliationActionView):
    success_message = _("Pelunasan tunai dicatat.")

    def perform(self, obj, post):
        amount = _parse_amount(post.get('amount'))
        if amount is None:
            raise ValueError(_("Jumlah harus angka positif dengan maksimal 2 desimal."))
        settle_reconciliation_with_cash(obj, amount, post.get('reference', '').strip(), actor=self.request.user)


class ReconciliationInvoiceView(_ReconciliationActionView):
    success_message = _("Kasus ditagihkan ke tagihan siswa.")

    def perform(self, obj, post):
        invoice_reconciliation_case(obj, actor=self.request.user)


class ReconciliationWriteOffView(_ReconciliationActionView):
    success_message = _("Kasus dihapusbukukan.")

    def perform(self, obj, post):
        reason = post.get('reason', '').strip()
        if not reason:
            raise ValueError(_("Alasan wajib diisi."))
        write_off_reconciliation_case(obj, self.request.user, reason[:255])


class ReconciliationResendView(_ReconciliationActionView):
    success_message = _("Pemberitahuan dikirim ulang.")

    def perform(self, obj, post):
        resend_reconciliation_notice(obj, actor=self.request.user)
