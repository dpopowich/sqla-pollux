"""Utility to generate OSI API specifications directly from SQLAlchemy ORM classes."""

# stdlib imports
from collections import defaultdict
import functools
from typing import Any, Optional

# venv imports
from pydantic import (
    BaseModel,
    ConfigDict,
    create_model,
    Field,
)
from sqlalchemy import inspect

# sentinel
MISSING = "__missing__"
# Default `model_config` for generated pydantic models
MODEL_CONFIG = ConfigDict(from_attributes=True)


class ApiSpec(BaseModel):
    """Utility to specify what CRUD operations are allowed on a column

    See: apispec()
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        MISSING, description="Name of column, default: taken from col.key"
    )
    type: Any = Field(
        MISSING, description="Column's type, default: taken from col's pytype"
    )
    default: Any = Field(
        MISSING, description="Column's default value, default: taken from col"
    )
    create: bool = Field(True, description="If True, allow column in Model.create()")
    read: bool = Field(True, description="If True, add column in JSON serialization")
    update: bool = Field(True, description="If True, allow column in Model.update()")
    fld_kwargs: dict = Field({}, description="Extra kwargs to pass to pydantic.Field()")


def apispec(
    *,
    name=MISSING,
    type=MISSING,
    default=MISSING,
    create=True,
    read=True,
    update=True,
    **fld_kwargs,
):
    """Factory to generate an ApiSpec instance suitable for use with
    sqlachemy Column.info.

    E.g.:

       Column('password', String(), nullable=False,
              info=apispec(read=False))

    Returns - dict with single key, `apispec`, with value an ApiSpec instance.

    """
    return dict(
        apispec=ApiSpec(
            name=name,
            type=type,
            default=default,
            create=create,
            read=read,
            update=update,
            fld_kwargs=fld_kwargs,
        )
    )


## default specs for typical use-cases
# one of create, update, read is False
apispec.NO_CREATE = functools.partial(apispec, create=False)
apispec.NO_UPDATE = functools.partial(apispec, update=False)
apispec.NO_READ = functools.partial(apispec, read=False)
# two of create, update, read are False
apispec.CREATE_ONLY = functools.partial(apispec, update=False, read=False)
apispec.UPDATE_ONLY = functools.partial(apispec, create=False, read=False)
apispec.READ_ONLY = functools.partial(apispec, create=False, update=False)
# internally maintained and not visible beyond model impl
apispec.INTERNAL = functools.partial(apispec, create=False, update=False, read=False)


def _column_type(column):
    """Determine column type, default and nullability

    Raises - RuntimeError if python type cannot be determined

    Returns - tuple - python-type, default, nullable
    """
    # determine python type
    pytype = None
    try:
        try:
            pytype = column.type.python_type
        except AttributeError:
            pytype = column.type.impl.python_type
    except AttributeError:
        # pylint: disable=raise-missing-from
        raise RuntimeError(
            f"Programming Error: cannot determine python type of Column: {column}"
        )

    nullable = column.nullable
    # assume there's no default and it's nullable
    default = None
    # if the column has a scalar default, use it
    coldef = column.default
    if coldef and coldef.is_scalar:
        default = coldef.arg
    # no default and not nullable?  make it required
    if default is None and not nullable:
        default = ...
    # if nullable, make type optional
    if nullable:
        pytype = Optional[pytype]

    return pytype, default, nullable


def generate_model_apispec(
    db_model,
    *,
    model_config=MODEL_CONFIG,
    excludes=None,
    exclude_private=None,
    extras=None,
):
    """Create ApiSpec for each column of sqlachemy model.

    Args -
       db_model - model subclassing ..base.BaseModel
       model_config - pydantic `model_config`, minimally it must specify `from_attributes=True`
       excludes - names of columns to be excluded (matched on column
                  key); if None (the default), use
                  db_model.__apispec_excludes__, which, in turn,
                  defaults to an empty tuple.
       exclude_private - if True, in addition to names in `excludes` any
                         column name beginning with an underscore will be excluded.  If
                         None, use db_model.__apispec_exclude_private__, which defaults to
                         True.
       extras - A sequence of ApiSpec instances to be added to the model
                in addition to the generated instances from columns.  If None, use
                db_model.__apispec_extras__, which defaults to an empty tuple.  If
                a callable, it will be called without any arguments to return the
                sequence of ApiSpec.

    Raises - RuntimeError if a column's python type cannot be
             determined. (NB: we use RuntimeError because these are
             really a programming error to be fixed in configuring
             models.)

    Returns - custom class with three attributes:
                  .create - pydantic.BaseModel for creation
                  .read   - pydantic.BaseModel for read
                  .update - pydantic.BaseModel for update

    """
    mapper = inspect(db_model)
    model_name = db_model.__name__

    # CRU operations
    cru = defaultdict(dict)
    operations = "create", "read", "update"

    if excludes is None:
        excludes = getattr(db_model, "__apispec_excludes__", ())

    if exclude_private is None:
        exclude_private = getattr(db_model, "__apispec_exclude_private__", True)

    if extras is None:
        extras = getattr(db_model, "__apispec_extras__", ())
        if callable(extras):
            extras = extras()

    for colprop in mapper.column_attrs:
        name = colprop.key
        # skip excluded
        if name in excludes or (exclude_private and name.startswith("_")):
            continue

        # get underlying Column
        if not colprop.columns or len(colprop.columns) != 1:
            raise RuntimeError(
                "Programming Error: must exclude ColumnProperties"
                f" not having exactly one Column: {model_name}.{name}"
            )
        column = colprop.columns[0]

        # get the ApiSpec for this column; creating new one if
        # necessary; NB: we don't use setdefault because it's not lazy
        spec = column.info.get("apispec")
        if spec is None:
            spec = column.info["apispec"] = ApiSpec()

        # get column type, default value, and nullability
        pytype, default, nullable = _column_type(column)

        # fill missing parts of ApiSpec
        if spec.name is MISSING:
            spec.name = name
        if spec.type is MISSING:
            spec.type = pytype
        if spec.default is MISSING:
            spec.default = default

        # update field for each of CRU operations
        for oper in operations:
            if getattr(spec, oper):
                fld = Field(spec.default, description=column.doc, **spec.fld_kwargs)
                cru[oper][spec.name] = (spec.type, fld)

    # update with extras
    for spec in extras:
        # spec is an instance of ApiSpec, but for convenience with
        # apispec(), we allow a dict with key "apispec"
        if isinstance(spec, dict) and "apispec" in spec:
            spec = spec["apispec"]
        if spec.name is MISSING or spec.type is MISSING or spec.default is MISSING:
            raise RuntimeError(
                f"Programming error: ApiSpec has missing name or type: {spec}"
            )

        for oper in operations:
            if getattr(spec, oper):
                cru[oper][spec.name] = (spec.type, spec.default)

    # generate dynamic type:
    #
    #  class SomeModelApiSpec:
    #     create = SomeModelCreate (pydantic.BaseModel)
    #     read = SomeModelRead (pydantic.BaseModel)
    #     update = SomeModelUpdate (pydantic.BaseModel)
    #
    return type(
        f"{model_name}ApiSpec",
        (),
        {
            oper: create_model(
                f"{model_name}{oper.capitalize()}", __config__=model_config, **cru[oper]
            )
            for oper in operations
        },
    )
