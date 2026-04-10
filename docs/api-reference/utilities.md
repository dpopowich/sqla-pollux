# Utilities API Reference

## Session Management

### `dbsession` - Context Variable

Get the current async session within a request/transaction context.

```python
from sqla_pollux.utils.sqla import dbsession

async def my_function():
    session = dbsession.get()
    result = await session.execute(sa.select(User))
```

**Note**: This is automatically managed by sqla-pollux. Usually you don't need to access it directly.

### `new_isolated_trx()`

Context manager that creates a new isolated database transaction.

```python
from sqla_pollux.utils.sqla import new_isolated_trx

async with new_isolated_trx() as session:
    result = await session.execute(sa.select(User))
    # Runs in its own session/transaction
```

**Use cases**:
- Running queries in parallel
- Avoiding session state pollution
- Creating independent transactions

### `new_dbsession(engine, **kwargs)`

Create a new async session factory.

```python
from sqla_pollux.utils.sqla import new_dbsession
from sqlalchemy.ext.asyncio import create_async_engine

engine = create_async_engine("postgresql+asyncpg://...")
session_factory = new_dbsession(engine)

async with session_factory() as session:
    result = await session.execute(sa.select(User))
```

## Serialization

### `dumps(obj)`

Serialize object to JSON string.

```python
from sqla_pollux import dumps

user_instance = await User.get(1)
json_string = dumps(user_instance)
print(json_string)
# '{"id": 1, "username": "alice", ...}'
```

**Behavior**:
- For BaseModel instances: calls `instance.apispec.read.model_dump_json()`
- For other objects: uses standard json.dumps()
- Handles special sqla-pollux types

### `loads(json_string)`

Parse JSON string.

```python
from sqla_pollux import loads

data = loads('{"id": 1, "username": "alice"}')
print(data)
# {'id': 1, 'username': 'alice'}
```

**Behavior**:
- Standard json.loads()
- Handles special sqla-pollux types

## Initialization and Setup

### `sqla_init(engine, echo=False)`

Initialize SQLAlchemy engine and session management.

```python
from sqla_pollux.utils.sqla import sqla_init
from sqlalchemy.ext.asyncio import create_async_engine

engine = create_async_engine("postgresql+asyncpg://...")
sqla_init(engine, echo=True)
```

**Parameters**:
- `engine`: Async SQLAlchemy engine
- `echo`: Enable SQL query logging (default: False)

### `create_all(engine=None)`

Create all tables in the database.

```python
from sqla_pollux import create_all

await create_all()
```

**Parameters**:
- `engine` (optional): Specific engine to use

### `drop_all(engine=None)`

Drop all tables from the database.

```python
from sqla_pollux import drop_all

await drop_all()  # Careful!
```

**Parameters**:
- `engine` (optional): Specific engine to use

### `run_sync(fn, *args, **kwargs)`

Run a synchronous function in a thread pool from async code.

```python
from sqla_pollux import run_sync
import time

def blocking_operation():
    time.sleep(5)
    return "done"

result = await run_sync(blocking_operation)
print(result)  # "done"
```

**Use cases**:
- Running CPU-intensive code
- Using sync-only libraries
- Avoiding event loop blocking

## Caching

### `CachedData`

Lightweight per-instance cache.

```python
from sqla_pollux.utils import CachedData

user = await User.get(1)
user.cache.computed_value = expensive_computation()
user.cache.flag = True

# Retrieve later
if user.cache.flag:
    print(user.cache.computed_value)
```

**Behavior**:
- Returns None for undefined attributes
- Allows setting arbitrary attributes
- Each instance has its own cache
- Not persisted to database

## Type Utilities

### `populate_module_with_apispec(module, add_all=True)`

Populate module namespace with generated Pydantic models.

```python
import sys
from sqla_pollux import populate_module_with_apispec

from .models import User, Post, Comment

# Adds to module namespace:
# UserRead, UserCreate, UserUpdate
# PostRead, PostCreate, PostUpdate
# CommentRead, CommentCreate, CommentUpdate
populate_module_with_apispec(sys.modules[__name__])

# Now available at module level
from . import UserRead, PostCreate, CommentUpdate
```

**Parameters**:
- `module`: Module to populate (usually `sys.modules[__name__]`)
- `add_all` (bool): Also add names to `module.__all__` (default: True)

**Example**: In `models/__init__.py`

