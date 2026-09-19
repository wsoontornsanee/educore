"""Tests for apps.core.pii: the canonical PII-type registry (AGENTS Red Line #5).

apps.core.logging's scrub behavior is already exercised end-to-end by
test_logging.py (which imports scrub_value re-exported from here) — these
tests cover the registry's own structure and typed surface instead of
duplicating that coverage.
"""
from django.test import SimpleTestCase

from apps.core.pii import (
    BIOMETRIC_KEYS,
    CREDENTIAL_KEYS,
    PII_REGEX_PATTERNS,
    SENSITIVE_KEYS,
    PIIType,
    scrub_string,
)


class PIITypeRegistryTests(SimpleTestCase):
    def test_pii_type_has_expected_members(self):
        self.assertEqual(
            {member.value for member in PIIType},
            {'NIK', 'NISN', 'PHONE', 'BIOMETRIC', 'CREDENTIAL'},
        )

    def test_regex_patterns_cover_nik_phone_nisn_in_order(self):
        types_in_order = [pii_type for pii_type, _pattern, _replacement in PII_REGEX_PATTERNS]
        self.assertEqual(types_in_order, [PIIType.NIK, PIIType.PHONE, PIIType.NISN])

    def test_sensitive_keys_is_union_of_credential_and_biometric(self):
        self.assertEqual(SENSITIVE_KEYS, CREDENTIAL_KEYS | BIOMETRIC_KEYS)
        self.assertIn('password', CREDENTIAL_KEYS)
        self.assertIn('private_key', CREDENTIAL_KEYS)  # service-account key JSON
        self.assertIn('biometric', BIOMETRIC_KEYS)
        self.assertTrue(CREDENTIAL_KEYS.isdisjoint(BIOMETRIC_KEYS))


class ScrubStringTests(SimpleTestCase):
    def test_scrub_string_redacts_all_three_regex_types(self):
        text = 'NIK 3171012345678901 NISN 1234567890 telp 081234567890'
        scrubbed = scrub_string(text)
        self.assertNotIn('3171012345678901', scrubbed)
        self.assertNotIn('1234567890', scrubbed)
        self.assertIn('[REDACTED_NIK]', scrubbed)
        self.assertIn('[REDACTED_PHONE]', scrubbed)
