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

    def test_privacy_policy_page_renders(self):
        response = self.client.get(reverse('marketing:privacy-policy'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Kebijakan Privasi')
        self.assertContains(response, 'support@makan.live')

    def test_dpa_page_renders(self):
        response = self.client.get(reverse('marketing:dpa'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Perjanjian Pemrosesan Data')

    def test_data_retention_page_renders(self):
        response = self.client.get(reverse('marketing:data-retention'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Retensi Data')
        self.assertContains(response, '90 hari')

    def test_changelog_page_renders(self):
        response = self.client.get(reverse('marketing:changelog'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'v1.9')
        self.assertContains(response, 'v3.4.1')
        self.assertContains(response, 'v1.8')
        self.assertContains(response, 'v4.2')
        self.assertContains(response, 'GET /partner/staff/:id/positions')
        self.assertContains(response, '20 November 2026')

    def test_changelog_page_translates_to_english(self):
        self.client.cookies['django_language'] = 'en'
        response = self.client.get(reverse('marketing:changelog'))
        self.assertEqual(response.headers.get('Content-Language'), 'en')
        self.assertContains(response, 'What changed, and what you need to do.')
        self.assertContains(response, 'ACTION NEEDED')
        self.assertContains(response, 'NEW')
        self.assertContains(response, 'FIX')
        self.assertContains(response, 'CHANGED')
        self.assertNotContains(response, 'Apa yang berubah')

    def test_changelog_entries_carry_their_category_for_filtering(self):
        response = self.client.get(reverse('marketing:changelog'))
        self.assertContains(response, 'data-changelog-category="partner-api"')
        self.assertContains(response, 'data-changelog-category="portal-web"')
        self.assertContains(response, 'data-changelog-category="aplikasi-seluler"')

    def test_footer_no_longer_mailtos_compliance_pages(self):
        response = self.client.get(reverse('marketing:home'))
        self.assertContains(response, reverse('marketing:privacy-policy'))
        self.assertContains(response, reverse('marketing:dpa'))
        self.assertContains(response, reverse('marketing:data-retention'))

    def test_footer_links_changelog_to_the_real_page(self):
        response = self.client.get(reverse('marketing:home'))
        self.assertContains(response, reverse('marketing:changelog'))

    def test_pages_require_no_authentication(self):
        for name in (
            'marketing:home', 'marketing:downloads', 'marketing:partner-api',
            'marketing:privacy-policy', 'marketing:dpa', 'marketing:data-retention',
            'marketing:changelog',
        ):
            response = self.client.get(reverse(name))
            self.assertNotIn(response.status_code, (301, 302, 401, 403))

    def test_compliance_pages_render_indonesian_by_default(self):
        cases = {
            'marketing:privacy-policy': ('Kebijakan Privasi', 'pengendali data'),
            'marketing:dpa': ('Perjanjian Pemrosesan Data', 'Pemroses Data'),
            'marketing:data-retention': ('Retensi Data', '90 hari secara default'),
        }
        for name, (heading, body_text) in cases.items():
            response = self.client.get(reverse(name))
            self.assertEqual(response.headers.get('Content-Language'), 'id')
            self.assertContains(response, heading)
            self.assertContains(response, body_text)

    def test_compliance_pages_translate_to_english(self):
        self.client.cookies['django_language'] = 'en'
        cases = {
            'marketing:privacy-policy': ('Privacy Policy', 'data controller'),
            'marketing:dpa': ('Data Processing Agreement', 'Data Processor'),
            'marketing:data-retention': ('Data Retention', '90 days by default'),
        }
        for name, (heading, body_text) in cases.items():
            response = self.client.get(reverse(name))
            self.assertEqual(response.headers.get('Content-Language'), 'en')
            self.assertContains(response, heading)
            self.assertContains(response, body_text)
            self.assertNotContains(response, 'Kebijakan Privasi')

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
