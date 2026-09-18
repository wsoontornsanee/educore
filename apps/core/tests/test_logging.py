"""Tests for apps.core.logging: JSON formatting + universal PII scrubbing (AGENTS Red Line #5)."""
import json
import logging
import time

from django.test import SimpleTestCase

from apps.core.logging import JSONFormatter, PIIScrubbingFilter, scrub_value, sentry_before_send


def _make_record(msg, args=(), extra=None, exc_info=None):
    record = logging.LogRecord(
        name='educore.test', level=logging.INFO, pathname=__file__, lineno=1,
        msg=msg, args=args, exc_info=exc_info,
    )
    for key, value in (extra or {}).items():
        setattr(record, key, value)
    return record


class ScrubValueTests(SimpleTestCase):
    def test_redacts_nik(self):
        self.assertEqual(scrub_value('NIK siswa: 3171012345678901'), 'NIK siswa: [REDACTED_NIK]')

    def test_redacts_nisn(self):
        self.assertEqual(scrub_value('NISN: 1234567890'), 'NISN: [REDACTED_NISN]')

    def test_redacts_phone_variants(self):
        for phone in ('081234567890', '6281234567890', '+6281234567890'):
            with self.subTest(phone=phone):
                self.assertEqual(scrub_value(f'Telp: {phone}'), 'Telp: [REDACTED_PHONE]')

    def test_phone_not_misredacted_as_nisn(self):
        # A 10-digit phone (08 + 8 digits) must be tagged PHONE, not NISN.
        scrubbed = scrub_value('call 0812345678')
        self.assertIn('[REDACTED_PHONE]', scrubbed)
        self.assertNotIn('NISN', scrubbed)

    def test_redacts_sensitive_keys_in_dict(self):
        payload = {'password': 'hunter2', 'token': 'abc', 'note': 'ok', 'nested': {'secret': 'xyz'}}
        scrubbed = scrub_value(payload)
        self.assertEqual(scrubbed['password'], '[REDACTED]')
        self.assertEqual(scrubbed['token'], '[REDACTED]')
        self.assertEqual(scrubbed['note'], 'ok')
        self.assertEqual(scrubbed['nested']['secret'], '[REDACTED]')

    def test_redacts_sensitive_keys_in_list_of_dicts(self):
        scrubbed = scrub_value([{'cvv': '123'}, {'pin': '4567'}])
        self.assertEqual(scrubbed[0]['cvv'], '[REDACTED]')
        self.assertEqual(scrubbed[1]['pin'], '[REDACTED]')

    def test_non_string_values_pass_through(self):
        self.assertEqual(scrub_value(42), 42)
        self.assertIsNone(scrub_value(None))

    def test_redacts_nik_logged_as_bare_integer(self):
        # A BigIntegerField NIK logged via extra={'nik': student.nik} (an int,
        # not a string) must still be redacted, not silently passed through.
        self.assertEqual(scrub_value(3171012345678901), '[REDACTED_NIK]')

    def test_unrelated_integers_pass_through_unchanged(self):
        self.assertEqual(scrub_value(200), 200)
        self.assertEqual(scrub_value(12.5), 12.5)
        self.assertIs(scrub_value(True), True)

    def test_redacts_biometric_sensitive_key(self):
        scrubbed = scrub_value({'template_ciphertext': b'...', 'biometric': 'xyz'})
        self.assertEqual(scrubbed['template_ciphertext'], '[REDACTED]')
        self.assertEqual(scrubbed['biometric'], '[REDACTED]')

    def test_redacts_pii_glued_to_identifier_without_boundary(self):
        # \b treats "_" as a word char and would miss this; the digit-only
        # lookaround boundary must not.
        self.assertIn('[REDACTED_NIK]', scrub_value('nik_3171012345678901'))

    def test_scrubs_pii_inside_object_str_fallback(self):
        class FakeStudent:
            def __str__(self):
                return 'Siswa NIK 3171012345678901'

        scrubbed = scrub_value(FakeStudent())
        self.assertNotIn('3171012345678901', scrubbed)
        self.assertIn('[REDACTED_NIK]', scrubbed)


