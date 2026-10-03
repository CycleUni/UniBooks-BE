from django.core.exceptions import ValidationError
from django.core.validators import URLValidator

# Book.cover_url's max_length. Saves that pass update_fields skip the
# URLField validators, so writers check it here instead.
COVER_URL_MAX = 1024

# http as well: Google Books still hands out http:// thumbnails, which the
# cover proxy upgrades.
_cover_url = URLValidator(schemes=['http', 'https'])


def clean_cover_url(value):
    """The cover URL to store: stripped, '' when blank, None when unusable.

    Unusable means not a string, longer than the column, or not an http(s)
    URL Django's URLValidator accepts. Callers decide what None means: the
    admin refuses the edit, a seller's manual create just leaves it out.
    """
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return ''
    if len(value) > COVER_URL_MAX:
        return None
    try:
        _cover_url(value)
    except ValidationError:
        return None
    return value
