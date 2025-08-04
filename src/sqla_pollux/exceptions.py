"""Exceptions for models package."""


class ModelError(Exception):
    """Base class for all cwf model exceptions"""

    def __json__(self):
        """Serializing returns first arg passed to constructor or class docstring"""
        return self.args[0] if self.args else self.__class__.__doc__


######################################################################
# ModelError subclasses
######################################################################
class NotAKnownGroup(ModelError):
    """A group was referenced not known by the system"""


class CannotCreateModel(ModelError):
    """Generic exception for when a model cannot be created."""


class CannotLogin(ModelError):
    """User cannot login, e.g., disabled account"""


class CannotUpdateModel(ModelError):
    """Generic exception for when a model cannot be updated."""


class FlushError(ModelError):
    """Flushing to database is caught in an infinite loop"""


class ModelNotFound(ModelError):
    """An expected object was not found"""


class ValidationError(ModelError):
    """A validation error on a model attribute"""


class WrongStateError(ModelError):
    """A model is in a state disallowing the operation"""


class StaleValueError(ModelError):
    """A stale value was referenced"""

    def __init__(self, current):
        super().__init__(dict(stale=current))


class NeedConfirmation(WrongStateError):
    """A model is in a state disallowing the operation, but can be forced
    with confirmation"""


class CannotDeleteModel(ModelError):
    """Generic exception for when a model cannot be deleted"""


######################################################################
# CannotCreateModel subclasses
######################################################################
class DuplicateModel(CannotCreateModel):
    """An object could not be created because of a duplicate value"""


class PermissionDenied(CannotCreateModel):
    """Current authenticated user does not have authorization"""


class BadGroupConfig(CannotCreateModel):
    """Bad configuration of user groups"""


class ModelLocked(CannotCreateModel):
    """The object is in a state that no longer allows the operation"""


class BadData(CannotCreateModel):
    """Cannot create/generate instance due to bad data input"""
