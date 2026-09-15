from unittest.mock import patch
from django.test import TestCase

from apps.identity.models import Foundation, School, User
from apps.notifications.models import CATEGORY_CONFIG, IntentStatus, NotificationCategory
from apps.notifications.providers import MockPushProvider, MockWhatsAppProvider, register_provider
from apps.notifications.services import dispatch_intent
from educore.middleware.tenancy import set_current_foundation_id, tenant_context


def _always_true(intent):
    return True


def _always_false(intent):
    return False


def _raises(intent):
    raise RuntimeError("boom")


class SendTimeValidatorTests(TestCase):
    """NTF-004: process_intent's generic CATEGORY_CONFIG['send_time_validator'] dispatch."""

    def setUp(self):
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Validator", brand_name="Validator", npwp="01.111.222.3-444.000", address="Jakarta",
        )
        set_current_foundation_id(self.foundation.id)
        self.school = School.all_tenants.create(foundation_id=self.foundation.id, name="SMP Validator", npsn="20300001", level=School.LEVEL_SMP)
        self.parent_user = User.objects.create(
            foundation_id=self.foundation.id, phone_e164="+6281111111111", email="ortu@example.sch.id", full_name="Ortu Validator",
        )
        register_provider('WHATSAPP', MockWhatsAppProvider())
        register_provider('PUSH', MockPushProvider())

    def _dispatch(self):
        with tenant_context(self.foundation.id):
            return dispatch_intent(
                foundation_id=self.foundation.id,
                school_id=self.school.id,
                recipient_user=self.parent_user,
                recipient_phone="+6281111111111",
                category=NotificationCategory.ARRIVAL,
                template_key='attendance.arrival',
                payload={'student_name': 'Sari', 'school_name': 'SMP Validator', 'gate_name': 'Gerbang', 'time': '07:00'},
                immediate=True,
            )

    def test_no_validator_configured_dispatches_normally(self):
        self.assertNotIn('send_time_validator', CATEGORY_CONFIG[NotificationCategory.ARRIVAL])
        intent = self._dispatch()
        self.assertEqual(intent.status, IntentStatus.DISPATCHED)

    def test_validator_returning_true_dispatches(self):
        with patch.dict(CATEGORY_CONFIG[NotificationCategory.ARRIVAL], {
            'send_time_validator': 'apps.notifications.tests.test_send_time_validator._always_true',
        }):
            intent = self._dispatch()
        self.assertEqual(intent.status, IntentStatus.DISPATCHED)

    def test_validator_returning_false_cancels(self):
        with patch.dict(CATEGORY_CONFIG[NotificationCategory.ARRIVAL], {
            'send_time_validator': 'apps.notifications.tests.test_send_time_validator._always_false',
            'send_time_cancelled_reason': 'test cancelled',
        }):
            intent = self._dispatch()
        self.assertEqual(intent.status, IntentStatus.CANCELLED)
        self.assertEqual(intent.cancellation_reason, 'test cancelled')

    def test_validator_raising_fails_open_and_still_dispatches(self):
        with patch.dict(CATEGORY_CONFIG[NotificationCategory.ARRIVAL], {
            'send_time_validator': 'apps.notifications.tests.test_send_time_validator._raises',
        }):
            intent = self._dispatch()
        self.assertEqual(intent.status, IntentStatus.DISPATCHED)

    def test_unknown_validator_path_fails_open(self):
        with patch.dict(CATEGORY_CONFIG[NotificationCategory.ARRIVAL], {
            'send_time_validator': 'apps.notifications.tests.test_send_time_validator._does_not_exist',
        }):
            intent = self._dispatch()
        self.assertEqual(intent.status, IntentStatus.DISPATCHED)
