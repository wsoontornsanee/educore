"""Web (session-auth HTMX) views for the Kantin & dompet console.

The page is a school-side monitor over apps.wallet data (merchants, POS
terminals, wallets, POS transactions) plus — for finance.payment.write
holders — the two money queues a bendahara works: residual-balance refunds
(WAL-026) and offline-overspend reconciliation cases (REC-015/026). Every
action is a POST that calls the same service the JSON API calls (so state
checks, ledger effects and audit stay in one place), flashes the outcome and
redirects back to the page for the same school, mirroring the gate console.
"""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _, gettext_lazy as _lazy
from rest_framework.exceptions import NotFound
from rest_framework.views import APIView

from apps.identity.console_access import StaffConsoleMixin
from apps.identity.rbac import has_permission
from educore.middleware.tenancy import tenant_context

from .models import POSTerminal, POSTerminalStatus, WalletReconciliation, WalletReconciliationStatus, WalletRefundRequest, WalletRefundStatus
from .services import (
    get_canteen_console_snapshot,
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

ACTION_PERMISSION = 'finance.payment.write'


class CanteenConsolePageView(StaffConsoleMixin, APIView):
    """GET /web/wallet/canteen/ — merchants, today's sales, wallet totals, recent POS sales,
    and (finance.payment.write) the refund and reconciliation queues."""

    def get_required_permission(self):
        return 'wallet.topup.read'

    def get(self, request):
        foundation_id, schools, school = self.console_context(request)
        snapshot = get_canteen_console_snapshot(foundation_id, school) if school else None
        can_act = bool(school) and has_permission(request.user, ACTION_PERMISSION, foundation_id, school_id=school.id)
        ctx = {'schools': schools, 'school': school, 'snapshot': snapshot, 'can_act': can_act}
        if school:
            # QR Charge entry points (spec 18): oversight page for finance/admin, tablet screen for operators.
            ctx['can_qr_manage'] = has_permission(request.user, 'finance.payment.read', foundation_id, school_id=school.id)
            if has_permission(request.user, 'wallet.topup.write', foundation_id, school_id=school.id):
                ctx['qr_terminals'] = list(POSTerminal.objects.filter(
                    foundation_id=foundation_id, merchant__school=school, merchant__qr_self_amount_enabled=True,
                    merchant__is_active=True, status=POSTerminalStatus.ACTIVE, deleted_at__isnull=True,
                ).select_related('merchant').order_by('merchant__name', 'name'))
        if can_act:
            with tenant_context(foundation_id):
                ctx.update({
                    'refunds': get_refund_queue(school),
                    'reconciliations': get_reconciliation_queue(school),
                    'exposure': get_school_reconciliation_exposure(school),
                })
        return render(request, 'pages/canteen_console_page.html', ctx)


def _friendly(exc):
    """The service's ValueError, phrased for the operator. Services raise
    CODE: english detail; only the code carries meaning for the user."""
    code = str(exc).split(':', 1)[0]
    return {
        'INVALID_STATE': _("Data ini sudah diproses sebelumnya."),
        'CONSENT_REQUIRED': _("Persetujuan wali wajib untuk mendonasikan saldo."),
        'INVALID_AMOUNT': _("Jumlah harus lebih besar dari nol."),
        'WALLET_NOT_ACTIVE': _("Dompet siswa tidak aktif."),
        'CURRENCY_MISMATCH': _("Mata uang tidak sesuai dengan dompet."),
    }.get(code, _("Tindakan tidak dapat dilakukan."))


def _parse_amount(raw):
    """A positive Decimal with at most 2 decimal places, or None. Never float."""
    try:
        amount = Decimal((raw or '').strip())
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount <= 0 or amount != amount.quantize(Decimal('0.01')):
        return None
    return amount


class _CanteenActionView(StaffConsoleMixin, APIView):
    """POST base: `finance.payment.write`, the school taken from ?school_id=
    and validated against the schools this user may write for (console_context
    404s any other), the target row looked up inside that school and this
    tenant (another school's or tenant's id is a 404). Every outcome is
    flashed and the browser sent back to the canteen page for the school."""
    http_method_names = ['post']
    model = None
    success_message = ''

    def get_required_permission(self):
        return ACTION_PERMISSION

    def find(self, foundation_id, school, pk):
        obj = self.model.objects.filter(
            id=pk, foundation_id=foundation_id, student__school_id=school.id, deleted_at__isnull=True,
        ).select_related('student', 'wallet').first()
        if obj is None:
            raise NotFound()
        return obj

    def perform(self, request, obj):
        raise NotImplementedError

    def post(self, request, pk):
        foundation_id, _schools, school = self.console_context(request)
        if school is None:
            raise NotFound()
        with tenant_context(foundation_id):
            obj = self.find(foundation_id, school, pk)
            error = self.perform(request, obj)
        if error:
            messages.error(request, error)
        else:
            messages.success(request, self.success_message)
        return redirect(f"{reverse('canteen-console-page')}?school_id={school.id}")

    def run(self, service, *args, **kwargs):
        """Call a service; return None on success or the operator-facing error."""
        try:
            service(*args, **kwargs)
        except ValueError as exc:
            return _friendly(exc)
        return None


class _RefundActionView(_CanteenActionView):
    model = WalletRefundRequest


class _ReconciliationActionView(_CanteenActionView):
    model = WalletReconciliation


class RefundPaidView(_RefundActionView):
    """POST /web/wallet/canteen/refunds/<id>/paid/ — the bendahara transferred the balance out."""
    success_message = _lazy("Refund ditandai dibayar dan dompet ditutup.")

    def perform(self, request, obj):
        post = request.POST
        return self.run(
            mark_refund_paid, obj, post.get('bank_name', '').strip(), post.get('account_number', '').strip(),
            post.get('account_holder_name', '').strip(), post.get('reference', '').strip(), actor=request.user,
        )


class RefundDonatedView(_RefundActionView):
    """POST /web/wallet/canteen/refunds/<id>/donated/ — explicit guardian consent required."""
    success_message = _lazy("Saldo didonasikan dan dompet ditutup.")

    def perform(self, request, obj):
        return self.run(mark_refund_donated, obj, request.user, request.POST.get('donation_consent') == 'on')


class ReconciliationCashView(_ReconciliationActionView):
    """POST /web/wallet/canteen/reconciliations/<id>/cash/ — the guardian paid the shortfall in cash."""
    success_message = _lazy("Pelunasan tunai dicatat.")

    def perform(self, request, obj):
        amount = _parse_amount(request.POST.get('amount'))
        if amount is None:
            return _("Jumlah tidak valid. Gunakan angka positif dengan maksimal dua desimal.")
        return self.run(
            settle_reconciliation_with_cash, obj, amount, request.POST.get('reference', '').strip(),
            actor=request.user,
        )


class ReconciliationInvoiceView(_ReconciliationActionView):
    """POST /web/wallet/canteen/reconciliations/<id>/invoice/ — bill the shortfall to the guardian now."""
    success_message = _lazy("Kekurangan ditagihkan ke wali.")

    def perform(self, request, obj):
        if obj.status != WalletReconciliationStatus.OPEN:
            return _friendly(ValueError('INVALID_STATE'))
        return self.run(invoice_reconciliation_case, obj, actor=request.user)


class ReconciliationWriteOffView(_ReconciliationActionView):
    """POST /web/wallet/canteen/reconciliations/<id>/write-off/ — forgive the shortfall (reason required)."""
    success_message = _lazy("Kasus dihapusbukukan.")

    def perform(self, request, obj):
        reason = request.POST.get('reason', '').strip()[:255]
        if not reason:
            return _("Alasan penghapusbukuan wajib diisi.")
        return self.run(write_off_reconciliation_case, obj, request.user, reason)


class ReconciliationResendNoticeView(_ReconciliationActionView):
    """POST /web/wallet/canteen/reconciliations/<id>/resend/ — re-send the guardian notice."""
    success_message = _lazy("Pemberitahuan dikirim ulang.")

    def perform(self, request, obj):
        return self.run(resend_reconciliation_notice, obj, actor=request.user)
