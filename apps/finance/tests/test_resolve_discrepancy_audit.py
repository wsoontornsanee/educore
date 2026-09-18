import datetime
from decimal import Decimal

from django.test import TestCase

from apps.core.models import AuditEvent
from apps.finance.models import DiscrepancyResolution, GatewaySettlementBatch, PaymentDiscrepancy
from apps.finance.services.reconciliation import resolve_discrepancy
from apps.finance.tests.test_web_console import make_foundation, make_user
from apps.identity.models import RoleAssignment


class ResolveDiscrepancyAuditTests(TestCase):
    def test_resolving_writes_an_audit_event(self):
        foundation, (school, _) = make_foundation('A')
        user = make_user(
            foundation, '+6281300007001', RoleAssignment.ROLE_FINANCE_OFFICER,
            RoleAssignment.SCOPE_FOUNDATION, foundation.id, staff_school=school,
        )
        batch = GatewaySettlementBatch.objects.create(
            foundation_id=foundation.id, provider='XENDIT', settlement_date=datetime.date(2026, 9, 1),
        )
        discrepancy = PaymentDiscrepancy.objects.create(
            foundation_id=foundation.id, batch=batch, external_id='EXT-1',
            discrepancy_type='MISSING_IN_SYSTEM', gateway_amount=Decimal('1000.00'),
        )

        resolve_discrepancy(
            discrepancy_id=discrepancy.id, resolution=DiscrepancyResolution.WAIVED,
            resolved_by=user, foundation_id=foundation.id, notes='tidak material',
        )

        event = AuditEvent.objects.get(
            action='finance.reconciliation.discrepancy_resolved', entity_id=str(discrepancy.id),
        )
        self.assertEqual(event.entity_type, 'PaymentDiscrepancy')
        self.assertEqual(event.foundation_id, foundation.id)
        self.assertEqual(event.actor_id, str(user.id))
        self.assertEqual(event.diff, {'resolution': 'WAIVED', 'notes': 'tidak material'})
