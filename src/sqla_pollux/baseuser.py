"""BaseUser model - a user of the system (e.g., an entity that authenticates with the application).

The model defined here needs to be subclassed to define a database-backed user instance.  The
BaseUser can be used in a context to set the Current Authenticated User (CAU), e.g., after
authentication (in webapp middleware, for instance) we can call a request such that throughout the
request we know inside library code the authenticated user:

   # let's say our AppUser bases BaseUser:

   from geminus import BaseUser, BaseModel
   class AppUser(BaseUser, BaseModel):
      ...

   # then in middleware code:

   from myapp.models import AppUser

   def auth_middleware(request, handler):
       # default to anon user
       user = AppUser.ANONYMOUS

       # read headers for session cookie...
       if auth_header:
          ...
          user = await AppUser.login(...)

       # run request handler in the context of the user, who will be the CAU throughout the request.
       with user:
          response = await handler(request)

A classmethod, `current_authenticated_user()`, will return the current user, by default, user
ANONYMOUS (see below).

There are two properties of BaseUser which should be implemented in subclasses:

   is_admin - return True if the CAU is an admin. BaseUser returns False.
   is_anonymous - return True if the CAU is unknown to the system. BaseUser returns True.

How these are implemented (e.g., by assigning users to an app-defined group and checking the user's
group inside these properties) is up to an application's implementation and needs.

Two in-memory users (instances of BaseUser) are defined on the class:

   BaseUser.ANONYMOUS - an instance where `is_anonumous` is True.
   BaseUser.BOOTSTRAP - an instance where `is_admin` is True.

"""

# stdlib imports
import contextvars

# The Currently Authenticated User - by default, anon
_CAU = contextvars.ContextVar(f"{__name__}.cau")

__all__ = ["BaseUser"]


class BaseUser:
    """A User of the system"""

    def __enter__(self):
        """Set this user as the CAU in execution of the context"""
        # push this user on to the stack; last user in is the CAU
        try:
            stack = _CAU.get()
        except LookupError:
            stack = []
            _CAU.set(stack)

        stack.append(self)

        return self

    def __exit__(self, *exc):
        """Reset the cau to whomever it was before the context"""
        stack = _CAU.get()
        stack.pop()

    @property
    def is_admin(self):
        """Is this user an admin?"""
        # by default, no
        return False

    @property
    def is_anonymous(self):
        """Is this user an anonymous user?"""
        # by default, yes
        return True

    @classmethod
    def current_authenticated_user(cls):
        """Return the Current Authenticated User.

        NOTE: there is always a CAU, the Anonymous User being the
        default if no actual user has authenticated.

        Returns - User
        """
        try:
            stack = _CAU.get()
            return stack[-1]
        except (IndexError, LookupError):
            return cls.ANONYMOUS


class BOOTSTRAP(BaseUser):
    """Our singleton bootstrap (admin) user"""

    @property
    def is_admin(self):
        """Is this user an admin?"""
        return True

    @property
    def is_anonymous(self):
        """Is this user an anonymous user?"""
        return False


BaseUser.ANONYMOUS = BaseUser()
BaseUser.BOOTSTRAP = BOOTSTRAP()

del BOOTSTRAP
