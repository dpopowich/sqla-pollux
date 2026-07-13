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

    _ALLOWED_AUTH = {"ANONYMOUS", "ADMIN"}

    def __init__(self, _auth_name):

        # enforce subclassing
        if type(self) is BaseUser:
            raise TypeError("Cannot instantiate BaseUser. Use a subclass")
        # normalize to uppercase
        auth = _auth_name.upper()

        if auth not in self._ALLOWED_AUTH:
            raise ValueError("Unknown auth name")

        self._auth = auth

    def __str__(self):
        return self._auth

    def __init_subclass__(cls, additional=()):
        # normalize names to all uppercase and check against existing
        additional = {a.upper() for a in additional}
        if same := (additional & cls._ALLOWED_AUTH):
            raise RuntimeError(f"Programming error: cannot reuse {same} as permission names")

        # set allowed auth on the new class
        cls._ALLOWED_AUTH = cls._ALLOWED_AUTH | additional

    def __getattr__(self, name):
        """Check for is_* attributes, e.g., is_admin, is_anonymous"""
        # note: must be all lowercase to avoid, e.g., is_AdMiN
        if name.startswith("is_") and name == name.lower():
            # normalize to uppercase
            auth_name = name[3:].upper()
            # only if this is a known auth
            if auth_name in self._ALLOWED_AUTH:
                # compute and stash on instance
                result = self._auth == auth_name
                setattr(self, name, result)
                return result

        raise AttributeError(f"No such attribute '{name}'")


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
            return cls.gen_anonymous_user()

    # shorthand
    cau = current_authenticated_user

    @classmethod
    def gen_anonymous_user(cls):
        """Create a BaseUser instance that is anonymous"""
        return cls("ANONYMOUS")

    @classmethod
    def gen_admin_user(cls):
        """Create a BaseUser instance that is an admin"""
        return cls("ADMIN")
