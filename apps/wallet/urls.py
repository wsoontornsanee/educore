from django.urls import path

from apps.wallet.views import (
    SpendRuleView,
    WalletDetailView,
    WalletTopupView,
    WalletTransactionsView,
)

urlpatterns = [
    path('wallets/<int:student_id>/', WalletDetailView.as_view(), name='wallet-detail'),
    path('wallets/<int:student_id>/transactions/', WalletTransactionsView.as_view(), name='wallet-transactions'),
    path('wallets/<int:student_id>/topup/', WalletTopupView.as_view(), name='wallet-topup'),
    path('wallets/<int:student_id>/rules/', SpendRuleView.as_view(), name='wallet-rules'),
]
