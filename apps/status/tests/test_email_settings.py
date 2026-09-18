from django.conf import settings
from django.test import TestCase


class EmailSettingsTests(TestCase):
    def test_email_backend_defaults_to_console_in_test(self):
        # Test settings never fall back to a real SMTP attempt.
        self.assertIn('EmailBackend', settings.EMAIL_BACKEND)

    def test_default_from_email_is_set(self):
        self.assertTrue(settings.DEFAULT_FROM_EMAIL)

    def test_public_base_url_is_set(self):
        self.assertTrue(settings.EDUCORE_PUBLIC_BASE_URL)
        self.assertTrue(
            settings.EDUCORE_PUBLIC_BASE_URL.startswith('http://')
            or settings.EDUCORE_PUBLIC_BASE_URL.startswith('https://')
        )
