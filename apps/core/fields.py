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


def soft_delete_uniqueness_marker(extra_condition=None):
    """GeneratedField emulating a conditional UniqueConstraint on MySQL.

    `UniqueConstraint(condition=...)` is only enforced on Postgres/SQLite —
    Django silently drops it on MySQL (system check W036), which every
    TenantModel with a soft-delete-scoped unique constraint relies on. This
    collapses to 0 for rows the constraint should treat as "active" (where
    the real conditional constraint would have applied) and to SQL NULL
    otherwise. A plain (non-conditional) UniqueConstraint whose `fields`
    includes this marker then enforces uniqueness only among the 0 rows,
    because every SQL backend (MySQL included) treats NULL as never equal to
    NULL in a unique index — any number of "outside the condition" rows can
    carry a NULL marker without ever colliding, exactly matching "not
    enforced outside the condition". (An earlier version used the row's own
    `id` as the non-matching value instead of NULL — MySQL rejects that:
    "Generated column ... cannot refer to auto-increment column".)

    `extra_condition` ANDs in additional Q() clauses beyond `deleted_at__isnull=True`
    for constraints scoped to more than soft-delete state alone (e.g. ReportCard's
    "current" constraint).
    """
    condition = models.Q(deleted_at__isnull=True)
    if extra_condition is not None:
        condition &= extra_condition
    return models.GeneratedField(
        expression=models.Case(
            models.When(condition, then=models.Value(0)),
            default=models.Value(None),
            output_field=models.IntegerField(),
        ),
        output_field=models.IntegerField(),
        db_persist=True,
        null=True,
    )
