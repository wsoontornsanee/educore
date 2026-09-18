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
        self.assertContains(response, 'payroll.run.approved')

    def test_partner_api_base_url_reflects_request_host(self):
        response = self.client.get(
            reverse('marketing:partner-api'), SERVER_NAME='app.educore.id'
        )
        self.assertContains(response, 'http://app.educore.id/api/v1/')

    def test_pages_require_no_authentication(self):
        for name in ('marketing:home', 'marketing:downloads', 'marketing:partner-api'):
            response = self.client.get(reverse(name))
            self.assertNotIn(response.status_code, (301, 302, 401, 403))
