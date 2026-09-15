from django.utils.translation import gettext_lazy as _
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.pagination import StandardCursorPagination
from apps.identity.models import School, Student
from apps.identity.permissions import HasRequiredPermission
from educore.middleware.tenancy import get_current_foundation_id

from apps.wallet.models import (
    Merchant,
    MerchantSettlement,
    POSTerminal,
    POSTransaction,
    POSTransactionStatus,
    Product,
    WalletRefundRequest,
    WalletRefundStatus,
    WalletReconciliation,
    WalletReconciliationStatus,
    WalletTransaction,
)
from apps.wallet.serializers import (
    MerchantSerializer,
    MerchantSettlementRunSerializer,
    MerchantSettlementSerializer,
    POSBatchCreateSerializer,
    POSSessionSerializer,
    POSSyncQuerySerializer,
    POSTerminalSerializer,
    POSTransactionCreateSerializer,
    POSTransactionSerializer,
    POSTransactionVoidSerializer,
    ProductSerializer,
    ReconciliationCashSettleSerializer,
    ReconciliationWriteOffSerializer,
    RefundMarkDonatedSerializer,
    RefundMarkPaidSerializer,
    SpendRuleSerializer,
    TopupSerializer,
    WalletSerializer,
    WalletTransactionSerializer,
)
from apps.wallet.services import (
    CurrencyMismatchError,
    InsufficientBalanceError,
    SettlementStateError,
    SpendNotAllowedError,
    VoidWindowExpiredError,
    WalletNotActiveError,
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
    resend_reconciliation_notice,
    run_merchant_settlement,
    set_spend_rule,
    settle_reconciliation_with_cash,
    topup_wallet,
    void_pos_transaction,
    write_off_reconciliation_case,
)


def _get_student_or_404(student_id, foundation_id):
    return Student.objects.filter(id=student_id, foundation_id=foundation_id).first()


