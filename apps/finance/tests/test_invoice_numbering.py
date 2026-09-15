from decimal import Decimal
import datetime
from django.test import TestCase
from django.db import transaction

from apps.identity.models import Foundation, School
from apps.finance.models import InvoiceNumberSequence
from apps.finance.services import generate_invoice_number
from educore.middleware.tenancy import set_current_foundation_id, clear_current_foundation_id


class InvoiceNumberingTests(TestCase):
    def setUp(self):
        clear_current_foundation_id()
        self.foundation = Foundation.objects.create(
            legal_name="Yayasan Generasi Gemilang",
            brand_name="Generasi Gemilang",
            npwp="01.234.567.8-123.000",
            address="Jakarta Selatan",
        )
        set_current_foundation_id(self.foundation.id)
        self.school = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SMA Generasi Gemilang",
            npsn="20400001",
            level=School.LEVEL_SMA,
        )

    def tearDown(self):
        clear_current_foundation_id()

    def test_sequential_gapless_allocation(self):
        """Verify format INV/{school_code}/{YYYY}/{NNNNNN} and sequential progression (FIN-004)."""
        year = 2026
        with transaction.atomic():
            num1 = generate_invoice_number(self.school, year)
            num2 = generate_invoice_number(self.school, year)
            num3 = generate_invoice_number(self.school, year)

        self.assertEqual(num1, "INV/20400001/2026/000001")
        self.assertEqual(num2, "INV/20400001/2026/000002")
        self.assertEqual(num3, "INV/20400001/2026/000003")

        seq = InvoiceNumberSequence.all_tenants.get(
            foundation_id=self.foundation.id,
            school=self.school,
            year=year
        )
        self.assertEqual(seq.last_number, 3)

    def test_yearly_sequence_reset(self):
        """Verify sequence resets per calendar year."""
        with transaction.atomic():
            num_2026 = generate_invoice_number(self.school, 2026)
            num_2027 = generate_invoice_number(self.school, 2027)

        self.assertEqual(num_2026, "INV/20400001/2026/000001")
        self.assertEqual(num_2027, "INV/20400001/2027/000001")

    def test_school_isolation_in_sequence(self):
        """Verify different schools have independent sequence numbers."""
        school2 = School.all_tenants.create(
            foundation_id=self.foundation.id,
            name="SMP Generasi Gemilang",
            npsn="20400002",
            level=School.LEVEL_SMP,
        )
        with transaction.atomic():
            s1_num = generate_invoice_number(self.school, 2026)
            s2_num = generate_invoice_number(school2, 2026)

        self.assertEqual(s1_num, "INV/20400001/2026/000001")
        self.assertEqual(s2_num, "INV/20400002/2026/000001")
