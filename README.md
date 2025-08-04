# sqla-pollux

Write your python ORMs once: from [SQLAlchemy](https://www.sqlalchemy.org/) through your
[pydantic](https://docs.pydantic.dev/latest/install/)-validated REST API.

## Installation

Sqla-pollux will be available on PyPI soon, but in the meantime can be installed from its source on
github:

To add to your uv managed project:

```
uv add https://github.com/dpopowich/sqla-pollux.git
```

Or with pip:

```
pip install https://github.com/dpopowich/sqla-pollux.git
```

## Rationale

Typically, server-side web applications providing REST APIs receive JSON data (e.g., for a POST or
PUT), serialize the data into a python data structure, validate the data, then pass it on to the
underlying ORM, creating or updating a row in the database. The serialized version of the created or
updated model is returned as the response body.  Similarly for a GET request: query the database,
serialize the object(s) and return the JSON as the response body.

All too often this requires writing your data model multiple times. Once for the ORM, and one or
more times for serialization in-and-out of the REST framework.  For example, assume we model a user
of an application with the attributes listed in the table below. In the notes column we state how
the attributes can be written or read during create (POST), read (GET), and update (PUT,
PATCH). (Note: DELETE is not really a method we allow on model attributes: we don't delete an
attribute; maybe we update it to NULL, but that's still an update, in a PUT or PATCH).

|Attribute    | Type           | Note from REST API pov                                                       |
|-------------|----------------|------------------------------------------------------------------------------|
|`id`         | SERIAL         | User ID, (R)  - read-only, the system creates the ID                         |
|`username`   | VARCHAR        | Username (CR) - can specify on creation, but cannot update                   |
|`fullname`   | VARCHAR        | Full name (CRU) - full read-write access                                     |
|`password`   | TEXT           | Password (C) - user can create, but never read; and updates handled specially|
|`created`    | TIMESTAMP      | Created timestamp (R) - read-only, system sets the attr on account creation  |
|`lastlogin`  | TIMESTAMP      | Last login timestamp (R) - read-only, system maintains the value             |
|`metrics`    | JSON           | Internal metrics - (N/A) - user never sees this, for internal purposes only  |


Different frameworks offer different solutions, but generally one has to:

1. Define models in the ORM framework (e.g., SQLAlchemy, django ORM)
2. Use a serialization framework (e.g., django-rest-framework) and build intermediate models to
   represent your model in an out of your REST framework.  For example, in the above model, we'd
   need three different serializations.
   * **C**reate: username, fullname, password
   * **R**ead: id, username, fullname, created, lastlogin
   * **U**pdate: fullname

It's a lot of code. It's a lot of boilerplate. It's a lot of special-casing this attribute or that
based on the request method.

Other solutions oversimplify model definitions (e.g., [SQLModel](https://sqlmodel.tiangolo.com/)),
attempting to abstract away the subtleties and complexities of SQL.  One of the great features of
SQLAlchemy is how it allows you to express the full scope of SQL in python. Trying to encapsulate
SQL in type-hints is too limiting for anything more than a trivial schema.

## sqla-pollux's solution

Write once and annotate!  Define your models in **pure SQLAlchemy**, annotating each column in how
it should be serialized for our REST API. A SQLAlchemy event trigger will generate one [Pydantic
BaseModel](https://docs.pydantic.dev/latest/api/base_model/) for each of our CRU operations.  We can
use the pydantic models with a web application framework (e.g, aiohttp, fastapi) to serialize and
validate date in-and-out of our server.

Example: using the above user model, we define a SQLAlchemy model, annotating each column using stock
SQLAlchemy column definitions.  Note the use of `info=apispec.{SPEC}()` which annotates how the
attribute should be handled in our REST API.

```python
from sqla_pollux import (
    apispec,
    BaseModel,
)
import sqlalchemy as sa
from sqlalchemy.orm import configure_mappers

_AppUserTable = sa.Table(
    "appuser",
    BaseModel.metadata,

    # id is read-only
    sa.Column("id", sa.Integer, primary_key=True,
              info=apispec.READ_ONLY()),
    # username cannot be updated
    sa.Column("username", sa.String(255), nullable=False, unique=True,
              info=apispec.NO_UPDATE()),
    # fullname is available for CRU, which is the default if apispec is not specified
    sa.Column("fullname", sa.String(255), nullable=False),
    # password can be set on create; updates will be handled specially
    sa.Column("password", sa.TEXT, nullable=False, info=apispec.CREATE_ONLY()),
    # created is read-only
    sa.Column("created", sa.DateTime(timezone=True), info=apispec.READ_ONLY()),
    # lastlogin is read-only
    sa.Column("lastlogin", sa.DateTime(timezone=True), info=apispec.READ_ONLY()),
    # metrics is not available and so marked INTERNAL
    sa.Column("metrics", sa.JSON, default=dict, server_default=sa.text("'{}'"),
              info=apispec.INTERNAL()),
)

class AppUser(BaseModel):
      """Application User"""

      __table__ = _AppUserTable

configure_mappers()

```

> Side note: `Table` is used to define the SQL table, then assigned to the mapped class using the
  `__table__` attribute. This is not very common in SQLAlchemy examples and documentation, but is
  *highly* recommended. Unless you're very meticulous, burying your table definition directly in the
  mapped class clutters the class definition, making it hard to read and see at a glance _this is a
  class attribute (python)_ while _this is part of my table (SQL)_. It gets even harder as your
  database schema grows and you start using advanced features such as table constaints, mutli-column
  foreign keys, and complex indices. It cannot be recommended enough: separate your SQL definition
  from your python class definition. This method of defining tables will be used throughout all
  examples and demos.

The call to `configure_mappers()` (done here manually, but SQLAlchemy
does it automatically on first access of the mapper) triggers an event
which populates all models in the mapper with an attribute `apispec`
which itself has three attributes, `create`, `read`, `update`, each a
pydantic model validating the properties as specified by the calls to
`apispec`:

```python
>>> print("CREATE:", dumps(AppUser.apispec.create.model_json_schema(), indent=3))
CREATE: {
   "properties": {
      "username": {
         "title": "Username",
         "type": "string"
      },
      "fullname": {
         "title": "Fullname",
         "type": "string"
      },
      "password": {
         "title": "Password",
         "type": "string"
      }
   },
   "required": [
      "username",
      "fullname",
      "password"
   ],
   "title": "AppUserCreate",
   "type": "object"
}
>>>
>>> print("READ:", dumps(AppUser.apispec.read.model_json_schema(), indent=3))
READ: {
   "properties": {
      "id": {
         "title": "Id",
         "type": "integer"
      },
      "username": {
         "title": "Username",
         "type": "string"
      },
      "fullname": {
         "title": "Fullname",
         "type": "string"
      },
      "created": {
         "anyOf": [
            {
               "format": "date-time",
               "type": "string"
            },
            {
               "type": "null"
            }
         ],
         "default": null,
         "title": "Created"
      },
      "lastlogin": {
         "anyOf": [
            {
               "format": "date-time",
               "type": "string"
            },
            {
               "type": "null"
            }
         ],
         "default": null,
         "title": "Lastlogin"
      }
   },
   "required": [
      "id",
      "username",
      "fullname"
   ],
   "title": "AppUserRead",
   "type": "object"
}
>>>
>>> print("UPDATE:", dumps(AppUser.apispec.update.model_json_schema(), indent=3))
UPDATE: {
   "properties": {
      "fullname": {
         "title": "Fullname",
         "type": "string"
      },
   },
   "required": [
      "fullname"
   ],
   "title": "AppUserUpdate",
   "type": "object"
}
>>>
```

## API Documentation

Coming soon...

## Demos

* `demos/quick-demo` -- uses the above model and mocks an API to demonstrate CRUD operations on
  system users.

Full demos using aiohttp and FastAPI are forthcoming...

## The Name

Why *pollux*?

A key feature of the library is providing a DRY method of using SQLAlchemy to make a single model
definition that automagically defines our pydantic models. We don't need many, we need one. Of the
Gemini twins, Castor and Pollux, we choose only one, the immortal one, Pollux.

## License

MIT.  See the LICENSE file.
