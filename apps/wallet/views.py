from datetime import datetime
import base64
from decimal import Decimal

from django.db.models import Exists, OuterRef
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.pagination import StandardCursorPagination
from apps.core.services import build_signed_download
from apps.identity.models import School, Student
from apps.identity.permissions import HasRequiredPermission
from educore.middleware.tenancy import get_current_foundation_id

from apps.wallet.models import (
    POSEntryMode,
    POSQRSession,
    Merchant,
    MerchantSettlement,
    WalletAutoTopupConfig,
    POSTerminal,
    POSTransaction,
    POSTransactionStatus,
    Product,
    WalletRefundRequest,
    WalletRefundStatus,
    WalletReconciliation,
    WalletReconciliationStatus,
    WalletTopupIntent,
    WalletTransaction,
)
from apps.wallet.serializers import (
    MerchantSerializer,
    MerchantSettlementRunSerializer,
    MerchantQRChargeSerializer,
    MerchantSettlementSerializer,
    POSBatchCreateSerializer,
    POSSessionSerializer,
    POSSyncQuerySerializer,
    POSTerminalSerializer,
    POSTransactionCreateSerializer,
    POSTransactionSerializer,
    POSTransactionVoidSerializer,
    ProductSerializer,
    QRChargeSerializer,
    QRSessionCreateSerializer,
    QRStudentTokenSerializer,
    ReconciliationCashSettleSerializer,
    ReconciliationWriteOffSerializer,
    RefundMarkDonatedSerializer,
    RefundMarkPaidSerializer,
    SpendRuleSerializer,
    StudentNutritionSummarySerializer,
    WalletAutoTopupConfigSerializer,
    WalletAutoTopupConfigUpdateSerializer,
    TopupIntentCreateSerializer,
    TopupIntentSerializer,
    TopupSerializer,
    WalletSerializer,
    WalletTransactionSerializer,
)
from apps.wallet.qr_charge import (
    QRChargeError,
    cancel_qr_session,
    charge_qr_session,
    create_qr_session,
    get_qr_session_result,
    resolve_qr_session,
    set_merchant_qr_charge,
)
from apps.wallet.services import (
    CurrencyMismatchError,
    InsufficientBalanceError,
    InvalidWebhookError,
    SettlementStateError,
    SpendNotAllowedError,
    VoidWindowExpiredError,
    WalletAutoTopupError,
    WalletNotActiveError,
    attach_receipts_to_batch_report,
    create_wallet_topup_intent,
    set_wallet_auto_topup_config,
    generate_settlement_statement_pdf,
    get_or_create_spend_rule,
    get_or_create_wallet,
    get_reconciliation_queue,
    get_refund_queue,
    get_school_reconciliation_exposure,
    invoice_reconciliation_case,
    mark_refund_donated,
    mark_refund_paid,
    pos_session,
    pos_sync,
    process_offline_pos_batch,
    process_pos_transaction,
    process_wallet_topup_webhook,
    resend_reconciliation_notice,
    run_merchant_settlement,
    set_spend_rule,
    settle_reconciliation_with_cash,
    topup_wallet,
    void_pos_transaction,
    write_off_reconciliation_case,
)


def _get_student_or_404(student_id, foundation_id, user=None):
    student = Student.objects.filter(id=student_id, foundation_id=foundation_id).first()
    if not student:
        return None
    if user and user.is_authenticated:
        from apps.identity.guardian_access import can_guardian_access_student
        if not can_guardian_access_student(user, student.id, foundation_id):
            return None
    return student


