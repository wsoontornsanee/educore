import uuid

from django.test import TestCase
from django.urls import reverse

from apps.status.models import StatusSubscriber


class UnsubscribeViewTests(TestCase):
    def test_valid_token_deletes_subscriber_and_returns_200(self):
        subscriber = StatusSubscriber.objects.create(email='ortu@example.com')
        token = subscriber.unsubscribe_token

        response = self.client.get(reverse('status:unsubscribe', args=[token]))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(StatusSubscriber.objects.filter(unsubscribe_token=token).exists())

    def test_unknown_token_still_returns_200_no_leak(self):
        unknown_token = uuid.uuid4()

        response = self.client.get(reverse('status:unsubscribe', args=[unknown_token]))

        self.assertEqual(response.status_code, 200)

    def test_response_renders_confirmation_template(self):
        subscriber = StatusSubscriber.objects.create(email='ortu@example.com')
        response = self.client.get(reverse('status:unsubscribe', args=[subscriber.unsubscribe_token]))
        self.assertTemplateUsed(response, 'status/unsubscribe.html')
