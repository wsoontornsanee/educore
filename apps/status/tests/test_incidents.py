from django.test import TestCase
from django.utils import timezone

from apps.core.models import AuditEvent
from apps.identity.models import Foundation, User
from apps.status.models import ServiceComponent, StatusIncident
from apps.status.services import create_incident, update_incident, list_published_incidents


class IncidentServiceTests(TestCase):
    def setUp(self):
        self.foundation = Foundation.objects.create(
            legal_name='Yayasan Status Test',
            brand_name='Yayasan Status Test',
            npwp='01.222.333.4-000.000',
            address='Jakarta',
        )
        self.actor = User.objects.create(
            foundation_id=self.foundation.id, phone_e164='+6281200000005', full_name='Ops Actor',
        )
        self.component = ServiceComponent.objects.create(key='test_c1', name_id='C1', name_en='C1')

    def test_create_incident_writes_audit_event(self):
        incident = create_incident(
            severity=StatusIncident.SEVERITY_MINOR,
            title_id='Judul', title_en='Title',
            body_id='Isi', body_en='Body',
            occurred_at=timezone.now(), duration_minutes=30,
            affected_component_ids=[self.component.id], published=True, actor=self.actor,
        )
        self.assertEqual(incident.created_by, self.actor)
        self.assertIn(self.component, incident.affected_components.all())
        event = AuditEvent.objects.get(action='status.incident.created', entity_id=str(incident.id))
        self.assertEqual(event.actor_id, str(self.actor.id))

    def test_update_incident_writes_audit_event_and_sets_updated_by(self):
        incident = create_incident(
            severity=StatusIncident.SEVERITY_MINOR, title_id='A', title_en='A',
            body_id='A', body_en='A', occurred_at=timezone.now(), duration_minutes=10,
            affected_component_ids=[], published=False, actor=self.actor,
        )
        other_actor = User.objects.create(
            foundation_id=self.foundation.id, phone_e164='+6281200000006', full_name='Editor',
        )
        updated = update_incident(incident, actor=other_actor, published=True)
        self.assertTrue(updated.published)
        self.assertEqual(updated.updated_by, other_actor)
        self.assertTrue(AuditEvent.objects.filter(action='status.incident.updated', entity_id=str(incident.id)).exists())

    def test_list_published_incidents_excludes_unpublished_and_orders_newest_first(self):
        old = create_incident(
            severity=StatusIncident.SEVERITY_MAINTENANCE, title_id='Old', title_en='Old',
            body_id='Old', body_en='Old', occurred_at=timezone.now() - timezone.timedelta(days=5),
            duration_minutes=5, affected_component_ids=[], published=True, actor=self.actor,
        )
        new = create_incident(
            severity=StatusIncident.SEVERITY_MAJOR, title_id='New', title_en='New',
            body_id='New', body_en='New', occurred_at=timezone.now(),
            duration_minutes=5, affected_component_ids=[], published=True, actor=self.actor,
        )
        create_incident(
            severity=StatusIncident.SEVERITY_MINOR, title_id='Draft', title_en='Draft',
            body_id='Draft', body_en='Draft', occurred_at=timezone.now(),
            duration_minutes=5, affected_component_ids=[], published=False, actor=self.actor,
        )
        result = list(list_published_incidents())
        self.assertEqual(result, [new, old])