```python
import sys
from sqla_pollux import populate_module_with_apispec

from .user import User
from .post import Post
from .comment import Comment

populate_module_with_apispec(sys.modules[__name__])

__all__ = [
    "User",
    "Post", 
    "Comment",
    # UserRead, UserCreate, UserUpdate, etc. added automatically
]
```

Then import cleanly:

```python
from myapp.models import (
    User, UserRead, UserCreate, UserUpdate,
    Post, PostRead, PostCreate, PostUpdate,
)
```

## Exception Classes

### `ModelError`

Base exception for model-related errors.

```python
from sqla_pollux import ModelError

try:
    user = await User.get(999, raise_=True)
except ModelError:
    print("Model operation failed")
```

### `ModelNotFound`

Raised when a model instance cannot be found.

```python
from sqla_pollux import ModelNotFound

try:
    user = await User.get(999, raise_=True)
except ModelNotFound:
    print("User not found")
```

### `ValidationError`

Raised when Pydantic validation fails.

```python
from sqla_pollux import ValidationError

try:
    User.apispec.create(username="")  # Empty string
except ValidationError as e:
    print(f"Validation failed: {e}")
```

### `CannotCreateModel`

Raised when instance creation fails.

```python
from sqla_pollux import CannotCreateModel

# Custom model can raise this
try:
    user = await User.create(invalid_data=True)
except CannotCreateModel:
    print("Cannot create user")
```

### `CannotUpdateModel`

Raised when instance update fails.

```python
from sqla_pollux import CannotUpdateModel
```

### `CannotDeleteModel`

Raised when instance deletion is not allowed.

```python
from sqla_pollux import CannotDeleteModel

# Custom model can raise this
async def _can_delete_conditionally(self):
    if self.is_protected:
        raise CannotDeleteModel("Cannot delete protected user")
```

### `PermissionDenied`

Raised when user lacks permission.

```python
from sqla_pollux import PermissionDenied

try:
    user = await User.get(999, user=current_user)
except PermissionDenied:
    print("Access denied")
```

### `DuplicateModel`

Raised when duplicate entry is created.

```python
from sqla_pollux import DuplicateModel
```

## Quick Reference

| Task | Function | Async |
|------|----------|-------|
| Serialize to JSON | `dumps(obj)` | No |
| Deserialize JSON | `loads(json_str)` | No |
| Initialize DB | `sqla_init(engine)` | No |
| Create tables | `create_all()` | Yes |
| Drop tables | `drop_all()` | Yes |
| Run sync code | `run_sync(fn)` | Yes |
| New transaction | `new_isolated_trx()` | Context |
| Get current session | `dbsession.get()` | No |
| Populate module | `populate_module_with_apispec(module)` | No |
| Per-instance cache | `instance.cache` | No |

## Examples

### Setting Up a FastAPI Application

```python
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine
from sqla_pollux import create_all, sqla_init

app = FastAPI()

@app.on_event("startup")
async def startup():
    engine = create_async_engine("postgresql+asyncpg://localhost/myapp")
    sqla_init(engine)
    await create_all()

@app.on_event("shutdown")
async def shutdown():
    # Clean up if needed
    pass
```

### Exporting Pydantic Models

```python
# myapp/models/__init__.py
import sys
from sqla_pollux import populate_module_with_apispec

from .user import User
from .post import Post

populate_module_with_apispec(sys.modules[__name__])

# Now use in routes
from fastapi import FastAPI
from myapp.models import UserCreate, UserRead, PostRead

app = FastAPI()

@app.post("/users", response_model=UserRead)
async def create_user(user: UserCreate):
    pass
```

### Handling Errors

```python
from fastapi import HTTPException
from sqla_pollux import ModelNotFound, PermissionDenied

@app.get("/users/{user_id}")
async def get_user(user_id: int, current_user):
    try:
        user = await User.get(user_id, strict=True, user=current_user)
        return user.to_pydantic
    except ModelNotFound:
        raise HTTPException(status_code=404, detail="User not found")
    except PermissionDenied:
        raise HTTPException(status_code=403, detail="Access denied")
```

### Using Instance Cache

```python
@app.get("/expensive/{user_id}")
async def expensive_computation(user_id: int):
    user = await User.get(user_id)
    
    # Cache expensive computation
    if not hasattr(user.cache, "expensive_result"):
        result = await compute_something(user)
        user.cache.expensive_result = result
    
    return user.cache.expensive_result
```