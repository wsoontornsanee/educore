from decimal import Decimal
from django.test import TestCase
from apps.finance.services import calculate_idr_rounding


class IDRRoundingTests(TestCase):
    """Test PEMBULATAN rules rounding IDR amounts to nearest Rp 100 per FIN-008c and CUR-019."""

    def test_exact_multiples_require_no_rounding(self):
        self.assertEqual(calculate_idr_rounding(Decimal('750000.00')), Decimal('0.00'))
        self.assertEqual(calculate_idr_rounding(Decimal('100.00')), Decimal('0.00'))
        self.assertEqual(calculate_idr_rounding(Decimal('0.00')), Decimal('0.00'))

    def test_round_down_when_remainder_less_than_fifty(self):
        # 750,030 -> adjustment -30 -> 750,000
        rounding = calculate_idr_rounding(Decimal('750030.00'))
        self.assertEqual(rounding, Decimal('-30.00'))
        self.assertEqual(Decimal('750030.00') + rounding, Decimal('750000.00'))

        # 750,049 -> adjustment -49 -> 750,000
        rounding_49 = calculate_idr_rounding(Decimal('750049.00'))
        self.assertEqual(rounding_49, Decimal('-49.00'))
        self.assertEqual(Decimal('750049.00') + rounding_49, Decimal('750000.00'))

        # 750,001 -> adjustment -1 -> 750,000
        rounding_1 = calculate_idr_rounding(Decimal('750001.00'))
        self.assertEqual(rounding_1, Decimal('-1.00'))
        self.assertEqual(Decimal('750001.00') + rounding_1, Decimal('750000.00'))

    def test_round_up_when_remainder_fifty_or_more(self):
        # 750,050 -> adjustment +50 -> 750,100
        rounding_50 = calculate_idr_rounding(Decimal('750050.00'))
        self.assertEqual(rounding_50, Decimal('50.00'))
        self.assertEqual(Decimal('750050.00') + rounding_50, Decimal('750100.00'))

        # 750,075 -> adjustment +25 -> 750,100
        rounding_75 = calculate_idr_rounding(Decimal('750075.00'))
        self.assertEqual(rounding_75, Decimal('25.00'))
        self.assertEqual(Decimal('750075.00') + rounding_75, Decimal('750100.00'))

        # 750,099 -> adjustment +1 -> 750,100
        rounding_99 = calculate_idr_rounding(Decimal('750099.00'))
        self.assertEqual(rounding_99, Decimal('1.00'))
        self.assertEqual(Decimal('750099.00') + rounding_99, Decimal('750100.00'))
