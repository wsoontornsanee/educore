from django.test import TestCase
from apps.status.models import StatusSubscriber
from apps.status.services import subscribe_email


class SubscribeEmailTests(TestCase):
    def test_new_email_creates_subscriber(self):
        subscriber = subscribe_email('parent@example.com')
        self.assertEqual(StatusSubscriber.objects.count(), 1)
        self.assertIsNotNone(subscriber.unsubscribe_token)

    def test_duplicate_email_is_idempotent(self):
        first = subscribe_email('parent@example.com')
        second = subscribe_email('parent@example.com')
        self.assertEqual(StatusSubscriber.objects.count(), 1)
        self.assertEqual(first.id, second.id)

    def test_unsubscribe_tokens_are_unique_across_subscribers(self):
        a = subscribe_email('a@example.com')
        b = subscribe_email('b@example.com')
        self.assertNotEqual(a.unsubscribe_token, b.unsubscribe_token)
