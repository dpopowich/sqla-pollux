# quick_demo.py

To run you will need to add an extra dependencies:

```
uv add sqla-pollux[demos]
```


Then you can run with:

```
uv run ./quick_demo.py
```

Things to note in the demo:

* This demo uses aiosqlite as our database engine and uses an
  in-memory database.

* How _small_ the implementations of each REST call are. Just a few
  lines. That's the power of getting automatic validation from
  pydantic models.

* sqla_pollux.BaseModel has a number of high-level async methods to
  create, get, update, and delete models. There's a lot more. Take a
  look at the BaseModel method docstrings.

* sqla_pollux comes with a dumps/loads which can handle the special
  types it uses.  Of note: calling dumps(model_instance) returns
  model_instance.apispec.read.model_dump()

* sqla_pollux has high-order utilities for working with SQLAlchemy
  async engines, connections, and sessions.  Note the mock of
  middleware in MockRestApi.handler() to run each request in an
  isolated async session.
