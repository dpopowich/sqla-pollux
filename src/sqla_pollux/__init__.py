"""Model package - all ORMs and related utilities"""

# stdlib imports
import sys

# venv imports

# public app imports
from .exceptions import (
    BadGroupConfig,
    CannotCreateModel,
    CannotLogin,
    CannotUpdateModel,
    DuplicateModel,
    ModelError,
    ModelNotFound,
    NotAKnownGroup,
    PermissionDenied,
    ValidationError,
)

from .utils import (
    apispec,
    create_view,
    dumps,
    loads,
)
from .utils.sqla import (
    create_all,
    new_dbsession,
    new_isolated_trx,
    drop_all,
    run_sync,
    sqla_init,
)

from .basemodel import (
    BaseModel,
    BaseModelMixin,
    populate_module_with_apispec,
)

from .baseuser import (
    BaseUser,
)

__all__ = [
    "ANONYMOUS",
    "BadGroupConfig",
    "BOOTSTRAP",
    "CannotCreateModel",
    "CannotLogin",
    "CannotUpdateModel",
    "Client",
    "dumps",
    "DuplicateModel",
    "loads",
    "ModelError",
    "ModelNotFound",
    "NotAKnownGroup",
    "PermissionDenied",
    "populate_module_with_apispec",
    "ValidationError",
    "create_all",
    "new_dbsession",
    "new_isolated_trx",
    "drop_all",
    "run_sync",
    "sqla_init",
    "BaseModel",
    "BaseUser",
]
