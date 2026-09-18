# apps/status/tests/test_models.py
from django.test import TestCase
from apps.status.models import ServiceComponent


class ServiceComponentTests(TestCase):
    def test_create_component(self):
        component = ServiceComponent.objects.create(
            key='test_component_a', name_id='Komponen A', name_en='Component A',
            note_id='Catatan A', note_en='Note A', display_order=1,
        )
        self.assertEqual(component.manual_status, None)
        self.assertEqual(str(component), 'Komponen A')

    def test_key_is_unique(self):
        ServiceComponent.objects.create(key='test_component_b', name_id='A', name_en='A')
        with self.assertRaises(Exception):
            ServiceComponent.objects.create(key='test_component_b', name_id='B', name_en='B')

    def test_ordering_by_display_order(self):
        c2 = ServiceComponent.objects.create(key='test_component_c2', name_id='C2', name_en='C2', display_order=2)
        c1 = ServiceComponent.objects.create(key='test_component_c1', name_id='C1', name_en='C1', display_order=1)
        ordered = list(ServiceComponent.objects.filter(key__in=['test_component_c1', 'test_component_c2']))
        self.assertEqual(ordered, [c1, c2])
