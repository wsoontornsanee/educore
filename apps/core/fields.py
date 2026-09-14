"""Custom database fields for EduCore.

Implements CUR-001 & CUR-005 from spec/16-currency-and-money.md:
Every monetary column MUST use core.fields.MoneyField -> DECIMAL(18,2).
Float values are strictly forbidden to prevent precision loss.
"""
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import models

class MoneyField(models.DecimalField):
    """Custom DecimalField strictly enforcing 18 digits and 2 decimal places."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('max_digits', 18)
        kwargs.setdefault('decimal_places', 2)
        if kwargs.get('max_digits') != 18 or kwargs.get('decimal_places') != 2:
            raise ValueError("MoneyField must have max_digits=18 and decimal_places=2 per CUR-001.")
        super().__init__(*args, **kwargs)

    def to_python(self, value):
        if value is None:
            return value
        if isinstance(value, float):
            raise ValidationError(
                f"Float value {value!r} is forbidden for MoneyField. "
                "Use Python Decimal or string representation per CUR-005."
            )
        return super().to_python(value)

    def get_prep_value(self, value):
        if value is not None:
            if isinstance(value, float):
                raise ValueError(
                    f"Float value {value!r} cannot be saved to MoneyField. "
                    "Use Python Decimal or string per CUR-005."
                )
            if not isinstance(value, Decimal):
                value = Decimal(str(value))
        return super().get_prep_value(value)
