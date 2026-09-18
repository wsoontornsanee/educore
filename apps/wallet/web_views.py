"""Web (session-auth HTMX) views for the Kantin & dompet console.

Read-only monitor over apps.wallet data (merchants, POS terminals, wallets,
POS transactions). Money movement — top-ups, refunds, reconciliation actions —
stays on the JSON API with its own finance.* permission keys.
"""
from django.shortcuts import render
from rest_framework.views import APIView

from apps.identity.console_access import StaffConsoleMixin

from .services import get_canteen_console_snapshot


class CanteenConsolePageView(StaffConsoleMixin, APIView):
    """GET /web/wallet/canteen/ — merchants, today's sales, wallet totals, recent POS sales."""

    def get_required_permission(self):
        return 'wallet.topup.read'

    def get(self, request):
        foundation_id, schools, school = self.console_context(request)
        snapshot = get_canteen_console_snapshot(foundation_id, school) if school else None
        return render(request, 'pages/canteen_console_page.html', {
            'schools': schools,
            'school': school,
            'snapshot': snapshot,
        })
