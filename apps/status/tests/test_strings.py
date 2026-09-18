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

    def test_st_m3_label_matches_the_metric_it_actually_reports(self):
        # st_m3 is populated from get_open_component_count() — a count of
        # non-operational COMPONENTS, not open incidents — so its copy must
        # say so, not claim to be an incident count.
        self.assertEqual(STATUS_STRINGS['ID']['st_m3'], 'Komponen terganggu')
        self.assertEqual(STATUS_STRINGS['EN']['st_m3'], 'Affected components')
