"""Web console for canteen QR Charge (spec 18): school-admin settings and oversight page,
and the operator's terminal screen.

Same conventions as the Kantin console: session-auth pages gated by StaffConsoleMixin, every
action a plain POST that calls the service the JSON API calls, flash + redirect. The terminal
screen is the one interactive page: it mints the QR, polls for the student's charge and lets
the operator void a wrong amount (QRS-014, QRS-025).
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _, gettext_lazy as _lazy
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.identity.console_access import StaffConsoleMixin
from apps.identity.rbac import has_permission
from educore.middleware.tenancy import tenant_context

from .models import (
    Merchant,
    POSEntryMode,
    POSQRSession,
    POSTerminal,
    POSTerminalStatus,
    POSTransaction,
    QRDispute,
    QRDisputeStatus,
)
from .qr_charge import (
    QR_SESSION_TTL_SECONDS,
    QRChargeError,
    cancel_qr_session,
    create_qr_session,
    get_qr_session_result,
    render_qr_svg,
    set_merchant_qr_charge,
)
from .qr_oversight import (
    QRDisputeError,
    clear_merchant_qr_flag,
    get_underpayment_signals,
    resolve_qr_dispute,
)
from .services import VoidWindowExpiredError, void_pos_transaction

ADMIN_PERMISSION = 'school_config.write'


def _qr_page_url(school):
    return f"{reverse('canteen-qr-page')}?school_id={school.id}"


class CanteenQRPageView(StaffConsoleMixin, APIView):
    """GET /web/wallet/canteen/qr/ — per-merchant QR switch and terminals, open disputes,
    and the daily underpayment signals. Viewing needs finance.payment.read; acting on the
    switch, the flag or a dispute needs school_config.write."""

    def get_required_permission(self):
        return 'finance.payment.read'

    def get(self, request):
        foundation_id, schools, school = self.console_context(request)
        ctx = {'schools': schools, 'school': school, 'merchants': []}
        if school:
            raw_date = request.query_params.get('date')
            day = None
            if raw_date:
                try:
                    day = datetime.strptime(raw_date, '%Y-%m-%d').date()
                except ValueError:
                    messages.error(request, _("Tanggal tidak valid (YYYY-MM-DD)."))
            with tenant_context(foundation_id):
                merchants = list(Merchant.objects.filter(
                    foundation_id=foundation_id, school=school, deleted_at__isnull=True,
                ).order_by('name'))
                for merchant in merchants:
                    merchant.terminals_list = list(merchant.terminals.filter(
                        foundation_id=foundation_id, deleted_at__isnull=True, status=POSTerminalStatus.ACTIVE,
                    ))
                    merchant.signals = get_underpayment_signals(merchant, day) if merchant.qr_self_amount_enabled else None
                disputes = list(QRDispute.objects.filter(
                    foundation_id=foundation_id, merchant__in=merchants, status=QRDisputeStatus.OPEN, deleted_at__isnull=True,
                ).select_related('merchant', 'student', 'student__person', 'pos_transaction').order_by('created_at'))
            ctx.update({
                'merchants': merchants,
                'disputes': disputes,
                'cap': school.qr_self_amount_max,
                'currency': school.base_currency,
                'can_admin': has_permission(request.user, ADMIN_PERMISSION, foundation_id, school_id=school.id),
                'signals_date': day.isoformat() if day else '',
            })
        return render(request, 'pages/canteen_qr_page.html', ctx)


class _QRAdminActionView(StaffConsoleMixin, APIView):
    """POST base for school-admin actions: school_config.write, target looked up inside the
    selected school and tenant (another school's or tenant's id is a 404)."""
    http_method_names = ['post']

    def get_required_permission(self):
        return ADMIN_PERMISSION

    def find(self, foundation_id, school, pk):
        raise NotImplementedError

    def perform(self, request, obj):
        raise NotImplementedError

    success_message = ''

    def post(self, request, pk):
        foundation_id, _schools, school = self.console_context(request)
        if school is None:
            raise NotFound()
        with tenant_context(foundation_id):
            obj = self.find(foundation_id, school, pk)
            try:
                self.perform(request, obj)
            except (QRChargeError, QRDisputeError) as exc:
                messages.error(request, exc.message)
            except ValueError:  # e.g. WalletNotActiveError when the refund target is closed
                messages.error(request, _("Tindakan tidak dapat dilakukan."))
            else:
                messages.success(request, self.success_message)
        return redirect(_qr_page_url(school))


class _MerchantAction(_QRAdminActionView):
    def find(self, foundation_id, school, pk):
        merchant = Merchant.objects.filter(
            id=pk, foundation_id=foundation_id, school_id=school.id, deleted_at__isnull=True,
        ).first()
        if merchant is None:
            raise NotFound()
        return merchant


class MerchantQRSwitchView(_MerchantAction):
    """POST /web/wallet/canteen/qr/merchants/<id>/switch/ — enable (with acknowledgement) or disable."""

    def perform(self, request, merchant):
        enabled = request.POST.get('enabled') == '1'
        set_merchant_qr_charge(merchant, enabled, request.POST.get('acknowledged') == 'on', request.user)
        self.success_message = _("QR Charge diaktifkan.") if enabled else _("QR Charge dinonaktifkan.")


class MerchantQRFlagClearView(_MerchantAction):
    """POST /web/wallet/canteen/qr/merchants/<id>/flag/clear/ — admin review closes the dispute flag."""
    success_message = _lazy("Tanda sanggahan ditutup.")

    def perform(self, request, merchant):
        clear_merchant_qr_flag(merchant, request.user)


class QRDisputeResolveWebView(_QRAdminActionView):
    """POST /web/wallet/canteen/qr/disputes/<id>/resolve/ — uphold (full or partial) or reject."""

    def find(self, foundation_id, school, pk):
        dispute = QRDispute.objects.filter(
            id=pk, foundation_id=foundation_id, merchant__school_id=school.id, deleted_at__isnull=True,
        ).select_related('merchant').first()
        if dispute is None:
            raise NotFound()
        return dispute

    def perform(self, request, dispute):
        outcome = request.POST.get('outcome')
        raw = (request.POST.get('refund_amount') or '').strip()
        amount = None
        if raw:
            try:
                amount = Decimal(raw)
            except InvalidOperation:
                raise QRDisputeError('DISPUTE_INVALID_AMOUNT', _("Jumlah pengembalian tidak valid."))
        resolve_qr_dispute(dispute, outcome, request.user, request.POST.get('note', '').strip()[:500], amount)
        self.success_message = _("Sanggahan dikabulkan.") if outcome == QRDisputeStatus.UPHELD else _("Sanggahan ditolak.")


# --- operator terminal screen ------------------------------------------------------------------

class _TerminalMixin(StaffConsoleMixin):
    """Operator views: wallet.topup.write, and the terminal's merchant must belong to a school
    this user may operate (any other terminal, school or tenant is a 404)."""

    def get_required_permission(self):
        return 'wallet.topup.write'

    def terminal_or_404(self, request, terminal_id):
        foundation_id, _schools, _school = self.console_context(request)
        allowed = {s.id for s in _schools}
        terminal = POSTerminal.objects.filter(
            id=terminal_id, foundation_id=foundation_id, deleted_at__isnull=True,
        ).select_related('merchant', 'merchant__school').first()
        if terminal is None or terminal.merchant.school_id not in allowed:
            raise NotFound()
        return foundation_id, terminal

    def session_or_404(self, foundation_id, terminal, session_id):
        session = POSQRSession.objects.filter(
            id=session_id, foundation_id=foundation_id, terminal=terminal, deleted_at__isnull=True,
        ).first()
        if session is None:
            raise NotFound()
        return session


class QRTerminalPageView(_TerminalMixin, APIView):
    """GET /web/wallet/canteen/qr/terminal/<terminal_id>/ — the tablet screen the student scans."""

    def get(self, request, terminal_id):
        foundation_id, terminal = self.terminal_or_404(request, terminal_id)
        return render(request, 'pages/canteen_qr_terminal.html', {
            'terminal': terminal, 'merchant': terminal.merchant, 'ttl': QR_SESSION_TTL_SECONDS,
            'cap': terminal.merchant.school.qr_self_amount_max, 'currency': terminal.merchant.school.base_currency,
            'enabled': terminal.merchant.qr_self_amount_enabled and terminal.status == POSTerminalStatus.ACTIVE,
        })


class QRTerminalSessionView(_TerminalMixin, APIView):
    """POST /web/wallet/canteen/qr/terminal/<id>/session/ — mint (or regenerate) the QR."""
    http_method_names = ['post']

    def post(self, request, terminal_id):
        foundation_id, terminal = self.terminal_or_404(request, terminal_id)
        with tenant_context(foundation_id):
            previous = request.data.get('previous_session_id')
            if previous:
                old = POSQRSession.objects.filter(
                    id=previous, foundation_id=foundation_id, terminal=terminal, deleted_at__isnull=True,
                ).first()
                if old:
                    cancel_qr_session(old)
            try:
                minted = create_qr_session(terminal)
            except QRChargeError as exc:
                return Response({'error': exc.code, 'message': exc.message}, status=400)
        session = minted['session']
        return Response({
            'session_id': session.id, 'qr_svg': render_qr_svg(minted['token']), 'expires_at': session.expires_at,
        }, status=201)


class QRTerminalCancelView(_TerminalMixin, APIView):
    """POST /web/wallet/canteen/qr/terminal/<id>/session/<sid>/cancel/ — operator backs out; the QR stops resolving."""
    http_method_names = ['post']

    def post(self, request, terminal_id, session_id):
        foundation_id, terminal = self.terminal_or_404(request, terminal_id)
        with tenant_context(foundation_id):
            cancel_qr_session(self.session_or_404(foundation_id, terminal, session_id))
        return Response({'status': 'CANCELLED'})


class QRTerminalResultView(_TerminalMixin, APIView):
    """GET /web/wallet/canteen/qr/terminal/<id>/session/<sid>/result/ — the poll (QRS-013)."""
    http_method_names = ['get']

    def get(self, request, terminal_id, session_id):
        foundation_id, terminal = self.terminal_or_404(request, terminal_id)
        with tenant_context(foundation_id):
            result = get_qr_session_result(self.session_or_404(foundation_id, terminal, session_id))
        pos_tx = result['transaction']
        body = {'status': result['status'], 'transaction': None}
        if pos_tx:
            body['transaction'] = {
                'id': pos_tx.id, 'student_name': pos_tx.student.person.full_name,
                'student_nis': pos_tx.student.nis, 'amount': str(pos_tx.total),
                'confirmation_code': pos_tx.confirmation_code, 'occurred_at': pos_tx.occurred_at,
                'voided': pos_tx.status == 'VOIDED',
            }
        return Response(body)


class QRTerminalVoidView(_TerminalMixin, APIView):
    """POST /web/wallet/canteen/qr/terminal/<id>/session/<sid>/void/ — wrong amount: void, then re-charge (QRS-025)."""
    http_method_names = ['post']

    def post(self, request, terminal_id, session_id):
        foundation_id, terminal = self.terminal_or_404(request, terminal_id)
        with tenant_context(foundation_id):
            session = self.session_or_404(foundation_id, terminal, session_id)
            pos_tx = POSTransaction.objects.filter(
                foundation_id=foundation_id, qr_session=session, entry_mode=POSEntryMode.SELF_ENTERED,
                status='COMPLETED',
            ).first()
            if pos_tx is None:
                raise NotFound()
            try:
                void_pos_transaction(pos_tx, _("Dibatalkan petugas: jumlah salah"), actor=request.user)
            except (VoidWindowExpiredError, ValueError):
                return Response({'error': _("Batas waktu pembatalan sudah lewat.")}, status=400)
        return Response({'status': 'VOIDED'})
