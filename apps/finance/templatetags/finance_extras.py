from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter
def money(amount, currency='IDR'):
    """Render a server-provided amount per the money display contract
    (spec/16 CUR-026): IDR as 'Rp 1.500.000' (no decimals), anything else
    with 2 decimals and id-ID grouping ('USD 1.234,50'). Display only —
    clients never sum or compute with these values."""
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, ValueError):
        return ''
    if currency == 'IDR':
        text = f"{value:,.0f}".replace(',', '.')
        return f"Rp {text}"
    text = f"{value:,.2f}".replace(',', '\0').replace('.', ',').replace('\0', '.')
    return f"{currency} {text}"
