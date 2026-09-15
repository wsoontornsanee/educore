"""Tests for MoneyField and monetary rules (CUR-001, CUR-005)."""
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import connection, models
from django.test import TransactionTestCase
from apps.core.fields import MoneyField

class MoneyHolder(models.Model):
    title = models.CharField(max_length=50)
    amount = MoneyField()

    class Meta:
        app_label = 'core'
        db_table = 'test_money_holder'

class MoneyFieldTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Use the schema editor against the model's own field definitions
        # rather than hand-written DDL — hand-written SQLite dialect SQL
        # (AUTOINCREMENT, bare INTEGER PRIMARY KEY) breaks on MySQL, and this
        # way the throwaway table can never drift from what MoneyHolder
        # actually declares.
        with connection.schema_editor(atomic=False) as schema_editor:
            schema_editor.create_model(MoneyHolder)

    @classmethod
    def tearDownClass(cls):
        with connection.schema_editor(atomic=False) as schema_editor:
            schema_editor.delete_model(MoneyHolder)
        super().tearDownClass()

    def test_money_field_parameters(self):
        """CUR-001: MoneyField must enforce max_digits=18 and decimal_places=2."""
        field = MoneyHolder._meta.get_field('amount')
        self.assertEqual(field.max_digits, 18)
        self.assertEqual(field.decimal_places, 2)

        # Attempting custom incorrect parameters must raise ValueError
        with self.assertRaises(ValueError):
            MoneyField(max_digits=10, decimal_places=4)

    def test_rejection_of_float(self):
        """CUR-005: Passing a float to MoneyField must fail to prevent precision loss."""
        holder = MoneyHolder(title="Float Test", amount=1500000.50)
        with self.assertRaises((ValidationError, ValueError)):
            holder.full_clean()

    def test_valid_decimal_storage(self):
        """Valid Decimal or numeric string saves correctly."""
        holder = MoneyHolder.objects.create(
            title="SPP Bulanan",
            amount=Decimal("1500000.00")
        )
        holder.refresh_from_db()
        self.assertEqual(holder.amount, Decimal("1500000.00"))

    def test_string_coercion_on_save(self):
        """String numerical representation converts safely to Decimal."""
        holder = MoneyHolder.objects.create(
            title="Pendaftaran",
            amount="2500000.00"
        )
        holder.refresh_from_db()
        self.assertIsInstance(holder.amount, Decimal)
        self.assertEqual(holder.amount, Decimal("2500000.00"))
