"""MockPushProvider must capture the variables dict it's given, so the
notification pipeline's payload is inspectable as the future push data
payload (spec/08 PAR-004)."""
from django.test import TestCase
from apps.notifications.providers import MockPushProvider


class MockPushProviderDataPayloadTests(TestCase):
    def test_send_records_variables_dict(self):
        provider = MockPushProvider()
        result = provider.send(
            recipient_target='device-token-abc',
            rendered_body='Anak Anda tiba di sekolah pukul 07:05.',
            rendered_subject='',
            template_key='attendance.arrival',
            variables={'type': 'ARRIVAL', 'student_id': 42, 'date': '2026-09-17'},
        )
        self.assertTrue(result.success)
        self.assertEqual(len(provider.dispatched_messages), 1)
        recorded = provider.dispatched_messages[0]
        self.assertEqual(
            recorded['variables'],
            {'type': 'ARRIVAL', 'student_id': 42, 'date': '2026-09-17'},
        )

    def test_send_records_empty_dict_when_variables_omitted(self):
        provider = MockPushProvider()
        provider.send(
            recipient_target='device-token-abc',
            rendered_body='Test',
        )
        self.assertEqual(provider.dispatched_messages[0]['variables'], {})
