from unittest.mock import patch, MagicMock

from django.test import TestCase, override_settings

from apps.hardware.models import Device, DeviceClass, DeviceStatus
from apps.identity.models import Foundation, School
from apps.status.models import ServiceComponent
from apps.status.probes import probe_payments, probe_whatsapp, probe_canteen_pos, PROBES
from educore.middleware.tenancy import clear_current_foundation_id, set_current_foundation_id


class ProbePaymentsTests(TestCase):
    @override_settings(XENDIT_API_KEY='')
    def test_returns_none_when_unconfigured(self):
        self.assertIsNone(probe_payments())

    @override_settings(XENDIT_API_KEY='test-key', XENDIT_BASE_URL='https://api.xendit.co')
    @patch('apps.status.probes.requests.get')
    def test_returns_operational_on_2xx(self, mock_get):
        mock_get.return_value = MagicMock(ok=True, status_code=200)
        status, latency_ms = probe_payments()
        self.assertEqual(status, ServiceComponent.STATUS_OPERATIONAL)
        self.assertIsInstance(latency_ms, int)
        mock_get.assert_called_once()
        called_url = mock_get.call_args[0][0]
        self.assertEqual(called_url, 'https://api.xendit.co/balance')
        self.assertEqual(mock_get.call_args[1]['auth'], ('test-key', ''))
        self.assertEqual(mock_get.call_args[1]['timeout'], 5)

    @override_settings(XENDIT_API_KEY='test-key')
    @patch('apps.status.probes.requests.get')
    def test_returns_down_on_non_2xx(self, mock_get):
        mock_get.return_value = MagicMock(ok=False, status_code=500)
        status, _latency_ms = probe_payments()
        self.assertEqual(status, ServiceComponent.STATUS_DOWN)

    @override_settings(XENDIT_API_KEY='test-key')
    @patch('apps.status.probes.requests.get')
    def test_returns_down_on_request_exception(self, mock_get):
        import requests
        mock_get.side_effect = requests.ConnectionError("boom")
        status, _latency_ms = probe_payments()
        self.assertEqual(status, ServiceComponent.STATUS_DOWN)


class ProbeWhatsappTests(TestCase):
    @override_settings(WHATSAPP_API_TOKEN='', WHATSAPP_PHONE_NUMBER_ID='')
    def test_returns_none_when_unconfigured(self):
        self.assertIsNone(probe_whatsapp())

    @override_settings(WHATSAPP_API_TOKEN='tok', WHATSAPP_PHONE_NUMBER_ID='')
    def test_returns_none_when_only_token_set(self):
        self.assertIsNone(probe_whatsapp())

    @override_settings(WHATSAPP_API_TOKEN='tok', WHATSAPP_PHONE_NUMBER_ID='12345')
    @patch('apps.status.probes.requests.get')
    def test_returns_operational_on_2xx(self, mock_get):
        mock_get.return_value = MagicMock(ok=True, status_code=200)
        status, latency_ms = probe_whatsapp()
        self.assertEqual(status, ServiceComponent.STATUS_OPERATIONAL)
        self.assertIsInstance(latency_ms, int)
        called_url = mock_get.call_args[0][0]
        self.assertEqual(called_url, 'https://graph.facebook.com/v19.0/12345')
        self.assertEqual(mock_get.call_args[1]['params'], {'fields': 'id'})
        self.assertEqual(mock_get.call_args[1]['headers'], {'Authorization': 'Bearer tok'})
        self.assertEqual(mock_get.call_args[1]['timeout'], 5)

    @override_settings(WHATSAPP_API_TOKEN='tok', WHATSAPP_PHONE_NUMBER_ID='12345')
    @patch('apps.status.probes.requests.get')
    def test_returns_down_on_non_2xx(self, mock_get):
        mock_get.return_value = MagicMock(ok=False, status_code=401)
        status, _latency_ms = probe_whatsapp()
        self.assertEqual(status, ServiceComponent.STATUS_DOWN)


