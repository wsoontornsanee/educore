from django.test import TestCase

from apps.notifications.models import CATEGORY_CONFIG, NotificationCategory, NotificationPriority


class ClinicIncidentCategoryTests(TestCase):
    def test_clinic_incident_category_exists(self):
        self.assertEqual(NotificationCategory.CLINIC_INCIDENT, 'CLINIC_INCIDENT')

    def test_clinic_incident_config(self):
        config = CATEGORY_CONFIG[NotificationCategory.CLINIC_INCIDENT]
        self.assertEqual(config['priority'], NotificationPriority.HIGH)
        self.assertFalse(config['quiet_hours_respected'])
        self.assertTrue(config['opt_out_allowed'])