class WalletDetailView(APIView):
    """GET /wallets/:student_id (WAL-008)."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'wallet.topup.read'

    def get(self, request, student_id):
        foundation_id = get_current_foundation_id()
        student = _get_student_or_404(student_id, foundation_id)
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
        student = _get_student_or_404(student_id, foundation_id)
        if not student:
            return Response({'error': _("Siswa tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)
        wallet = get_or_create_wallet(student)

        qs = WalletTransaction.objects.filter(
            foundation_id=foundation_id, wallet=wallet, deleted_at__isnull=True,
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
        student = _get_student_or_404(student_id, foundation_id)
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


class SpendRuleView(APIView):
    """GET/PUT /wallets/:student_id/rules (WAL-009 to WAL-011)."""
    permission_classes = [HasRequiredPermission]

    def get_required_permission(self):
        return 'wallet.topup.write' if self.request.method == 'PUT' else 'wallet.topup.read'

    def get(self, request, student_id):
        foundation_id = get_current_foundation_id()
        student = _get_student_or_404(student_id, foundation_id)
        if not student:
            return Response({'error': _("Siswa tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)
        rule = get_or_create_spend_rule(student)
        return Response(SpendRuleSerializer(rule).data)

    def put(self, request, student_id):
        foundation_id = get_current_foundation_id()
        student = _get_student_or_404(student_id, foundation_id)
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
        )
        return Response(SpendRuleSerializer(rule).data)


class TenantScopedCatalogViewSet(viewsets.ModelViewSet):
    """Common tenancy-scoped queryset behaviour for merchant/product/terminal catalog data."""
    model = None
    pagination_class = StandardCursorPagination
    permission_classes = [HasRequiredPermission]

    def get_queryset(self):
        foundation_id = get_current_foundation_id()
        if not foundation_id:
            return self.model.objects.none()
        qs = self.model.objects.filter(foundation_id=foundation_id, deleted_at__isnull=True).order_by('-created_at')
        for param, field in getattr(self, 'filter_params', {}).items():
            value = self.request.query_params.get(param)
            if value:
                qs = qs.filter(**{field: value})
        return qs

    def perform_create(self, serializer):
        serializer.save(foundation_id=get_current_foundation_id())


class MerchantViewSet(TenantScopedCatalogViewSet):
    model = Merchant
    serializer_class = MerchantSerializer
    filter_params = {'school_id': 'school_id'}
    action_permissions = {
        'list': 'school_config.read', 'retrieve': 'school_config.read',
        'create': 'school_config.write', 'update': 'school_config.write',
        'partial_update': 'school_config.write', 'destroy': 'school_config.write',
        'sales': 'finance.payment.read', 'settlements': 'finance.payment.read', 'run_settlement': 'finance.payment.write',
    }

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


class ProductViewSet(TenantScopedCatalogViewSet):
    model = Product
    serializer_class = ProductSerializer
    filter_params = {'merchant_id': 'merchant_id'}
    action_permissions = {
        'list': 'school_config.read', 'retrieve': 'school_config.read',
        'create': 'school_config.write', 'update': 'school_config.write',
        'partial_update': 'school_config.write', 'destroy': 'school_config.write',
    }


class POSTerminalViewSet(TenantScopedCatalogViewSet):
    model = POSTerminal
    serializer_class = POSTerminalSerializer
    filter_params = {'merchant_id': 'merchant_id'}
    action_permissions = {
        'list': 'hardware.read', 'retrieve': 'hardware.read',
        'create': 'hardware.write', 'update': 'hardware.write',
        'partial_update': 'hardware.write', 'destroy': 'hardware.write',
    }


class POSTransactionViewSet(TenantScopedCatalogViewSet):
    model = POSTransaction
    serializer_class = POSTransactionSerializer
    filter_params = {'merchant_id': 'merchant_id', 'student_id': 'student_id', 'terminal_id': 'terminal_id'}
    action_permissions = {
        'list': 'wallet.topup.read', 'retrieve': 'wallet.topup.read',
        'create': 'wallet.topup.write', 'void': 'wallet.topup.write', 'batch': 'wallet.topup.write',
    }

    @action(detail=False, methods=['post'], url_path='batch')
    def batch(self, request):
        foundation_id = get_current_foundation_id()
        payload = POSBatchCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        terminal = POSTerminal.objects.filter(id=payload.validated_data['terminal_id'], foundation_id=foundation_id).first()
        if not terminal:
            return Response({'error': _("Terminal tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)

        report = process_offline_pos_batch(terminal, payload.validated_data['transactions'])
        return Response(report, status=status.HTTP_200_OK)

    def create(self, request, *args, **kwargs):
        foundation_id = get_current_foundation_id()
        payload = POSTransactionCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        terminal = POSTerminal.objects.filter(id=data['terminal_id'], foundation_id=foundation_id).first()
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

        return Response(self.get_serializer(pos_tx).data, status=status.HTTP_201_CREATED)

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
        terminal = POSTerminal.objects.filter(id=payload.validated_data['terminal_id'], foundation_id=foundation_id).first()
        if not terminal:
            return Response({'error': _("Terminal tidak ditemukan.")}, status=status.HTTP_404_NOT_FOUND)
        return Response(pos_session(terminal))


class POSSyncView(APIView):
    """GET /pos/sync?terminal_id&cursor: incremental deltas since a cursor (WAL-012)."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'wallet.topup.read'

    def get(self, request):
        foundation_id = get_current_foundation_id()
        payload = POSSyncQuerySerializer(data=request.query_params)
        payload.is_valid(raise_exception=True)
        terminal = POSTerminal.objects.filter(id=payload.validated_data['terminal_id'], foundation_id=foundation_id).first()
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


class WalletReconciliationCaseView(APIView):
    """Detail + admin actions on one reconciliation case (REC-026)."""
    permission_classes = [HasRequiredPermission]
    required_permission = 'finance.payment.write'

    def _get_case(self, request, case_id):
        foundation_id = get_current_foundation_id()
        return WalletReconciliation.objects.filter(id=case_id, foundation_id=foundation_id).first()


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
        except (WalletNotActiveError, CurrencyMismatchError) as e:
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
        return WalletRefundRequest.objects.filter(id=refund_id, foundation_id=foundation_id).first()


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
