"""Utilities for model package"""

# stdlib imports
from collections import namedtuple
from datetime import datetime, date, timezone
import enum
import functools
from hashlib import pbkdf2_hmac
import json
import os
import re
import uuid

# venv imports
from pydantic import BaseModel as PydBaseModel
from sqlalchemy import (
    text,
)

# app imports
from .apispec import (
    apispec,
    ApiSpec,
    generate_model_apispec,
)

# For password hash generation. See
# https://docs.python.org/3.12/library/hashlib.html#hashlib.pbkdf2_hmac. Care
# must be taken when changing these values: see password_hash().
_HashingConfig = namedtuple("_HashingConfig", "name iterations salt_len")
_HASHING = {
    b"v1": _HashingConfig(name="sha256", iterations=500_000, salt_len=16),
}
_HASHING_VERSION = b"v1"


__all__ = [
    "ApiSpec",
    "apispec",
    "CachedData",
    "canonical_filename",
    "dumps",
    "generate_model_apispec",
    "is_email_valid",
    "is_password_valid",
    "loads",
    "password_hash",
    "password_hash_check",
    "range_clause",
    "utcnow",
    "VALID_EMAIL_RE",
    "VALID_PASSWORD_RE",
]


class CachedData:
    """Object to store cache by attribute or item lookup.

    Useful to store state for the lifetime of an instance.

       cache = CachedData()
       cache.x = 42
       cache['y'] = 43
       assert cache.asdict == {"x": 42, "y": 43}
       assert cache.y == 43
       cache.clear()
       assert cache.asdict == {}

       cache['z'] = 42
       assert cache.z == 42
       assert cache.asdict == {"z": 42}
       del cache.z
       assert cache.asdict == {}
    """

    def __getitem__(self, key):
        return self.__dict__[key]

    def __setitem__(self, key, val):
        self.__dict__[key] = val

    @property
    def asdict(self):
        """Return cache as dict"""
        return self.__dict__

    def clear(self):
        """Clear the cache"""
        self.__dict__.clear()


def canonical_filename(fname):
    """Convert fname to a univerally acceptable filename.

    Args -
       fname - any object to be forced to a string with `str()` - the
               file name

    1. Strip whitespace.
    2. Replace "/" with "-"
    3. Replace any contiguous set of characters that are not
       alphanumeric, dash, underscore, or period with a single
       underscore.
    4. rstrip any periods

    Example:
       "  Project (日本語) to prove 1 ≥ 2 and/or 2 < 1  "

    Becomes:
       "Project_日本語_to_prove_1_2_and_or_2_1"

    Returns - str

    """
    fname = fname.strip().replace("/", "-")
    return ("_".join(p for p in re.split(r"[^-.\w]", fname) if p)).rstrip(".")


def range_clause(col, start=None, end=None):
    """Return a SQLAlchemy Clause for querying a range.

    Args -

       col - sqla Column
       start - start value
       end - end value

    NB: Either start or end can be None in which the range will be
    open-ended on that side of the range.

    NB: it is the caller's responsibility to pass in values compatible
    with the column's type.

    NB: If both are None the returned clause will be `true`.

    NB: The ranges are inclusive.

    NB: if start > end, the values are swapped.

    Returns - Clause
    """
    if start is None and end is None:
        # None, None
        return text("true")
    elif start is not None and end is None:
        # start, None
        return col >= start
    elif start is None and end is not None:
        # None, end
        return col <= end
    else:
        # start, end
        if start > end:
            start, end = end, start
        return col.between(start, end)


######################################################################
# Date/Time related utilities
######################################################################
def utcnow():
    """Return current datetime, UTC, as an "aware" datetime"""
    return datetime.now(timezone.utc)


######################################################################
# security related utilities
######################################################################
# valid email address: http://www.regular-expressions.info/email.html
VALID_EMAIL_RE = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"

# Valid Password Regex: 8-255 chars, with at least one lower, one
# upper, one digit and one special character as defined here:
_SPECIAL = r"[!@#$%^&*?<>.~_=`\-+]"  # the special chars allowed
_VALID = r"(\w|{_S}){{8,255}}".format(
    _S=_SPECIAL
)  # a word char OR a special char, 8-255 of them
# a series of lookahead assertions to make sure we have one of each, followed by _VALID
VALID_PASSWORD_RE = r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*{_S}){_V}$".format(
    _S=_SPECIAL, _V=_VALID
)
del _SPECIAL, _VALID

