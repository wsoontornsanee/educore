from django.test import TestCase
from django.urls import reverse
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

    def test_metrics_show_em_dash_when_no_data(self):
        # setUp creates no DailyComponentStatus/ComponentHeartbeat rows, so
        # get_uptime_percentage/get_average_latency_ms return None and the
        # template's em-dash fallback path should render.
        response = self.client.get(reverse('status:page'))
        self.assertContains(response, '—')  # em dash (U+2014)
