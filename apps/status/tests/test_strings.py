from django.test import TestCase
from apps.status.strings import STATUS_STRINGS, get_status_strings


class StatusStringsTests(TestCase):
    def test_both_locales_present(self):
        self.assertIn('ID', STATUS_STRINGS)
        self.assertIn('EN', STATUS_STRINGS)

    def test_id_and_en_have_identical_key_sets(self):
        self.assertEqual(set(STATUS_STRINGS['ID'].keys()), set(STATUS_STRINGS['EN'].keys()))

    def test_get_status_strings_defaults_to_id(self):
        self.assertEqual(get_status_strings('XX'), STATUS_STRINGS['ID'])
        self.assertEqual(get_status_strings(None), STATUS_STRINGS['ID'])

    def test_get_status_strings_returns_english(self):
        strings = get_status_strings('EN')
        self.assertEqual(strings['st_banner'], 'All systems operational')

    def test_indonesian_banner_copy(self):
        self.assertEqual(STATUS_STRINGS['ID']['st_banner'], 'Semua sistem beroperasi normal')
