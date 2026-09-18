from django.test import SimpleTestCase

from apps.core.templatetags.console_format import money


class MoneyFilterTests(SimpleTestCase):
    def test_idr_uses_indonesian_grouping_without_decimals(self):
        self.assertEqual(money('1500000.00', 'IDR'), 'Rp 1.500.000')
        self.assertEqual(money('0.00'), 'Rp 0')

    def test_idr_rounds_half_up(self):
        self.assertEqual(money('1000.50', 'IDR'), 'Rp 1.001')

    def test_other_currency_shows_two_decimals(self):
        self.assertEqual(money('1500.5', 'USD'), 'USD 1,500.50')

    def test_garbage_renders_empty(self):
        self.assertEqual(money('abc'), '')
