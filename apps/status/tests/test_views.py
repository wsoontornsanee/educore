from django.conf import settings
from django.test import TestCase
from django.urls import resolve, reverse
from django.utils import timezone
from apps.status.models import ServiceComponent, DailyComponentStatus, StatusIncident
from apps.status.services import create_incident
from apps.identity.models import Foundation, User


class StatusPageViewTests(TestCase):
    def setUp(self):
        self.c1 = ServiceComponent.objects.create(key='c1', name_id='Portal', name_en='Portal', display_order=1)
        self.foundation = Foundation.objects.create(
            legal_name='Yayasan Status Page Test',
            brand_name='Yayasan Status Page Test',
            npwp='01.222.333.4-000.001',
            address='Jakarta',
        )
        self.actor = User.objects.create(
            foundation_id=self.foundation.id, phone_e164='+6281200000007', full_name='Ops',
        )

    def test_url_resolves_to_the_real_status_app_not_a_shadowing_route(self):
        """Regression guard: /status/ must resolve to apps.status.views.StatusPageView.

        PR #186 once registered a second, static apps.marketing route at the
        same 'status/' path, ahead of this app's include in educore/urls.py —
        Django's first-match-wins routing silently made this real,
        database-backed page unreachable. Reverted in the fix for that
        collision; this test exists so a future 'status/' route added to
        apps.marketing.urls (or a urls.py reorder) fails loudly instead of
        silently shadowing this page again.
        """
        match = resolve('/status/')
        self.assertEqual(match.view_name, 'status:page')

    def test_page_renders_200(self):
        response = self.client.get(reverse('status:page'))
        self.assertEqual(response.status_code, 200)

    def test_page_shows_component_name(self):
        response = self.client.get(reverse('status:page'))
        self.assertContains(response, 'Portal')

    def test_banner_reflects_all_operational(self):
        response = self.client.get(reverse('status:page'))
        self.assertContains(response, 'Semua sistem beroperasi normal')

    def test_banner_reflects_degraded_component(self):
        self.c1.manual_status = ServiceComponent.STATUS_DEGRADED
        self.c1.save()
        response = self.client.get(reverse('status:page'))
        self.assertContains(response, 'Sebagian sistem mengalami gangguan')

    def test_published_incident_shown_unpublished_hidden(self):
        create_incident(
            severity=StatusIncident.SEVERITY_MINOR, title_id='Kejadian tampil', title_en='Visible incident',
            body_id='x', body_en='x', occurred_at=timezone.now(), duration_minutes=5,
            affected_component_ids=[], published=True, actor=self.actor,
        )
        create_incident(
            severity=StatusIncident.SEVERITY_MINOR, title_id='Kejadian sembunyi', title_en='Hidden incident',
            body_id='x', body_en='x', occurred_at=timezone.now(), duration_minutes=5,
            affected_component_ids=[], published=False, actor=self.actor,
        )
        response = self.client.get(reverse('status:page'))
        self.assertContains(response, 'Kejadian tampil')
        self.assertNotContains(response, 'Kejadian sembunyi')

    def test_english_locale_query_param(self):
        response = self.client.get(reverse('status:page'), {'lang': 'EN'})
        self.assertContains(response, 'All systems operational')

    def test_site_active_language_drives_status_page_without_query_param(self):
        # Fix 6: the site's real active language must drive the page by
        # default — not just an explicit ?lang= query param. LocaleMiddleware
        # resolves the active language per-request from the django_language
        # cookie (see educore/middleware/i18n.py) — the same mechanism the
        # site-wide language switcher (django.views.i18n.set_language) uses —
        # so setting that cookie on the test client, not
        # translation.override() (which LocaleMiddleware would just
        # overwrite again while processing the request), is what actually
        # exercises this path end-to-end.
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'en'
        response = self.client.get(reverse('status:page'))
        self.assertContains(response, 'All systems operational')

    def test_explicit_lang_param_overrides_site_active_language(self):
        # An explicit ?lang=EN must still work even when the site's active
        # language is Indonesian (e.g. a status-page-specific link).
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'id'
        response = self.client.get(reverse('status:page'), {'lang': 'EN'})
        self.assertContains(response, 'All systems operational')

    def test_default_is_indonesian_when_site_language_is_indonesian(self):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'id'
        response = self.client.get(reverse('status:page'))
        self.assertContains(response, 'Semua sistem beroperasi normal')

    def test_invalid_manual_status_already_in_db_does_not_crash_public_page(self):
        # Defense-in-depth: a bogus manual_status that reached the DB by
        # some other route (e.g. Django admin's list_editable, which
        # bypasses choices validation on the list page) must not 500 the
        # public status page via a bare status_label/status_dot dict lookup.
        ServiceComponent.objects.filter(id=self.c1.id).update(manual_status='NOT_A_REAL_STATUS')
        response = self.client.get(reverse('status:page'))
        self.assertEqual(response.status_code, 200)

    def test_metrics_show_em_dash_when_no_data(self):
        # setUp creates no DailyComponentStatus/ComponentHeartbeat rows, so
        # get_uptime_percentage/get_average_latency_ms return None and the
        # template's em-dash fallback path should render.
        response = self.client.get(reverse('status:page'))
        self.assertContains(response, '—')  # em dash (U+2014)
