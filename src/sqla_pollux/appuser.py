"""User related models, representing users of the system and their
permission groups.
"""

# stdlib imports
import contextvars
import enum
import logging
import re

# venv imports
from sqlalchemy import (
    and_ as AND,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    String,
    Table,
    text,
)
from sqlalchemy.dialects.postgresql import (
    BYTEA,
    ENUM,
    UUID,
)
from sqlalchemy.orm import (
    synonym,
    validates,
)

# app imports
from . import (
   BaseModel,
   BaseUser,
   exceptions,
)
from .utils import (
    apispec,
    password_hash,
    password_hash_check,
    utcnow,
    validators,
)

logger = logging.getLogger(__name__)

# The Currently Authenticated User - by default, anon
_CAU = contextvars.ContextVar(f"{__name__}.cau")


# Pattern placed in a disabled user's email, from "name@domain" to
# "NAME+__HASH_disabled__@DOMAIN" where HASH is eight random lower-case
# hex chars.  There are three groups, (1) the original name (left-hand
# side of "@"), (2) the munged hash, (3) the "@" and following domain.
DISABLED_NAME_RE = re.compile(r"(.+)(\+__[0-9a-f]{8}_disabled__)(@.+)")


class AppGroup(enum.Enum):
    """Known application user group names"""

    ADMIN = "ADMIN"
    STAFF = "STAFF"
    USER = "USER"


_User = Table(
    "appuser",
    BaseModel.metadata,
    Column(
        "id",
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        info=apispec.READ_ONLY(),
    ),
    Column(
        "group",
        ENUM(AppGroup, name="appgroup"),
        nullable=False,
        info=apispec.NO_UPDATE(),
    ),
    Column("email", String(255), nullable=False),  # unique constraint placed below
    Column("password", BYTEA, nullable=False, info=apispec.NO_READ()),
    Column("first_name", String(255), nullable=False),
    Column("last_name", String(255), nullable=False),
    Column("title", String, doc="Employee job title"),
    Column("last_accessed", DateTime(timezone=True), info=apispec.READ_ONLY()),
    Column(
        "enabled",
        Boolean,
        default=True,
        server_default=text("true"),
        nullable=False,
        info=apispec.READ_ONLY(),
    ),
    Column(
        "created",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        info=apispec.READ_ONLY(),
    ),
    CheckConstraint(text("created < last_accessed"), name="created_before_accessed"),
    CheckConstraint(text("email = trim(email)"), name="trimmed_email"),
    Index("appuser_uniq_caseinsenstive_email", text("lower(email)"), unique=True),
)


