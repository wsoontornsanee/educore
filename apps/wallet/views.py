from django.utils.translation import gettext_lazy as _
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.pagination import StandardCursorPagination
from apps.identity.models import Student
from apps.identity.permissions import HasRequiredPermission
from educore.middleware.tenancy import get_current_foundation_id

from apps.wallet.models import WalletTransaction
from apps.wallet.serializers import (
    SpendRuleSerializer,
    TopupSerializer,
    WalletSerializer,
    WalletTransactionSerializer,
)
from apps.wallet.services import (
    CurrencyMismatchError,
    InsufficientBalanceError,
    WalletNotActiveError,
    get_or_create_spend_rule,
    get_or_create_wallet,
    set_spend_rule,
    topup_wallet,
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