# compiled reges
_valid_email_re = re.compile(VALID_EMAIL_RE)
_valid_password_re = re.compile(VALID_PASSWORD_RE)


def is_email_valid(email):
    """Is email a valid email address?

    See VALID_EMAIL_RE for definition.

    Returns: bool
    """
    return bool(_valid_email_re.match(email))


def is_password_valid(password):
    """Is user password valid?

    See VALID_PASSWORD_RE for definition.

    Returns: bool
    """
    return bool(_valid_password_re.match(password))


def password_hash(password):
    """Hash a password for storage.

    Args:

       password (str|bytes) - the password. If a str, it is converted
                              to bytes with utf-8 encoding.

    Returns - (bytes) - the hash of the password. See
                        check_password_hash() for authenticating a
                        password against an existing hash.

    NB: the current form of the hash is:

        VERSION + b'$' + SALT + HASH
    """
    if isinstance(password, str):
        password = password.encode("utf-8")
    # we use the latest hashing version
    hashing = _HASHING[_HASHING_VERSION]

    salt = os.urandom(hashing.salt_len)
    hashed = pbkdf2_hmac(hashing.name, password, salt, hashing.iterations)

    return _HASHING_VERSION + b"$" + salt + hashed


def password_hash_check(password, hashed):
    """Check password against previous hash.

    Args:

       password (str|bytes) - the password. If a str, it is converted
                              to bytes with utf-8 encoding.
       hashed (bytes) - the output of a previous call to password_hash().

    This is suitable for a user login.  E.g.:

       # user sets their password, `passwd`
       user.password = password_hash(passwd)

       # When user wants to login, we fetch the user's record (by
       # username), then check the given password against the previous
       # hash:
       username, passwd = get_user_login_info()
       user = User.get_by_username(username)
       if not password_hash_check(passwd, user.password):
          raise PermissionDenied

    Returns - (bool) - True if the password hashes to the given hash

    """
    if isinstance(password, str):
        password = password.encode("utf-8")

    # find index of our "$" marker
    idx = hashed.find(b"$")
    if idx == -1:
        # no marker? Can't possibly match
        return False

    # get the version and find it in _HASHING
    version = hashed[:idx]
    hashing = _HASHING.get(version)
    if not hashing:
        # can't find version???
        return False

    # skip over version and marker
    hashed = hashed[(idx + 1) :]

    # the original salt and output of pbkdf2_hmac()
    salt_len = hashing.salt_len
    salt, hashed = hashed[:salt_len], hashed[salt_len:]

    return hashed == pbkdf2_hmac(hashing.name, password, salt, hashing.iterations)


######################################################################
# JSON related utilities
######################################################################
def _json_default(obj):
    """Custom JSON encoder for our types.

    Args:

       obj (Any): any object needing to be JSON-ified.

    Returns: (Any) - an object known to be json-ified.

    Raises:
       TypeError - if the object is of a type that cannot be encoded to JSON.
    """
    if hasattr(obj, "__json__"):
        return obj.__json__()
    elif isinstance(obj, Exception):
        return str(obj)
    elif isinstance(obj, PydBaseModel):
        # we don't use pydantic's `.model_dump()` because it converts any sub
        # pydantic BaseModel instances with `.model_dump()`, but we want to
        # use __json__() if we defined one on such a class.
        model = obj.__class__
        return {key: getattr(obj, key) for key in model.model_fields.keys()}
    elif isinstance(obj, datetime):
        return obj.isoformat()
    elif isinstance(obj, date):
        return obj.isoformat()
    elif isinstance(obj, enum.Enum):
        return obj.name
    elif isinstance(obj, uuid.UUID):
        return str(obj)
    elif isinstance(obj, set):
        return list(obj)
    raise TypeError(f"Cannot JSON encode {type(obj)}")


dumps = functools.partial(json.dumps, default=_json_default)
loads = json.loads
