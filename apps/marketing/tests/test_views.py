"""Smoke tests for the public marketing website — no auth, no tenancy."""
from django.test import TestCase
from django.urls import reverse


class MarketingPagesTests(TestCase):
    def test_home_page_renders(self):
        response = self.client.get(reverse('marketing:home'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Satu sistem untuk seluruh yayasan Anda.')
        self.assertContains(response, 'Kolektibilitas per sekolah')

    def test_downloads_page_renders(self):
        response = self.client.get(reverse('marketing:downloads'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Aplikasi Orang Tua')
        self.assertContains(response, 'Aplikasi Guru')
        self.assertContains(response, 'POS Kantin')
        self.assertContains(response, '/static/img/qr-orang-tua.png')
        self.assertContains(response, '/static/img/qr-guru.png')

    def test_partner_api_page_renders(self):
        response = self.client.get(reverse('marketing:partner-api'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'SCOPE_DENIED')
        self.assertContains(response, 'roster.staff.updated')
        self.assertNotContains(response, 'payroll')

    def test_partner_api_base_url_reflects_request_host(self):
        response = self.client.get(
            reverse('marketing:partner-api'), SERVER_NAME='app.educore.id'
        )
        self.assertContains(response, 'http://app.educore.id/api/v1/')

    def test_pages_require_no_authentication(self):
        for name in ('marketing:home', 'marketing:downloads', 'marketing:partner-api'):
            response = self.client.get(reverse(name))
            self.assertNotIn(response.status_code, (301, 302, 401, 403))

    def test_defaults_to_indonesian_regardless_of_browser_language(self):
        # A first-time visitor with no django_language cookie must see id-ID
        # (spec/appendix §2.10) even if their browser prefers English —
        # ForceDefaultLanguageMiddleware strips Accept-Language ahead of
        # LocaleMiddleware precisely to stop the browser header from
        # outranking the site's actual default.
        response = self.client.get(
            reverse('marketing:home'), HTTP_ACCEPT_LANGUAGE='en-US,en;q=0.9'
        )
        self.assertEqual(response.headers.get('Content-Language'), 'id')
        self.assertContains(response, 'Satu sistem untuk seluruh yayasan Anda.')

    def test_explicit_language_choice_overrides_browser_language(self):
        # Once a visitor has picked a language (set_language sets this
        # cookie), that choice must win over both Accept-Language and the
        # id-ID default on every later request.
        self.client.cookies['django_language'] = 'en'
        response = self.client.get(
            reverse('marketing:home'), HTTP_ACCEPT_LANGUAGE='id-ID'
        )
        self.assertEqual(response.headers.get('Content-Language'), 'en')
