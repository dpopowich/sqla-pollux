"""Validators for model attributes"""

# stdlib imports
from datetime import datetime, date

# app imports
from . import is_email_valid, is_password_valid
from .. import exceptions

# NB: each function should have the signature:
#
#   def fn(value, *, [parm=DEFAULT, ...], **kwargs)
#
# and should return value, normalized, or raise ValidationError
#
# By having **kwargs common params can be passed to any validator
# (e.g., minimum=VAL).


def is_bool(value, **_kwargs):
    """Validate value is a bool (NOT: truthiness, but True or False)"""
    if not isinstance(value, bool):
        raise exceptions.ValidationError("not a boolean")
    return value


def date_format(fmt="%Y-%m-%d"):
    """Factory function to validate a date or its string representation in
    a particular format, optionally within a range
    """

    def func(value, minimum=None, maximum=None, **_kwargs):
        if not isinstance(value, date):
            value = text(value)
            value = datetime.strptime(value, fmt).date()

        return minmax(value, minimum=minimum, maximum=maximum)

    return func


def email(value, **_kwargs):
    """Validate value is a valid email address."""
    value = text(value)
    if not is_email_valid(value):
        raise exceptions.ValidationError("not a valid email address")

    return value


def int_range(value, minimum=None, maximum=None, **_kwargs):
    """Factory function to validate integer, optionally within a range"""
    if not isinstance(value, int):
        # attempt to coerce
        try:
            value = int(value)
        except Exception:
            raise exceptions.ValidationError("not an integer") from None

    return minmax(value, minimum=minimum, maximum=maximum)


def minmax(value, minimum=None, maximum=None, **_kwargs):
    """Validate value is between min/max values, inclusive"""
    if minimum is not None and value < minimum:
        raise exceptions.ValidationError("out of range")
    if maximum is not None and value > maximum:
        raise exceptions.ValidationError("out of range")
    return value


def number_range(value, minimum=None, maximum=None, **_kwargs):
    """Factory function to validate value is a number, optionally within a range"""
    if not isinstance(value, (int, float)):
        # attempt to coerce
        for typ in int, float:
            try:
                value = typ(value)
                break
            except Exception:
                pass
        else:
            raise exceptions.ValidationError("not a number")

    return minmax(value, minimum=minimum, maximum=maximum)


def password(value, **_kwargs):
    """Validate value is a valid password."""
    value = text(value)
    if not is_password_valid(value):
        raise exceptions.ValidationError("not a valid password")

    return value


def text(
    value,
    empty=False,
    downcase=False,
    upcase=False,
    exclude=None,
    truncate=None,
    regex=None,
    **_kwargs,
):
    """Normalize text -

    The following happens in order:

         * strip extra space from ends
         * empty strings only allowed if empty=True (after stripping)
         * upcase or downcase as indicated
         * exclude - a str, no characters of which may appear in value
         * truncate - if not None, truncate string to specified max length.
         * regex - a compiled regex, if not None the string must
                   `match` (after applying previous modifications).

    Returns - normalized str
    """
    if not isinstance(value, str):
        raise exceptions.ValidationError("not a str")

    value = value.strip()
    if not empty and not value:
        raise exceptions.ValidationError("empty string")
    if downcase:
        value = value.lower()
    if upcase:
        value = value.upper()
    if exclude and set(exclude) & set(value):
        raise exceptions.ValidationError(
            f'{value} should not contain any of: "{exclude}"'
        )
    if truncate:
        value = value[:truncate]
    if regex and not regex.match(value):
        raise exceptions.ValidationError(f"Does not match regex: {regex.pattern}")
    return value
