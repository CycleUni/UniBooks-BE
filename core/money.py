from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from django.core.exceptions import ValidationError
from django.core.cache import cache

def _get_decimal_places(currency_code: str) -> int:
    """
    Get decimal places for a currency, cached.
    Raises Currency.DoesNotExist if the currency is not found.
    """
    cache_key = f"currency_dp_{currency_code}"
    dp = cache.get(cache_key)
    if dp is not None:
        return dp

    from core.models import Currency
    dp = Currency.objects.values_list('decimal_places', flat=True).get(code=currency_code)
    cache.set(cache_key, dp, 86400)
    return dp

def to_minor(major, currency: str) -> int:
    """
    Convert a major unit amount to minor units (e.g. 10.50 HKD -> 1050).
    Rounds half up to prevent truncation of fractional cents if input precision
    exceeds the currency's decimal places. Physical transactions cannot handle
    fractional cents, so rounding to the nearest minor unit is the standard approach.
    """
    from core.models import Currency
    try:
        dp = _get_decimal_places(currency)
    except Currency.DoesNotExist:
        raise ValidationError(f"Invalid currency: {currency}")

    try:
        major_dec = Decimal(str(major))
    except InvalidOperation:
        raise ValidationError("Amount must be a valid number.")

    # Quantize using ROUND_HALF_UP and the currency's decimal places
    multiplier = Decimal('10') ** dp
    minor_dec = (major_dec * multiplier).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    return int(minor_dec)

def from_minor(minor, currency: str) -> Decimal:
    """
    Convert a minor unit amount to major units (e.g. 1050 HKD -> Decimal('10.50')).
    """
    from core.models import Currency
    try:
        dp = _get_decimal_places(currency)
    except Currency.DoesNotExist:
        raise ValidationError(f"Invalid currency: {currency}")

    try:
        minor_dec = Decimal(str(minor))
    except InvalidOperation:
        raise ValidationError("Amount must be a valid number.")

    return minor_dec / (Decimal('10') ** dp)

def validate_minor(minor, currency: str):
    """
    Validate that the amount is a valid non-negative minor unit (integer).
    We ensure `currency` exists, as a valid amount depends on a known currency.
    Also guards against bool (which passes isinstance(..., int)).
    """
    if type(minor) is not int or minor < 0:
        raise ValidationError("Amount must be a non-negative integer (minor units).")
        
    from core.models import Currency
    try:
        _get_decimal_places(currency)
    except Currency.DoesNotExist:
        raise ValidationError(f"Invalid currency: {currency}")
