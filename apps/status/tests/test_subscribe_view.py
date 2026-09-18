from django.test import TestCase
from django.urls import reverse

from apps.core.throttling import StatusSubscribeThrottle
from apps.status.models import StatusSubscriber
from apps.status.views import StatusSubscribeView


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

    def test_malformed_email_rejected(self):
        # Fix 4: EmailField's validation never runs via a raw
        # get_or_create — this endpoint must validate the format itself.
        response = self.client.post(reverse('status:subscribe'), {'email': 'not-an-email'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(StatusSubscriber.objects.count(), 0)

    def test_malformed_email_missing_domain_rejected(self):
        response = self.client.post(reverse('status:subscribe'), {'email': 'foo@'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(StatusSubscriber.objects.count(), 0)

    def test_throttle_class_is_wired(self):
        # Fix 4: this is a public, unauthenticated write endpoint and must be
        # rate-limited like this repo's other public write surfaces
        # (apps.core.throttling.OtpRequestIPThrottle precedent). A full
        # rate-limit-triggering test would rely on DRF's cross-request cache
        # state and risk flakiness (see apps/core/throttling.py's module
        # docstring), so we assert the throttle is actually attached instead.
        self.assertIn(StatusSubscribeThrottle, StatusSubscribeView.throttle_classes)
        self.assertEqual(StatusSubscribeView.throttle_scope, 'status_subscribe')