class WalletDetailView(APIView):
    """GET /wallets/:student_id (WAL-008)."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'wallet.topup.read'

    def get(self, request, student_id):
        foundation_id = get_current_foundation_id()
        student = _get_student_or_404(student_id, foundation_id, user=request.user)
        if not student:
            return Response({'error': _("Siswa tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)
        wallet = get_or_create_wallet(student)
        return Response(WalletSerializer(wallet).data)


class WalletTransactionsView(APIView):
    """GET /wallets/:student_id/transactions (WAL-008: itemised history)."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'wallet.topup.read'
    pagination_class = StandardCursorPagination

    def get(self, request, student_id):
        foundation_id = get_current_foundation_id()
        student = _get_student_or_404(student_id, foundation_id, user=request.user)
        if not student:
            return Response({'error': _("Siswa tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)
        wallet = get_or_create_wallet(student)

        qs = WalletTransaction.objects.filter(
            foundation_id=foundation_id, wallet=wallet, deleted_at__isnull=True,
        ).annotate(
            self_entered=Exists(POSTransaction.objects.filter(
                wallet_transaction_id=OuterRef('pk'), entry_mode=POSEntryMode.SELF_ENTERED,
            )),
        ).order_by('-occurred_at')

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request, view=self)
        serializer = WalletTransactionSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class WalletTopupView(APIView):
    """POST /wallets/:student_id/topup (WAL-005: manual/cash methods this slice)."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'wallet.topup.write'

    def post(self, request, student_id):
        foundation_id = get_current_foundation_id()
        student = _get_student_or_404(student_id, foundation_id, user=request.user)
        if not student:
            return Response({'error': _("Siswa tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)
        wallet = get_or_create_wallet(student)

        payload = TopupSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        try:
            record = topup_wallet(
                wallet,
                payload.validated_data['amount'],
                payload.validated_data['method'],
                payload.validated_data['idempotency_key'],
                reference=payload.validated_data.get('reference', ''),
            )
        except (InsufficientBalanceError, WalletNotActiveError, CurrencyMismatchError, ValueError) as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(WalletTransactionSerializer(record).data, status=status.HTTP_201_CREATED)


class WalletTopupIntentView(APIView):
    """POST /wallets/:student_id/topup-intents (WAL-005: VA/QRIS)."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'wallet.topup.write'

    def post(self, request, student_id):
        foundation_id = get_current_foundation_id()
        student = _get_student_or_404(student_id, foundation_id, user=request.user)
        if not student:
            return Response({'error': _("Siswa tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)
        wallet = get_or_create_wallet(student)

        payload = TopupIntentCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        try:
            intent = create_wallet_topup_intent(
                wallet, student, student.school,
                payload.validated_data['method'],
                payload.validated_data['amount'],
                bank=payload.validated_data.get('bank') or None,
                provider_name=payload.validated_data.get('provider') or 'MOCK',
            )
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(TopupIntentSerializer(intent).data, status=status.HTTP_201_CREATED)


class WalletTopupIntentDetailView(APIView):
    """GET /wallets/:student_id/topup-intents/:intent_id (WAL-005: polling status)."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'wallet.topup.read'

    def get(self, request, student_id, intent_id):
        foundation_id = get_current_foundation_id()
        student = _get_student_or_404(student_id, foundation_id, user=request.user)
        if not student:
            return Response({'error': _("Siswa tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        intent = WalletTopupIntent.objects.filter(
            id=intent_id,
            student=student,
            foundation_id=foundation_id,
            deleted_at__isnull=True,
        ).first()
        if not intent:
            return Response({'error': _("Topup intent tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        return Response(TopupIntentSerializer(intent).data)


class WalletAutoTopupConfigView(APIView):
    """GET/PUT /wallets/:student_id/auto-topup-config (WAL-006).

    GET returns the platform default (is_active=False, no threshold set) when the
    wallet has no config yet. PUT with is_active=False is the cancel path.
    """
    permission_classes = [HasRequiredPermission]

    def get_required_permission(self):
        return 'wallet.topup.write' if self.request.method == 'PUT' else 'wallet.topup.read'

    def get(self, request, student_id):
        foundation_id = get_current_foundation_id()
        student = _get_student_or_404(student_id, foundation_id, user=request.user)
        if not student:
            return Response({'error': _("Siswa tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)
        wallet = get_or_create_wallet(student)

        config = WalletAutoTopupConfig.objects.filter(wallet=wallet).first()
        if config:
            return Response(WalletAutoTopupConfigSerializer(config).data)
        return Response({
            'wallet': wallet.id, 'is_active': False,
            'threshold_amount': None, 'topup_amount': None, 'method': 'VA', 'bank': '',
        })

    def put(self, request, student_id):
        foundation_id = get_current_foundation_id()
        student = _get_student_or_404(student_id, foundation_id, user=request.user)
        if not student:
            return Response({'error': _("Siswa tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)
        wallet = get_or_create_wallet(student)

        payload = WalletAutoTopupConfigUpdateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        try:
            config = set_wallet_auto_topup_config(
                wallet,
                is_active=payload.validated_data['is_active'],
                threshold_amount=payload.validated_data['threshold_amount'],
                topup_amount=payload.validated_data['topup_amount'],
                method=payload.validated_data.get('method', 'VA'),
                bank=payload.validated_data.get('bank') or None,
                actor=request.user,
            )
        except WalletAutoTopupError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(WalletAutoTopupConfigSerializer(config).data)


class WalletTopupWebhookView(APIView):
    """Public signature-verified wallet top-up webhook (WAL-005, mirrors finance's PaymentWebhookView)."""
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request, provider):
        try:
            result = process_wallet_topup_webhook(provider, request.data, request.headers)
            return Response(result, status=status.HTTP_200_OK)
        except InvalidWebhookError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class SpendRuleView(APIView):
    """GET/PUT /wallets/:student_id/rules (WAL-009 to WAL-011)."""
    permission_classes = [HasRequiredPermission]

    def get_required_permission(self):
        return 'wallet.topup.write' if self.request.method == 'PUT' else 'wallet.topup.read'

    def get(self, request, student_id):
        foundation_id = get_current_foundation_id()
        student = _get_student_or_404(student_id, foundation_id, user=request.user)
        if not student:
            return Response({'error': _("Siswa tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)
        rule = get_or_create_spend_rule(student)
        return Response(SpendRuleSerializer(rule).data)

    def put(self, request, student_id):
        foundation_id = get_current_foundation_id()
        student = _get_student_or_404(student_id, foundation_id, user=request.user)
        if not student:
            return Response({'error': _("Siswa tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        payload = SpendRuleSerializer(data=request.data, partial=True)
        payload.is_valid(raise_exception=True)

        rule = set_spend_rule(
            student,
            daily_limit=payload.validated_data.get('daily_limit'),
            blocked_categories=payload.validated_data.get('blocked_categories'),
            blocked_products=payload.validated_data.get('blocked_products'),
            allowed_window_start=payload.validated_data.get('allowed_window_start'),
            allowed_window_end=payload.validated_data.get('allowed_window_end'),
            qr_charge_enabled=payload.validated_data.get('qr_charge_enabled'),
        )
        return Response(SpendRuleSerializer(rule).data)


def _school_ceiling(request, permission):
    """None = `permission` held foundation-wide; else the set of school ids the
    user holds it in (IAM-012). The permission class only sees a school when the
    URL or query names one, so anything addressed by id must check its own."""
    from apps.identity.console_access import accessible_school_ids

    return accessible_school_ids(request.user, get_current_foundation_id(), permission)


def _get_terminal_in_ceiling(request, foundation_id, terminal_id, permission):
    """The POSTerminal `terminal_id` if its merchant's school is inside the
    user's `permission` ceiling, else None (callers answer 404)."""
    ceiling = _school_ceiling(request, permission)
    qs = POSTerminal.objects.filter(id=terminal_id, foundation_id=foundation_id)
    if ceiling is not None:
        qs = qs.filter(merchant__school_id__in=ceiling)
    return qs.first()


class TenantScopedCatalogViewSet(viewsets.ModelViewSet):
    """Common tenancy- and school-scoped queryset behaviour for merchant/product/terminal
    catalog data. Subclasses set `school_path` (ORM path from a row to its school id):
    rows outside the schools the actor holds the action's permission in are 404s, and a
    create/update may not point a row at a school outside that ceiling."""
    model = None
    school_path = None
    pagination_class = StandardCursorPagination
    permission_classes = [HasRequiredPermission]

    def _action_ceiling(self):
        permission = self.action_permissions.get(self.action)
        return _school_ceiling(self.request, permission) if permission else set()

    def target_school_id(self, validated_data):
        """The school a create/update would put the row in (None = untouched)."""
        return None

    def _check_target(self, serializer):
        ceiling = self._action_ceiling()
        school_id = self.target_school_id(serializer.validated_data)
        if ceiling is not None and school_id is not None and school_id not in ceiling:
            raise NotFound()

    def get_queryset(self):
        foundation_id = get_current_foundation_id()
        if not foundation_id:
            return self.model.objects.none()
        qs = self.model.objects.filter(foundation_id=foundation_id, deleted_at__isnull=True).order_by('-created_at')
        ceiling = self._action_ceiling()
        if ceiling is not None:
            qs = qs.filter(**{f'{self.school_path}__in': ceiling})
        for param, field in getattr(self, 'filter_params', {}).items():
            value = self.request.query_params.get(param)
            if value:
                qs = qs.filter(**{field: value})
        return qs

    def perform_create(self, serializer):
        self._check_target(serializer)
        serializer.save(foundation_id=get_current_foundation_id())

    def perform_update(self, serializer):
        self._check_target(serializer)
        serializer.save()


class MerchantViewSet(TenantScopedCatalogViewSet):
    model = Merchant
    school_path = 'school_id'

    def target_school_id(self, validated_data):
        school = validated_data.get('school')
        return school.id if school else None

    serializer_class = MerchantSerializer
    filter_params = {'school_id': 'school_id'}
    action_permissions = {
        'list': 'school_config.read', 'retrieve': 'school_config.read',
        'create': 'school_config.write', 'update': 'school_config.write',
        'partial_update': 'school_config.write', 'destroy': 'school_config.write',
        'sales': 'finance.payment.read', 'settlements': 'finance.payment.read', 'run_settlement': 'finance.payment.write',
        'qr_charge': 'school_config.write',
    }

    @action(detail=True, methods=['post'], url_path='qr-charge')
    def qr_charge(self, request, pk=None):
        """POST /merchants/:id/qr-charge/ — school admin switch for student-entered QR (QRS-001)."""
        merchant = self.get_object()
        payload = MerchantQRChargeSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            set_merchant_qr_charge(
                merchant, payload.validated_data['enabled'], payload.validated_data['acknowledged'], request.user,
            )
        except QRChargeError as e:
            return _qr_error_response(e)
        return Response(self.get_serializer(merchant).data)

    @action(detail=True, methods=['get'], url_path='sales')
    def sales(self, request, pk=None):
        merchant = self.get_object()
        qs = POSTransaction.objects.filter(
            foundation_id=merchant.foundation_id, merchant=merchant, status=POSTransactionStatus.COMPLETED,
        ).order_by('-occurred_at')
        date_from = request.query_params.get('from')
        date_to = request.query_params.get('to')
        if date_from:
            qs = qs.filter(occurred_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(occurred_at__date__lte=date_to)
        return Response(POSTransactionSerializer(qs, many=True).data)

    @action(detail=True, methods=['get'], url_path='settlements')
    def settlements(self, request, pk=None):
        merchant = self.get_object()
        qs = MerchantSettlement.objects.filter(
            foundation_id=merchant.foundation_id, merchant=merchant, deleted_at__isnull=True,
        ).order_by('-period_start')
        return Response(MerchantSettlementSerializer(qs, many=True).data)

    @action(detail=True, methods=['post'], url_path='settlements/run')
    def run_settlement(self, request, pk=None):
        merchant = self.get_object()
        payload = MerchantSettlementRunSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            settlement = run_merchant_settlement(
                merchant, payload.validated_data['period_start'], payload.validated_data['period_end'],
            )
        except SettlementStateError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        generate_settlement_statement_pdf(settlement)
        return Response(MerchantSettlementSerializer(settlement).data, status=status.HTTP_201_CREATED)


class SettlementStatementDownloadView(APIView):
    """Signed-GET download URL for a merchant settlement's statement PDF."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'finance.payment.read'

    def get(self, request, settlement_id):
        foundation_id = get_current_foundation_id()
        settlements = MerchantSettlement.objects.filter(
            id=settlement_id, foundation_id=foundation_id, deleted_at__isnull=True,
        )
        ceiling = _school_ceiling(request, self.required_permission)
        if ceiling is not None:
            settlements = settlements.filter(merchant__school_id__in=ceiling)
        settlement = settlements.first()
        if not settlement:
            return Response({'error': _("Settlement tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)
        if not settlement.statement_pdf_key:
            return Response({'error': _("Belum ada dokumen statement untuk diunduh.")}, status=status.HTTP_404_NOT_FOUND)
        return Response(build_signed_download(settlement.statement_pdf_key))


class _MerchantChildViewSet(TenantScopedCatalogViewSet):
    """Catalog rows that belong to a merchant: their school is the merchant's."""
    school_path = 'merchant__school_id'

    def target_school_id(self, validated_data):
        merchant = validated_data.get('merchant')
        return merchant.school_id if merchant else None


class ProductViewSet(_MerchantChildViewSet):
    model = Product
    serializer_class = ProductSerializer
    filter_params = {'merchant_id': 'merchant_id'}
    action_permissions = {
        'list': 'school_config.read', 'retrieve': 'school_config.read',
        'create': 'school_config.write', 'update': 'school_config.write',
        'partial_update': 'school_config.write', 'destroy': 'school_config.write',
    }


class POSTerminalViewSet(_MerchantChildViewSet):
    model = POSTerminal
    serializer_class = POSTerminalSerializer
    filter_params = {'merchant_id': 'merchant_id'}
    action_permissions = {
        'list': 'hardware.read', 'retrieve': 'hardware.read',
        'create': 'hardware.write', 'update': 'hardware.write',
        'partial_update': 'hardware.write', 'destroy': 'hardware.write',
    }


class POSTransactionViewSet(_MerchantChildViewSet):
    model = POSTransaction
    serializer_class = POSTransactionSerializer
    filter_params = {'merchant_id': 'merchant_id', 'student_id': 'student_id', 'terminal_id': 'terminal_id'}
    action_permissions = {
        'list': 'wallet.topup.read', 'retrieve': 'wallet.topup.read',
        'create': 'wallet.topup.write', 'void': 'wallet.topup.write', 'batch': 'wallet.topup.write',
        'receipt': 'wallet.topup.write',
    }

    @action(detail=True, methods=['get'], url_path='receipt')
    def receipt(self, request, pk=None):
        """GET /pos/transactions/:id/receipt/ — raw ESC/POS bytes for reprint
        (WAL-020). The terminal writes them straight to its locally-attached
        USB/Bluetooth printer port. Returns a plain Django HttpResponse: DRF's
        JSON renderer would re-encode the raw bytes as a JSON string."""
        from apps.wallet.escpos import render_pos_receipt
        from django.http import HttpResponse
        pos_tx = self.get_object()
        payload = render_pos_receipt(pos_tx)
        response = HttpResponse(payload, content_type='application/vnd.escpos.raw')
        response['Content-Disposition'] = f'attachment; filename="receipt-{pos_tx.pk}.bin"'
        return response

    @action(detail=False, methods=['post'], url_path='batch')
    def batch(self, request):
        foundation_id = get_current_foundation_id()
        payload = POSBatchCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        terminal = _get_terminal_in_ceiling(request, foundation_id, payload.validated_data['terminal_id'], 'wallet.topup.write')
        if not terminal:
            return Response({'error': _("Terminal tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        report = process_offline_pos_batch(terminal, payload.validated_data['transactions'])
        report = attach_receipts_to_batch_report(terminal, report)
        return Response(report, status=status.HTTP_200_OK)

    def create(self, request, *args, **kwargs):
        foundation_id = get_current_foundation_id()
        payload = POSTransactionCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        terminal = _get_terminal_in_ceiling(request, foundation_id, data['terminal_id'], 'wallet.topup.write')
        if not terminal:
            return Response({'error': _("Terminal tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)
        student = Student.objects.filter(id=data['student_id'], foundation_id=foundation_id).first()
        if not student:
            return Response({'error': _("Siswa tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        try:
            pos_tx = process_pos_transaction(
                terminal, student, data['items'], data['client_transaction_id'],
                occurred_at=data.get('occurred_at'),
            )
        except (SpendNotAllowedError, InsufficientBalanceError, WalletNotActiveError, CurrencyMismatchError) as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        # WAL-018/WAL-020: receipt bytes ride the checkout response so the
        # terminal prints immediately from its attached printer — no second
        # round trip inside the <=3s budget.
        from apps.wallet.escpos import render_pos_receipt
        body = self.get_serializer(pos_tx).data
        body['receipt_escpos_base64'] = base64.b64encode(render_pos_receipt(pos_tx)).decode('ascii')
        return Response(body, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='void')
    def void(self, request, pk=None):
        pos_tx = self.get_object()
        payload = POSTransactionVoidSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            voided = void_pos_transaction(pos_tx, payload.validated_data.get('reason', ''), actor=request.user)
        except VoidWindowExpiredError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(voided).data)


class POSSessionView(APIView):
    """POST /pos/sessions: full offline-cache snapshot for a terminal (WAL-014)."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'wallet.topup.read'

    def post(self, request):
        foundation_id = get_current_foundation_id()
        payload = POSSessionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        terminal = _get_terminal_in_ceiling(request, foundation_id, payload.validated_data['terminal_id'], 'wallet.topup.read')
        if not terminal:
            return Response({'error': _("Terminal tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)
        return Response(pos_session(terminal))


def _qr_error_response(error: QRChargeError):
    return Response({'error': error.code, 'message': error.message}, status=status.HTTP_400_BAD_REQUEST)


class QRSessionCreateView(APIView):
    """POST /pos/qr-sessions/: terminal mints a one-time QR (QRS-005)."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'wallet.topup.write'

    def post(self, request):
        foundation_id = get_current_foundation_id()
        payload = QRSessionCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        terminal = POSTerminal.objects.filter(
            id=payload.validated_data['terminal_id'], foundation_id=foundation_id, deleted_at__isnull=True,
        ).select_related('merchant').first()
        if not terminal:
            return Response({'error': _("Terminal tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)
        try:
            minted = create_qr_session(terminal)
        except QRChargeError as e:
            return _qr_error_response(e)
        session = minted['session']
        return Response(
            {'session_id': session.id, 'token': minted['token'], 'expires_at': session.expires_at},
            status=status.HTTP_201_CREATED,
        )


class QRSessionDetailView(APIView):
    """DELETE /pos/qr-sessions/:id/: regenerate/cancel."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'wallet.topup.write'

    def delete(self, request, session_id):
        session = POSQRSession.objects.filter(
            id=session_id, foundation_id=get_current_foundation_id(), deleted_at__isnull=True,
        ).first()
        if not session:
            return Response({'error': _("Sesi QR tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)
        cancel_qr_session(session)
        return Response(status=status.HTTP_204_NO_CONTENT)


class QRSessionResultView(APIView):
    """GET /pos/qr-sessions/:id/result/: terminal polls for the student's charge (QRS-013/014)."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'wallet.topup.write'

    def get(self, request, session_id):
        session = POSQRSession.objects.filter(
            id=session_id, foundation_id=get_current_foundation_id(), deleted_at__isnull=True,
        ).first()
        if not session:
            return Response({'error': _("Sesi QR tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)
        result = get_qr_session_result(session)
        pos_tx = result['transaction']
        body = {'status': result['status'], 'transaction': None}
        if pos_tx:
            body['transaction'] = {
                'id': pos_tx.id,
                'student_name': pos_tx.student.person.full_name,
                'student_nis': pos_tx.student.nis,
                'amount': str(pos_tx.total),
                'confirmation_code': pos_tx.confirmation_code,
                'occurred_at': pos_tx.occurred_at,
                'status': pos_tx.status,
            }
        return Response(body)


class _QRStudentView(APIView):
    """Shared base: the scanning caller must be an active guardian of the student (spec 18 §9).

    Staff hold ``wallet.topup.write`` too, so the permission alone is not enough — an
    operator must never be able to charge an arbitrary student's wallet by QR.
    """
    permission_classes = [HasRequiredPermission]
    required_permission = 'wallet.topup.write'

    def _guardian_student(self, request, student_id):
        foundation_id = get_current_foundation_id()
        from apps.identity.guardian_access import get_guardian_student_ids
        if student_id not in get_guardian_student_ids(request.user, foundation_id):
            return None
        return Student.objects.filter(id=student_id, foundation_id=foundation_id, deleted_at__isnull=True).first()


class QRResolveView(_QRStudentView):
    """POST /wallet/qr/resolve/: stall, own balance and cap before the keypad (QRS-011)."""

    def post(self, request):
        payload = QRStudentTokenSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        student = self._guardian_student(request, payload.validated_data['student_id'])
        if not student:
            return Response({'error': _("Siswa tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)
        try:
            data = resolve_qr_session(payload.validated_data['token'], student)
        except QRChargeError as e:
            return _qr_error_response(e)
        data['balance'] = str(data['balance'])
        data['max_amount'] = str(data['max_amount'])
        return Response(data)


class QRChargeView(_QRStudentView):
    """POST /wallet/qr/charge/: the student-entered debit (QRS-012)."""

    def post(self, request):
        payload = QRChargeSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        student = self._guardian_student(request, data['student_id'])
        if not student:
            return Response({'error': _("Siswa tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)
        try:
            pos_tx = charge_qr_session(data['token'], student, data['amount'], data['idempotency_key'])
        except QRChargeError as e:
            return _qr_error_response(e)
        return Response({
            'txn_id': pos_tx.id,
            'code': pos_tx.confirmation_code,
            'amount': str(pos_tx.total),
            'balance_after': str(pos_tx.wallet_transaction.balance_after),
            'occurred_at': pos_tx.occurred_at,
        }, status=status.HTTP_201_CREATED)


class POSSyncView(APIView):
    """GET /pos/sync?terminal_id&cursor: incremental deltas since a cursor (WAL-012)."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'wallet.topup.read'

    def get(self, request):
        foundation_id = get_current_foundation_id()
        payload = POSSyncQuerySerializer(data=request.query_params)
        payload.is_valid(raise_exception=True)
        terminal = _get_terminal_in_ceiling(request, foundation_id, payload.validated_data['terminal_id'], 'wallet.topup.read')
        if not terminal:
            return Response({'error': _("Terminal tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)
        return Response(pos_sync(terminal, since_cursor=payload.validated_data.get('cursor')))


class WalletReconciliationQueueView(APIView):
    """GET /wallet-reconciliations?school_id&status (REC-025, REC-028)."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'finance.payment.read'

    def get(self, request):
        foundation_id = get_current_foundation_id()
        school_id = request.query_params.get('school_id')
        if not school_id:
            return Response({'error': _("Parameter school_id wajib diisi.")}, status=status.HTTP_400_BAD_REQUEST)

        school = School.objects.filter(id=school_id, foundation_id=foundation_id).first()
        if not school:
            return Response({'error': _("Sekolah tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        status_param = request.query_params.get('status', WalletReconciliationStatus.OPEN)

        return Response({
            'school_id': school.id,
            'total_exposure': str(get_school_reconciliation_exposure(school)),
            'currency': school.base_currency,
            'cases': get_reconciliation_queue(school, status=status_param),
        })


def _school_scoped(qs, request, foundation_id, permission):
    """Narrow a student-bearing queryset to the schools `request.user` holds
    `permission` in (None ceiling = foundation-wide). The permission class only
    sees a school when the URL/query names one, so an id-addressed action must
    check the row's own school or a school-scoped officer could act on another
    school's rows (IAM-012)."""
    from apps.identity.console_access import accessible_school_ids

    ceiling = accessible_school_ids(request.user, foundation_id, permission)
    return qs if ceiling is None else qs.filter(student__school_id__in=ceiling)


class WalletReconciliationCaseView(APIView):
    """Detail + admin actions on one reconciliation case (REC-026)."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'finance.payment.write'

    def _get_case(self, request, case_id):
        foundation_id = get_current_foundation_id()
        qs = WalletReconciliation.objects.filter(id=case_id, foundation_id=foundation_id)
        return _school_scoped(qs, request, foundation_id, self.required_permission).first()


class WalletReconciliationSettleCashView(WalletReconciliationCaseView):
    def post(self, request, case_id):
        case = self._get_case(request, case_id)
        if not case:
            return Response({'error': _("Kasus tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        payload = ReconciliationCashSettleSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        try:
            settle_reconciliation_with_cash(
                case, payload.validated_data['amount'], payload.validated_data.get('reference', ''), actor=request.user,
            )
        except ValueError as e:  # WalletNotActiveError, CurrencyMismatchError, INVALID_STATE
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        case.refresh_from_db()
        return Response({'id': case.id, 'status': case.status})


class WalletReconciliationInvoiceNowView(WalletReconciliationCaseView):
    def post(self, request, case_id):
        case = self._get_case(request, case_id)
        if not case:
            return Response({'error': _("Kasus tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)
        if case.status != WalletReconciliationStatus.OPEN:
            return Response({'error': _("INVALID_STATE: kasus tidak lagi OPEN.")}, status=status.HTTP_400_BAD_REQUEST)

        invoice_reconciliation_case(case, actor=request.user)
        case.refresh_from_db()
        return Response({'id': case.id, 'status': case.status, 'invoice_id': case.invoice_id})


class WalletReconciliationWriteOffView(WalletReconciliationCaseView):
    def post(self, request, case_id):
        case = self._get_case(request, case_id)
        if not case:
            return Response({'error': _("Kasus tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        payload = ReconciliationWriteOffSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        try:
            write_off_reconciliation_case(case, request.user, payload.validated_data['reason'])
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        case.refresh_from_db()
        return Response({'id': case.id, 'status': case.status})


class WalletReconciliationResendNoticeView(WalletReconciliationCaseView):
    def post(self, request, case_id):
        case = self._get_case(request, case_id)
        if not case:
            return Response({'error': _("Kasus tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        try:
            resend_reconciliation_notice(case, actor=request.user)
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response({'id': case.id})


class WalletRefundQueueView(APIView):
    """GET /wallet-refunds?school_id&status (WAL-026)."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'finance.payment.read'

    def get(self, request):
        foundation_id = get_current_foundation_id()
        school_id = request.query_params.get('school_id')
        if not school_id:
            return Response({'error': _("Parameter school_id wajib diisi.")}, status=status.HTTP_400_BAD_REQUEST)

        school = School.objects.filter(id=school_id, foundation_id=foundation_id).first()
        if not school:
            return Response({'error': _("Sekolah tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        status_param = request.query_params.get('status', WalletRefundStatus.PENDING)
        return Response({'school_id': school.id, 'requests': get_refund_queue(school, status=status_param)})


class WalletRefundActionView(APIView):
    permission_classes = [HasRequiredPermission]
    required_permission = 'finance.payment.write'

    def _get_request(self, request, refund_id):
        foundation_id = get_current_foundation_id()
        qs = WalletRefundRequest.objects.filter(id=refund_id, foundation_id=foundation_id)
        return _school_scoped(qs, request, foundation_id, self.required_permission).first()


class WalletRefundMarkPaidView(WalletRefundActionView):
    def post(self, request, refund_id):
        refund_request = self._get_request(request, refund_id)
        if not refund_request:
            return Response({'error': _("Permintaan tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        payload = RefundMarkPaidSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        try:
            mark_refund_paid(
                refund_request,
                payload.validated_data.get('bank_name', ''),
                payload.validated_data.get('account_number', ''),
                payload.validated_data.get('account_holder_name', ''),
                payload.validated_data.get('reference', ''),
                actor=request.user,
            )
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        refund_request.refresh_from_db()
        return Response({'id': refund_request.id, 'status': refund_request.status})


class WalletRefundMarkDonatedView(WalletRefundActionView):
    def post(self, request, refund_id):
        refund_request = self._get_request(request, refund_id)
        if not refund_request:
            return Response({'error': _("Permintaan tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        payload = RefundMarkDonatedSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        try:
            mark_refund_donated(refund_request, request.user, payload.validated_data['donation_consent'])
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        refund_request.refresh_from_db()
        return Response({'id': refund_request.id, 'status': refund_request.status})


class StudentNutritionSummaryView(APIView):
    """GET /api/v1/students/:id/nutrition-summary?from&to (spec/07 §8, WAL-024)."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'student_records.read'

    def get(self, request, student_id):
        foundation_id = get_current_foundation_id()
        student = _get_student_or_404(student_id, foundation_id, user=request.user)
        if not student:
            return Response({'error': _("Siswa tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        from_str = request.query_params.get('from')
        to_str = request.query_params.get('to')
        today = timezone.localdate()

        try:
            from_date = datetime.strptime(from_str, '%Y-%m-%d').date() if from_str else today
            to_date = datetime.strptime(to_str, '%Y-%m-%d').date() if to_str else today
        except ValueError:
            return Response(
                {'error': _("Format tanggal tidak valid. Gunakan format YYYY-MM-DD.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if from_date > to_date:
            return Response(
                {'error': _("Parameter 'from' tidak boleh melebihi 'to'.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        transactions = POSTransaction.objects.filter(
            foundation_id=foundation_id,
            student_id=student.id,
            status=POSTransactionStatus.COMPLETED,
            occurred_at__date__gte=from_date,
            occurred_at__date__lte=to_date,
        ).order_by('occurred_at')

        skus = set()
        for tx in transactions:
            for item in (tx.items or []):
                if isinstance(item, dict) and item.get('sku'):
                    skus.add(item.get('sku'))

        products = Product.objects.filter(foundation_id=foundation_id, sku__in=skus)
        product_map = {p.sku: p for p in products}

        total_calories = 0
        total_sugar_g = Decimal('0.00')
        total_items = 0
        healthy_items_count = 0
        allergens_set = set()
        daily_map = {}
        items_list = []

        for tx in transactions:
            tx_date = tx.occurred_at.date()
            if tx_date not in daily_map:
                daily_map[tx_date] = {
                    'date': tx_date,
                    'total_calories': 0,
                    'total_sugar_g': Decimal('0.00'),
                    'items_count': 0,
                    'healthy_count': 0,
                }

            for item in (tx.items or []):
                if not isinstance(item, dict):
                    continue
                sku = item.get('sku', '')
                name = item.get('name', sku)
                qty = int(item.get('qty') or 1)
                unit_price = item.get('unit_price', '0.00')
                prod = product_map.get(sku)

                item_nutr = item.get('nutrition') or (prod.nutrition if prod else {}) or {}
                item_allergens = item.get('allergens') or (prod.allergens if prod else []) or []
                if isinstance(item_allergens, str):
                    item_allergens = [item_allergens]

                cal_per_unit = int(item_nutr.get('calories') or 0)
                sugar_per_unit = Decimal(str(item_nutr.get('sugar_g') or 0))
                is_healthy = bool(
                    item.get('is_healthy')
                    or item_nutr.get('is_healthy')
                    or (prod.nutrition.get('is_healthy') if prod and isinstance(prod.nutrition, dict) else False)
                    or (prod and 'healthy' in (prod.category or '').lower())
                )

                line_cals = cal_per_unit * qty
                line_sugar = sugar_per_unit * qty

                total_calories += line_cals
                total_sugar_g += line_sugar
                total_items += qty
                if is_healthy:
                    healthy_items_count += qty

                for a in item_allergens:
                    if a:
                        allergens_set.add(str(a).strip().lower())

                daily_map[tx_date]['total_calories'] += line_cals
                daily_map[tx_date]['total_sugar_g'] += line_sugar
                daily_map[tx_date]['items_count'] += qty
                if is_healthy:
                    daily_map[tx_date]['healthy_count'] += qty

                items_list.append({
                    'sku': sku,
                    'name': name,
                    'qty': qty,
                    'unit_price': unit_price,
                    'calories': line_cals,
                    'sugar_g': line_sugar,
                    'allergens': item_allergens,
                    'is_healthy': is_healthy,
                    'occurred_at': tx.occurred_at,
                })

        daily_breakdown = sorted(daily_map.values(), key=lambda d: d['date'])

        data = {
            'student_id': student.id,
            'from_date': from_date,
            'to_date': to_date,
            'total_calories': total_calories,
            'total_sugar_g': total_sugar_g,
            'total_items': total_items,
            'healthy_items_count': healthy_items_count,
            'allergens': sorted(list(allergens_set)),
            'daily_breakdown': daily_breakdown,
            'items': items_list,
        }
        serializer = StudentNutritionSummarySerializer(data)
        return Response(serializer.data, status=status.HTTP_200_OK)
