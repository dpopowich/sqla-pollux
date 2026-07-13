"""Helper to create SQL VIEWs based off table definitions.

See: `create_view()`
"""
# stdlib imports
import functools

# venv imports
import sqlalchemy as sa
from sqlalchemy.ext import compiler as sa_compiler
from sqlalchemy.schema import DDLElement

# views created by create_view() are modeled by a Table instance. We
# use a MetaData instance disassociated from the application's, so these
# tables are not created/dropped.
_VIEW_METADATA = sa.MetaData()
# used to ensure no duplicate view names
_VIEWS = set()
# sentinel
MISSING = object()

class CreateView(DDLElement):
   """CreateView - a DDL element used to issue 'CREATE OR REPLACE VIEW...'

   Args -
      schema -
      name - name of the view
      selectable - a select() construct.
      check - if True (the default), add "WITH CHECK OPTION" when creating view.
      schema - if not None (the default) apply the schema name when creating

   Example:

      Foo = Table(...)

      big_foo = select(Foo).where(Foo.c.size > 100000)
      view = CreateView('big_foo', big_foo)

   """

   # pylint: disable=abstract-method

   def __init__(self, name, selectable, /, *, check=False, schema=None):
      self.name = name
      self.selectable = selectable
      self.check = check
      self.schema = schema


class DropView(DDLElement):
   """DDL element to issue 'DROP VIEW IF EXISTS...'

   Args -
      name - name of view
      cascade - (bool) if True (the default) use "CASCADE" when dropping view.
      schema - if not None (the default) apply the schema name when dropping

   """

   # pylint: disable=abstract-method

   def __init__(self, name, /, *, cascade=False, schema=None):
      self.name = name
      self.cascade = cascade
      self.schema = schema


@sa_compiler.compiles(CreateView)
def compile_create_view(element, compiler, **_kw):
   """Compile CreateView instance to "CREATE VIEW" SQL command.

   See CreateView docstring.
   """
   name = compiler.dialect.identifier_preparer.quote(element.name)
   if element.schema is not None:
      schema = compiler.dialect.identifier_preparer.quote(element.schema)
      name = f"{schema}.{name}"
   sql = compiler.sql_compiler.process(element.selectable, literal_binds=True)
   check = "WITH CHECK OPTION" if element.check else ""

   return f"CREATE OR REPLACE VIEW {name} AS {sql} {check}"


@sa_compiler.compiles(DropView)
def compile_drop_view(element, compiler, **_kw):
   """Compile DropView instance to "DROP VIEW SQL command.

   See DropView docstring.
   """
   name = compiler.dialect.identifier_preparer.quote(element.name)
   if element.schema is not None:
      schema = compiler.dialect.identifier_preparer.quote(element.schema)
      name = f"{schema}.{name}"
   cascade = "CASCADE" if element.cascade else ""
   return f"DROP VIEW IF EXISTS {name} {cascade}"

def _make_column(col):
   """Make a new Column instance from the ColumnClause given by col"""
   # This is used by `create_view()` to convert an item in a
   # selectable's column list to a new Column instance for use by a
   # Table that reflects the underlying view.

   args = [col.name, col.type]
   kwargs = {}

   # if Label, use the underlying element to determine the kwargs
   if isinstance(col, sa.Label):
      col = col.element

   for attr in ("default", "nullable", "info"):
      if (val := getattr(col, attr, MISSING)) is not MISSING:
         kwargs[attr] = val

   return sa.Column(*args, **kwargs)

def create_view(func=None, *, name, metadata, primary_key, check=False, cascade=False):
   """Decorator to create a view.

   Args:
      name (str) - name of view
      metadata (MetaData) - sqlalchemy metadata to associate the view with
      primary_key (str|Sequence[str]) - the name of the column to act as primary key. May also be a
                  Sequence of names for a multicolumn primary key
      check (bool) - If True (default: False) add "WITH CHECK OPTION" on creation of the view.
      cascade (bool) - If True (default: False) on drop of the view, add "CASCADE"

   The decorated function must return a selectable. This decorator
   will return a Table which can be used with the ORM:

      @create_view(name="myview", BaseModel.metadata)
      def _MyView():
          # return a Select()
          return sa.select(...).select_from(...).where(...)


      # use _MyView with the ORM
      class MyView(BaseModel):
         __table__ = _MyView


   NOTE: sqlalchemy listeners will be registered to create/drop views.

   Returns - Table

   """
   if func is None:
      return functools.partial(create_view, name=name, metadata=metadata, primary_key=primary_key,
                               check=check, cascade=cascade)

   # ensure no dups:
   if (dedup := (metadata.schema, name)) in _VIEWS:
      raise ValueError(f"View {name} already created in schema")
   _VIEWS.add(dedup)

   # call the decorated callable to get the selectable
   selectable = func()

   # The *args to Table() for the generated table that represents the
   # view. We copy the columns from the selectable; this disassociates
   # the existing columns from their parent.
   col = lambda c: c if isinstance(c, sa.Column) else c.element
   view_args = [_make_column(c) for c in selectable.selected_columns]

   # add primary key(s)
   if isinstance(primary_key, str):
      primary_key = (primary_key,)
   view_args.append(sa.PrimaryKeyConstraint(*primary_key))

   # create the view - NB: not adding to our metadata - we don't want this table created by sqla
   view = sa.Table(name, _VIEW_METADATA, *view_args)

   sa.event.listen(metadata, "after_create", CreateView(name, selectable, check=check, schema=metadata.schema))
   sa.event.listen(metadata, "before_drop", DropView(name, cascade=cascade, schema=metadata.schema))

   return view
