from django.utils.translation import gettext_lazy as _
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.pagination import StandardCursorPagination
from apps.identity.models import Student
from apps.identity.permissions import HasRequiredPermission
from educore.middleware.tenancy import get_current_foundation_id

from apps.wallet.models import (
    Merchant,
    MerchantSettlement,
    POSTerminal,
    POSTransaction,
    POSTransactionStatus,
    Product,
    WalletTransaction,
)
from apps.wallet.serializers import (
    MerchantSerializer,
    MerchantSettlementRunSerializer,
    MerchantSettlementSerializer,
    POSTerminalSerializer,
    POSTransactionCreateSerializer,
    POSTransactionSerializer,
    POSTransactionVoidSerializer,
    ProductSerializer,
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
    process_pos_transaction,
    run_merchant_settlement,
    set_spend_rule,
    topup_wallet,
    void_pos_transaction,
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
        'create': 'wallet.topup.write', 'void': 'wallet.topup.write',
    }

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
