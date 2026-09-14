"""Tests for explicit audit logging and domain events (ARC-008, ARC-010, spec/01 §8.5)."""
from django.test import TestCase
from apps.core.models import AuditEvent, DomainEvent
from apps.core.services import audit, record_domain_event
from educore.middleware.tenancy import tenant_context

class AuditAndEventsTests(TestCase):
    def test_explicit_audit_event_logging(self):
        """Verify audit helper creates immutable AuditEvent record."""
        event = audit(
            action="finance.invoice.issued",
            entity_type="Invoice",
            entity_id="12345",
            actor_id="user_admin_01",
            role="foundation_admin",
            foundation_id=999,
            school_id=888,
            ip_address="192.168.1.100",
            diff={"total": {"before": "0.00", "after": "1500000.00"}},
        )

        self.assertIsNotNone(event.id)
        saved = AuditEvent.objects.get(id=event.id)
        self.assertEqual(saved.action, "finance.invoice.issued")
        self.assertEqual(saved.entity_type, "Invoice")
        self.assertEqual(saved.entity_id, "12345")
        self.assertEqual(saved.actor_id, "user_admin_01")
        self.assertEqual(saved.diff["total"]["after"], "1500000.00")

    def test_record_domain_event(self):
        """Verify domain events recorded transactionally with foundation scope."""
        with tenant_context(777):
            event = record_domain_event(
                name="attendance.gate_scan.recorded",
                payload={"student_id": "STU-001", "gate": "GATE_NORTH_01"}
            )

        self.assertIsNotNone(event.id)
        self.assertEqual(event.foundation_id, 777)
        self.assertEqual(event.name, "attendance.gate_scan.recorded")
        self.assertIsNone(event.processed_at)
