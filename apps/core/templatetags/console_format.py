"""Template filters for the web console (spec/16 §CUR-026, spec/17).

Money crosses the API as a string amount + currency; the UI formats it for
display only. The console never sums money client-side — totals are computed
in the view/service and passed in already summed.
"""
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter
def money(amount, currency='IDR'):
    """Render `amount` per spec/16: IDR -> "Rp 1.500.000" (no decimals,
    Indonesian grouping); any other currency -> "USD 1,500.00"."""
    try:
        value = Decimal(str(amount))
    except InvalidOperation:
        return ''
    if currency == 'IDR':
        rounded = value.quantize(Decimal('1'), rounding=ROUND_HALF_UP)
        return 'Rp ' + f'{rounded:,}'.replace(',', '.')
    return f'{currency} {value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP):,}'
