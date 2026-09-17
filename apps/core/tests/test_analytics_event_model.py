"""Tests for AnalyticsEvent model (spec/08 §5, spec/15 RPT-015)."""
from django.test import TestCase
from django.utils import timezone
from apps.core.models import AnalyticsEvent, ANALYTICS_EVENT_NAMES
from educore.middleware.tenancy import tenant_context


class AnalyticsEventModelTests(TestCase):
    def test_create_and_scope_by_foundation(self):
        with tenant_context(1):
            AnalyticsEvent.objects.create(
                foundation_id=1,
                event_name='app_open',
                school_id=None,
                role='parent',
                occurred_at=timezone.now(),
            )
        with tenant_context(2):
            AnalyticsEvent.objects.create(
                foundation_id=2,
                event_name='app_open',
                school_id=None,
                role='parent',
                occurred_at=timezone.now(),
            )

        with tenant_context(1):
            self.assertEqual(AnalyticsEvent.objects.count(), 1)
        with tenant_context(2):
            self.assertEqual(AnalyticsEvent.objects.count(), 1)

    def test_event_name_choices_cover_full_spec_list(self):
        self.assertEqual(
            set(ANALYTICS_EVENT_NAMES),
            {
                'app_open', 'child_switch', 'invoice_view', 'pay_start',
                'pay_method_selected', 'pay_intent_created', 'pay_completed',
                'topup_completed', 'absence_submitted', 'grades_view',
                'report_card_view', 'notification_opened',
            },
        )

    def test_model_has_no_pii_fields(self):
        field_names = {f.name for f in AnalyticsEvent._meta.get_fields()}
        for forbidden in ('actor_id', 'ip_address', 'diff', 'full_name', 'phone_e164'):
            self.assertNotIn(forbidden, field_names)
