from django.test import TestCase
from django.urls import reverse

from apps.status.models import StatusSubscriber


class StatusSubscribeViewTests(TestCase):
    def test_post_valid_email_creates_subscriber_and_redirects(self):
        response = self.client.post(reverse('status:subscribe'), {'email': 'parent@example.com'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(StatusSubscriber.objects.count(), 1)

    def test_post_duplicate_email_does_not_error(self):
        self.client.post(reverse('status:subscribe'), {'email': 'parent@example.com'})
        response = self.client.post(reverse('status:subscribe'), {'email': 'parent@example.com'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(StatusSubscriber.objects.count(), 1)

    def test_post_missing_email_returns_400(self):
        response = self.client.post(reverse('status:subscribe'), {})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(StatusSubscriber.objects.count(), 0)

    def test_get_not_allowed(self):
        response = self.client.get(reverse('status:subscribe'))
        self.assertEqual(response.status_code, 405)
