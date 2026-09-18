# apps/status/tests/test_models.py
from django.test import TestCase
from apps.status.models import ServiceComponent


class ServiceComponentTests(TestCase):
    def test_create_component(self):
        component = ServiceComponent.objects.create(
            key='web_portal', name_id='Portal web', name_en='Web portal',
            note_id='Dasbor yayasan', note_en='Foundation dashboard', display_order=1,
        )
        self.assertEqual(component.manual_status, None)
        self.assertEqual(str(component), 'Portal web')

    def test_key_is_unique(self):
        ServiceComponent.objects.create(key='web_portal', name_id='A', name_en='A')
        with self.assertRaises(Exception):
            ServiceComponent.objects.create(key='web_portal', name_id='B', name_en='B')

    def test_ordering_by_display_order(self):
        c2 = ServiceComponent.objects.create(key='c2', name_id='C2', name_en='C2', display_order=2)
        c1 = ServiceComponent.objects.create(key='c1', name_id='C1', name_en='C1', display_order=1)
        self.assertEqual(list(ServiceComponent.objects.all()), [c1, c2])