class PIIScrubbingFilterTests(SimpleTestCase):
    def setUp(self):
        self.filter = PIIScrubbingFilter()

    def test_scrubs_message_string(self):
        record = _make_record('Login gagal untuk NIK 3171012345678901')
        self.filter.filter(record)
        self.assertNotIn('3171012345678901', record.getMessage())
        self.assertIn('[REDACTED_NIK]', record.getMessage())

    def test_scrubs_message_args(self):
        record = _make_record('Phone %s failed', args=('081234567890',))
        self.filter.filter(record)
        self.assertIn('[REDACTED_PHONE]', record.getMessage())

    def test_scrubs_extra_payload_dict(self):
        record = _make_record('login attempt', extra={'password': 'hunter2', 'user_id': 7})
        self.filter.filter(record)
        self.assertEqual(record.password, '[REDACTED]')
        self.assertEqual(record.user_id, 7)

    def test_scrubs_exception_traceback(self):
        try:
            password = 'super-secret-value'  # noqa: F841
            raise ValueError('boom with NIK 3171012345678901')
        except ValueError:
            import sys
            record = _make_record('unhandled error', exc_info=sys.exc_info())
        self.filter.filter(record)
        self.assertIsNone(record.exc_info)
        self.assertNotIn('3171012345678901', record.exc_text)
        self.assertIn('[REDACTED_NIK]', record.exc_text)


class JSONFormatterTests(SimpleTestCase):
    def setUp(self):
        self.filter = PIIScrubbingFilter()
        self.formatter = JSONFormatter()

    def _format(self, record):
        self.filter.filter(record)
        return json.loads(self.formatter.format(record))

    def test_outputs_valid_json_with_core_fields(self):
        record = _make_record('hello world')
        payload = self._format(record)
        self.assertEqual(payload['level'], 'INFO')
        self.assertEqual(payload['logger'], 'educore.test')
        self.assertEqual(payload['message'], 'hello world')
        self.assertIn('timestamp', payload)
        self.assertTrue(payload['timestamp'].endswith('Z'))

    def test_includes_request_context_extras(self):
        record = _make_record('request handled', extra={
            'foundation_id': 1, 'user_id': 2, 'request_id': 'abc123',
            'path': '/api/v1/students', 'method': 'GET', 'status_code': 200,
            'duration_ms': 12.5,
        })
        payload = self._format(record)
        self.assertEqual(payload['foundation_id'], 1)
        self.assertEqual(payload['user_id'], 2)
        self.assertEqual(payload['request_id'], 'abc123')
        self.assertEqual(payload['path'], '/api/v1/students')
        self.assertEqual(payload['method'], 'GET')
        self.assertEqual(payload['status_code'], 200)
        self.assertEqual(payload['duration_ms'], 12.5)

    def test_scrubbed_message_never_reaches_json_output(self):
        record = _make_record('NIK bocor: 3171012345678901')
        payload = self._format(record)
        self.assertNotIn('3171012345678901', json.dumps(payload))


class SentryBeforeSendTests(SimpleTestCase):
    def test_scrubs_nested_event_payload(self):
        event = {
            'message': 'user NIK 3171012345678901 failed login',
            'extra': {'password': 'hunter2'},
            'request': {'headers': {'Authorization': 'Bearer xyz'}},
        }
        scrubbed = sentry_before_send(event, {})
        self.assertNotIn('3171012345678901', scrubbed['message'])
        self.assertEqual(scrubbed['extra']['password'], '[REDACTED]')
        self.assertEqual(scrubbed['request']['headers']['Authorization'], '[REDACTED]')


class ScrubPerformanceTests(SimpleTestCase):
    def test_scrub_overhead_under_one_millisecond(self):
        message = (
            'Siswa NIK 3171012345678901 NISN 1234567890 telp 081234567890 '
            'login dengan password=hunter2 token=abc123 pada endpoint /api/v1/students'
        )
        iterations = 1000
        started = time.perf_counter()
        for _ in range(iterations):
            scrub_value(message)
        elapsed_ms = (time.perf_counter() - started) * 1000 / iterations
        self.assertLess(elapsed_ms, 1.0, f'scrub_value took {elapsed_ms:.4f}ms/entry, expected < 1ms')