class ProbeCanteenPosTests(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Uji", brand_name="Uji",
            npwp="01.111.222.3-444.000", status=Foundation.STATUS_ACTIVE,
        )
        set_current_foundation_id(self.foundation.id)
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id, name="SMP Uji", npsn="30500099",
            level=School.LEVEL_SMP, timezone='Asia/Jakarta',
        )

    def tearDown(self):
        clear_current_foundation_id()

    def _make_pos(self, status, device_code):
        return Device.objects.create(
            foundation_id=self.foundation.id, school=self.school, device_code=device_code,
            name="Kasir Kantin", device_class=DeviceClass.POS_TERMINAL, status=status,
        )

    def test_returns_none_when_no_pos_devices(self):
        self.assertIsNone(probe_canteen_pos())

    def test_all_online_is_operational(self):
        self._make_pos(DeviceStatus.ONLINE, 'POS-1')
        self._make_pos(DeviceStatus.ONLINE, 'POS-2')
        status, latency_ms = probe_canteen_pos()
        self.assertEqual(status, ServiceComponent.STATUS_OPERATIONAL)
        self.assertIsNone(latency_ms)

    def test_minority_offline_is_degraded(self):
        self._make_pos(DeviceStatus.ONLINE, 'POS-1')
        self._make_pos(DeviceStatus.ONLINE, 'POS-2')
        self._make_pos(DeviceStatus.OFFLINE, 'POS-3')
        status, _latency_ms = probe_canteen_pos()
        self.assertEqual(status, ServiceComponent.STATUS_DEGRADED)

    def test_majority_offline_is_down(self):
        self._make_pos(DeviceStatus.ONLINE, 'POS-1')
        self._make_pos(DeviceStatus.OFFLINE, 'POS-2')
        self._make_pos(DeviceStatus.OFFLINE, 'POS-3')
        status, _latency_ms = probe_canteen_pos()
        self.assertEqual(status, ServiceComponent.STATUS_DOWN)

    def test_retired_devices_excluded_from_denominator(self):
        self._make_pos(DeviceStatus.ONLINE, 'POS-1')
        self._make_pos(DeviceStatus.RETIRED, 'POS-2')
        status, _latency_ms = probe_canteen_pos()
        self.assertEqual(status, ServiceComponent.STATUS_OPERATIONAL)

    def test_non_pos_device_class_ignored(self):
        Device.objects.create(
            foundation_id=self.foundation.id, school=self.school, device_code='GATE-1',
            name="Gerbang", device_class=DeviceClass.GATE_READER, status=DeviceStatus.OFFLINE,
        )
        self.assertIsNone(probe_canteen_pos())

    def test_aggregates_across_foundations(self):
        self._make_pos(DeviceStatus.ONLINE, 'POS-1')
        other_foundation = Foundation.objects.create(
            legal_name="Yayasan Lain", brand_name="Lain",
            npwp="01.999.888.7-666.000", status=Foundation.STATUS_ACTIVE,
        )
        set_current_foundation_id(other_foundation.id)
        other_school = School.all_tenants.create(
            foundation_id=other_foundation.id, name="SMP Lain", npsn="30500098",
            level=School.LEVEL_SMP, timezone='Asia/Jakarta',
        )
        Device.objects.create(
            foundation_id=other_foundation.id, school=other_school, device_code='POS-OTHER',
            name="Kasir Lain", device_class=DeviceClass.POS_TERMINAL, status=DeviceStatus.OFFLINE,
        )
        # Called with no ambient tenant context, matching the real cron process.
        clear_current_foundation_id()
        status, _latency_ms = probe_canteen_pos()
        self.assertEqual(status, ServiceComponent.STATUS_DEGRADED)


class ProbesRegistryTests(TestCase):
    def test_registry_maps_expected_component_keys(self):
        self.assertEqual(
            set(PROBES.keys()), {'payments', 'notifications', 'canteen_pos'},
        )
        self.assertIs(PROBES['payments'], probe_payments)
        self.assertIs(PROBES['notifications'], probe_whatsapp)
        self.assertIs(PROBES['canteen_pos'], probe_canteen_pos)