class AppUser(BaseUser, BaseModel):
    """A User of the system enabled or disabled"""

    __table__ = _User

    ALL_GROUPS = set(AppGroup)

    # make User.username a synonym for User.email
    username = synonym("email")

    # other classes can install hooks to run whenever a user logs in,
    # e.g., clear a flag in their profile when a user next logs in
    _login_hooks = set()

    ######################################################################
    # dunders
    ######################################################################
    def __str__(self):  # pragma: no cover
        return self.email

    ######################################################################
    # properties
    ######################################################################
    @property
    def can_login(self):
        """Can this user login?"""
        # The only criteria is if the account is enabled. Subclasses
        # could add other criteria, e.g., if last_accessed is older than
        # X days.
        return self.enabled

    @property
    def claims(self):
        """During login claims can be made about the authenticated user.

        These claims are ephemeral for a session and are not stored in the database.

        NOTE: these claims also come from a client and should not be
        used for any server-side credentialing.

        See User.login().

        Returns - dict
        """
        try:
            claims = self._claims
        except AttributeError:
            claims = self._claims = {}

        return claims

    @property
    def full_name(self):
        """User's full name"""
        return (f"{self.first_name} {self.last_name}").strip()

    @property
    def is_admin(self):
        """Is this user an admin?"""
        return self.group is AppGroup.ADMIN

    @property
    def is_admin_or_staff(self):
        """Is this user an admin?"""
        return self.is_admin or self.is_staff

    @property
    def is_anonymous(self):
        """Is this user an anonymous user?"""
        # yes, if no groups
        return self.group is None

    @property
    def is_staff(self):
        """Is this user staff?"""
        return self.group is AppGroup.STAFF

    @property
    def is_user(self):
        """Is this user a customer?"""
        return self.group is AppGroup.USER

    ######################################################################
    # validators
    ######################################################################
    @validates("email")
    def _validate_email(self, _key, email):
        """Validate email is an actual valid email"""
        return validators.email(email)

    @validates("password")
    def _validate_password(self, _key, password):
        """Validate password is valid then hash"""
        if isinstance(password, bytes):
            password = password.decode("utf-8")
        pwd = validators.password(password)
        return password_hash(pwd)

    @validates("first_name", "last_name")
    def _validate_name(self, _key, name):
        """Validate names"""
        return validators.text(name, empty=True)

    ######################################################################
    # staticmethods
    ######################################################################

    ######################################################################
    # classmethods
    ######################################################################
    @classmethod
    async def any_admin(cls):
        """Return any enabled admin of the system"""
        admin = await cls.list(
            where=AND(cls.group == AppGroup.ADMIN, cls.enabled), limit=1
        )

        if not admin:
            raise exceptions.ModelNotFound("No admin found in the system")

        return admin[0]

    @classmethod
    async def _create(
        cls,
        *,
        email,
        first_name,
        last_name,
        password,
        group=None,
        **attrs,
    ):
        """Create a new user.

        Only Admins and Staff can create users.

        Args:

           email (str): User's email address. Must be a valid email
                        address according to utils.is_email_valid()
           first_name (str): User's first name.
           last_name (str): User's last name.
           password (str): User's password. Must be valid according to
                           utils.is_password_valid()
           group (AppGroup): User's group. If None, defaults to the
                             default group created by a user:
                                Admin - Staff
                                Staff - User
                                User - N/A.
           **attrs: Optional attributes to set on the instance.

        Returns - User instance

        """

        # the CAU
        cau = cls.cau()

        if cau is BaseUser.BOOTSTRAP:
           can_create = (AppGroup.STAFF, AppGroup.ADMIN, AppGroup.USER)
        else:
           can_create = cau.can_create_user()
           if not can_create:
              raise exceptions.PermissionDenied("Not authorized to create other users")

        # normalize group to enum
        try:
            group = AppGroup(group) if group else can_create[0]
        except ValueError:
            raise exceptions.NotAKnownGroup(f"Unknown group: {group}") from None

        # ensure not creating someone in an unauthorized group
        if group not in set(can_create):
            raise exceptions.BadGroupConfig(f"Cannot create user in group {group}")

        user = await super()._create(
            email=email,
            first_name=first_name,
            last_name=last_name,
            password=password,
            group=group,
            _force={"password", "group"},
            **attrs,
        )

        return user

    @classmethod
    async def get_by_username(cls, username, /):
        """Fetch User by username"""
        return await cls.one_or_none(where=cls.email == username)

    @classmethod
    async def get_by_username_or_id(cls, name_id, /):
        """Fetch User by username or ID.

        First try username, then ID.
        """
        user = await cls.one_or_none(where=cls.email == name_id)
        if user is not None:
            return user

        return await cls.get(name_id)

    @classmethod
    async def login(cls, *, id=None, username=None, password=None, claims=None):
        """Login user.

        Get instance by id or username, whichever is not None, preferring id.

        If password is given, it must hash to the existing user's hashed password.

        If claims is not None it is expected to be a dict, a mapping of
        claims about this authenticated user.  These claims are
        considered ephemeral and exist only for a session (they are not
        saved to the database) and a copy are stored on this user
        made avaliable with the `claims` property.  If claims is None, an
        empty dict will be used.

        Raises - CannotLogin - if `user.can_login` is falsey or
                 password, if given, does not match.

        Returns - User instance if user is found (by id or username)
                  and `user.can_login` returns True and password
                  matches (if given), else None.

        """
        user = None

        # find user by id or username
        if id:
            user = await cls.get(id)
        elif username:
            user = await cls.get_by_username(username)

        # No user?
        if user is None:
            return None

        # Maybe they're disabled?
        if not user.can_login:
            raise exceptions.CannotLogin

        if password and not password_hash_check(password, user.password):
            raise exceptions.CannotLogin

        # set claims to copy of incoming
        if claims:
            # pylint: disable=protected-access
            user._claims = {**claims}

        # mark them having logged in
        user.last_accessed = utcnow()

        # run any login-hooks
        for hook in cls._login_hooks:
            hook(user)

        return user

    @classmethod
    def register_login_hook(cls, hook):
        """Register a login-hook function to be called whenever a user successfully logs in.

        The callable, hook, will be called with one argument, the newly
        logged in user with updated `last_accessed` attribute:

           hook(user)

        As of this writing:
           * The return value is ignored.
           * Any exception raised is propagated.
           * Hooks are called in random order.
           * Hooks are called synchronously

        Returns - None
        """
        cls._login_hooks.add(hook)

    ######################################################################
    # methods
    ######################################################################
    async def disable(self):
        """Disable the user.

        Notes:

          * Disabled users cannot login and will be "invisible",
            depending on the API call and who is making it (admin vs
            others).
          * Only Admins can disable other admins and staff users.
          * Staff can only disable users they have permission for.
          * Regular users have no permissions here.
          * This is a no-op for already disabled users.
          * Disabled users can be reenabled with enable().

        Returns - self
        """
        if not self.enabled:
            return self

        cau = self.cau()

        # only admin and staff
        if not cau.is_admin_or_staff:
            raise exceptions.PermissionDenied()

        # staff can only disable client users they have access to
        if cau.is_staff and not (self.is_user and await self.has_permission(user=cau)):
            raise exceptions.PermissionDenied()

        if cau.id == self.id:
            raise exceptions.CannotUpdateModel("Cannot disable self")

        await self.update(enabled=False, _force={"enabled"})

        return self

    async def enable(self):
        """Enable a disabled user.

        Notes:

          * Only Admins can enable a disabled user (but not their own account).
          * This is a no-op for already enabled users.

        Returns - self
        """
        if self.enabled:
            return self

        cau = self.cau()
        if not cau.is_admin:
            raise exceptions.PermissionDenied()
        if cau.id == self.id:
            raise exceptions.CannotUpdateModel("Cannot enable self")

        return await self.update(enabled=True, _force={"enabled"})

    async def has_permission(self, *, user=None):
        """Does other user have permission to access this user?"""

        # for clarity between the requesting user and the target of the permission
        # check (self)
        requester = user
        target = self

        if requester is None:
            requester = target.cau()

        # if the requesting user is disabled, False
        if not requester.enabled:
            return False

        # users always have access to self; note the previous rule: a disabled
        # user does not have access to self.
        if target.id == requester.id:
            return True

        if requester.is_admin:
            # admins have acces to everyone
            return True

        # No one (accept admins) has access to an admin or a disabled user.
        if target.is_admin or not target.enabled:
            return False

        # staff have access to all other staff and regular users
        if requester.is_staff:
            # note admin targets covered above
            return True

        # regular users only have access to their own account (covered above)
        return False

    async def _update(self, _create=False, **attrs):
        """Update user while syncing w/ keycloak"""
        cau = self.cau()
        # The only time a user can update another user's attributes:
        #  1. During creation
        #  2. An admin
        #  3. A staff with access
        if cau is not BaseUser.BOOTSTRAP and cau.id != self.id and not (
            _create or cau.is_admin or (cau.is_staff and not self.is_admin)
        ):
            # in case someone is phishing, we don't expose any
            # information
            raise exceptions.ModelNotFound("Not Found")

        # only admins
        if not cau.is_admin:
            attrs.pop("enabled", None)

        # only set password if given and non-empty
        if not attrs.get("password"):
            attrs.pop("password", None)

        # do the update
        return await super()._update(_create=_create, **attrs)

    def can_create_user(self):
        """Can this user create another user?

        Return tuple of groups this user may assign to a new user when
        creating the instance.  Default group for the new user is the
        first in the sequence.

        If this user cannot create another user return an empty tuple.
        """
        if self.is_admin:
            return (AppGroup.STAFF, AppGroup.ADMIN, AppGroup.USER)
        elif self.is_staff:
            return AppGroup.USER
        else:
            return ()
