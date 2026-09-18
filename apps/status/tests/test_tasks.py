from django.core import mail
from django.test import TestCase
from django.utils import timezone

from apps.core.services import get_task_handler
from apps.status.models import ServiceComponent, StatusIncident, StatusSubscriber
from apps.status.tasks import send_subscriber_incident_email


class SendSubscriberIncidentEmailTests(TestCase):
    def setUp(self):
        self.incident = StatusIncident.objects.create(
            severity=StatusIncident.SEVERITY_MAJOR,
            title_id='Gangguan API', title_en='API Outage',
            body_id='API mengalami gangguan.', body_en='API is experiencing an outage.',
            occurred_at=timezone.now(), duration_minutes=45, published=True,
        )
        self.subscriber = StatusSubscriber.objects.create(email='ortu@example.com')

    def test_sends_email_with_incident_details(self):
        send_subscriber_incident_email({
            'incident_id': self.incident.id,
            'subscriber_id': self.subscriber.id,
        })
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, ['ortu@example.com'])
        self.assertIn('Gangguan API', sent.subject)
        self.assertIn('API mengalami gangguan.', sent.body)

    def test_includes_unsubscribe_link(self):
        send_subscriber_incident_email({
            'incident_id': self.incident.id,
            'subscriber_id': self.subscriber.id,
        })
        sent = mail.outbox[0]
        expected_link = f'/status/unsubscribe/{self.subscriber.unsubscribe_token}/'
        self.assertIn(expected_link, sent.body)

    def test_noop_when_subscriber_already_unsubscribed(self):
        subscriber_id = self.subscriber.id
        self.subscriber.delete()
        send_subscriber_incident_email({
            'incident_id': self.incident.id,
            'subscriber_id': subscriber_id,
        })
        self.assertEqual(len(mail.outbox), 0)

    def test_raises_when_incident_missing(self):
        with self.assertRaises(StatusIncident.DoesNotExist):
            send_subscriber_incident_email({
                'incident_id': 999999,
                'subscriber_id': self.subscriber.id,
            })


class TaskHandlerRegistrationTests(TestCase):
    def test_handler_is_registered_via_app_registry(self):
        # Regression: catches the case where apps/status/tasks.py is never
        # imported at process startup, so drain_tasks finds no handler and
        # every subscriber email task dead-letters silently.
        self.assertIsNotNone(get_task_handler('status.subscriber_email.send'))
