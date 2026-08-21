from django.core.exceptions import ValidationError

def validate_ad_labels(value):
    if not isinstance(value, list):
        raise ValidationError('Labels must be a list.')
    if len(value) > 3:
        raise ValidationError('A maximum of 3 labels are allowed.')
    for label in value:
        if not isinstance(label, str):
            raise ValidationError('All labels must be strings.')
        if len(label) > 15:
            raise ValidationError('Each label must be 15 characters or less.')
