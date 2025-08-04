"""Abstract base class for all ORMs"""

# stdlib imports
import inspect
import logging
import sys

# venv imports
import sqlalchemy as sa
import yaml

from . import (
    exceptions,
)
from .utils import (
    CachedData,
)
from .utils.sqla import (
    AFTER_FLUSH_POSTEXEC_QUEUE,
    Base,
    dbsession,
    new_isolated_trx,
)

logger = logging.getLogger(__name__)

# for reading/writing large objects
CHUNK_SIZE = 2**16  # 64K


# Note: AsyncAttrs adds `awaitable_attrs` attribute to all instances.  See:
# https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#sqlalchemy.ext.asyncio.AsyncAttrs
class BaseModel(sa.ext.asyncio.AsyncAttrs, Base):
    """Base model for all SQLAlchemy models"""

    __abstract__ = True

    __mapper_args__ = dict(
        eager_defaults=True,
    )

    __apispec_excludes__ = frozenset()
    __apispec_exclude_private__ = True
    __apispec_extras__ = tuple()

    ######################################################################
    # dunders
    ######################################################################

    def __json__(self):
        """serialize to a dict, suitable for a JSON object representation"""
        return {name: getattr(self, name) for name in self.apispec.read.model_fields}

    def __str__(self):
        """stringified representation"""
        return f"{self.__class__.__name__}: {self.id}"

    ######################################################################
    # properties
    ######################################################################
    @property
    def cache(self):
        """An abritrary per-instance cache.

        See utils.CachedData

        Returns - opaque object suitable to set attributes
        """
        try:
            cache = self._cached_data
        except AttributeError:
            cache = self._cached_data = CachedData()

        return cache

    @property
    def to_pydantic(self):
        """Convert to the object's pydantic READ object"""
        return self.apispec.read(**self.__json__())

    ######################################################################
    # staticmethods
    ######################################################################
    @staticmethod
    def begin_nested():
        """Begin a nested transaction (SAVEPOINT) in a context.

        async with Model.begin_nested():
           ...

        """
        session = dbsession.get()
        return session.begin_nested()

    @staticmethod
    def cau():
        """Return the Current Authenticated User.

        A convenience to fetch the Current Authenticated User (cau)
        from any model; identical to:

           from . import BaseUser

           BaseUser.current_authenticated_user()

        Returns - BaseUser
        """
        # avoid circ imports
        from . import BaseUser

        return BaseUser.current_authenticated_user()

    @staticmethod
    async def execute(query, *, autoflush=None, params=None):
        """Execute query in session.

        Args -
           autoflush - if not None, must be a boolean to explicitly
                       set auto-flushing (which is normally True)

        """
        # handle autoflush (False by default, see: sqla.SessionFactory)
        session = dbsession.get()
        if autoflush is not None:
            save_autoflush = session.autoflush
            session.autoflush = autoflush
        try:
            return await session.execute(query, params=params)
        finally:
            if autoflush is not None:
                session.autoflush = save_autoflush

    @staticmethod
    async def _flush():
        """Flush the session.

        This will continue flush attempts while the session remains
        "dirty", which can happen if after_flush hooks add/modify
        instances.
        """
        session = dbsession.get()
        await session.flush()
        for _flush_guard in range(100):
            if not session.new | session.dirty | session.deleted:
                break
            await session.flush()
        else:
            raise exceptions.FlushError(
                "Over 100 subsequent flushes have occurred. "
                "Is an after_flush() hook creating new objects?"
            )

    @staticmethod
    async def lo_reader(oid, chunk_size=CHUNK_SIZE):
        """An AsyncIterable reading a Large Object.

        Args -
           oid - (int) Large Object OID
           chunk_size - (int) Size of chunks to read, default: 64K

        Using this generator prevents a Very Large File from being read
        into memory all at once.

        Each iteration yields bytes of size chunk_size.  If length of
        bytes is less than chunk_size, it is the last iteration.

        NB: this generator runs in its own database session!
        """
        logger.debug("lo_reader OPENING: %d (chunk_size: %d)", oid, chunk_size)

        query = sa.text("SELECT lo_get(:oid, :offset, :chunk_size)")
        params = dict(oid=oid, chunk_size=chunk_size)
        offset = 0
        chunk = 0

        async with new_isolated_trx() as sess:
            while True:
                params["offset"] = offset
                result = await sess.execute(query, params=params)
                data = result.scalars().one()
                logger.debug(
                    "lo_reader READ %d, CHUNK %d: %d bytes", oid, chunk, len(data)
                )
                if data:
                    yield data
                if len(data) < chunk_size:
                    break
                offset += chunk_size
                chunk += 1

        logger.debug("lo_reader CLOSING: %d", oid)

    @staticmethod
    async def load_fixture(fixture_file):
        """Load fixture

        Args -
           fixture_file: a yaml file holding one or more yaml documents.
                         Each document has three required properties:

              module: str - the full name of the module to find model, e.g., myapp.models
              model: str - name of model as known in the given module, e.g., MyModel
              rows: list - a list of dicts suitable for use with calling
                    Model.insert().values() or Model.create(**row)

           Optionally it may also specify:

              use_create: bool - When False (the default), use
                          model.insert() to insert db rows. If True, use
                          model.create() to create db rows. Set to True
                          if you need application level "triggers" for
                          their side effects.
              force: list[str], null, '*' - list of attributes in the
                     rows dicts to force on creation.  Default is '*'
                     (all attrs); to turn off specify null.  Ignored
                     unless `use_create` is True.
              pk: str - (optional, default: 'id') name of primary key
                  field (or other unique Integer field based on a
                  sequence).  Only used if seq is not null
              seq: str|null - (optional, default: '{table}_{pk}_seq')
                   name of sequence to update to max value of pk column.
                   If null, no update will happen.

        Returns - None
        """
        for doc in yaml.safe_load_all(open(fixture_file, encoding="utf-8")):
            # get module, model, rows, and other optional keys
            module = sys.modules[doc['module']]
            model = getattr(module, doc["model"])
            table = model.__table__.name
            rows = doc["rows"]

            # optional keys
            force = doc.get("force", "*")
            use_create = doc.get("use_create", False)
            pk = doc.get("pk", "id")
            seq = doc.get("seq", f"{table}_{pk}_seq")

            # create each row
            if use_create:
                for row in rows:
                    if force is not None:
                        row["_force"] = force
                    await model.create(**row)
            else:
                insert = model.insert().values(rows)
                await model.execute(insert)

            # update SEQUENCE
            if seq:
                await model.execute(
                    sa.text(f"select setval('{seq}', max({pk})) from {table}")
                )

    @staticmethod
    def register_after_flush_postexec(fn, *args, **kwargs):
        """Register an after flush call.

        Args -
           fn - synchronous callable or an awaitable.  If an awaitable,
                args and kwargs are ignored.
           args, kwargs - will be passed to `fn` when it is called during
                          the after_flush_postexec event.

        Returns: None
        """
        session = dbsession.get()
        queue = session.info.setdefault(AFTER_FLUSH_POSTEXEC_QUEUE, [])
        queue.append((fn, args, kwargs))

    @staticmethod
    async def stream(query):
        """Execute query in session"""
        return await dbsession.get().stream(query)

    ######################################################################
    # classmethods
    ######################################################################
    @classmethod
    def channel(cls, instid):
        """Given an instance of this class or an ID of an instance, return communication channel"""
        # we use the ID for the instance, assuming `.id` is the attribute.  If
        # it's not an instance we assume it is the ID
        if isinstance(instid, cls):
            instid = instid.id
        # "TABLE:ID"
        base = cls.__table__.name
        return f"{base}:{instid}"

    @classmethod
    async def _create(cls, **attrs):
        """Create instance and add it to the session, setting attrs on the instance.

        See _update() for details on setting attributes.

        NB: This is the method to override in subclasses to
            conditionally process the creation of new instances (not the
            create*() methods, taking care to:

           1. Call super()._create(...)
           2. No flushing!  (Though low-level calls with .execute() are OK.)
           3. Return the new instance.

        Returns - instance of class
        """
        inst = cls()
        dbsession.get().add(inst)
        return await inst._update(_create=True, **attrs)

    @classmethod
    async def create(cls, **attrs):
        """Create instance and add it to the session, then flush the session.

        This delegates the work to create_no_flush() and subclasses
        should prefer overriding that method for model-specific business
        logic over this method.

        Returns - instance of class
        """
        inst = await cls._create(**attrs)
        await cls._flush()
        return inst

    @classmethod
    async def create_no_flush(cls, **attrs):
        """Create instance and add it to the session without flushing.

        Returns - instance of class
        """
        return await cls._create(**attrs)

    @classmethod
    def bulk_delete(cls, **kwargs):
        """Wrapper around sqlalchemy.delete() using the table of the model.
        All other kwargs are passed on to that function.

        See: https://docs.sqlalchemy.org/en/14/core/dml.html#sqlalchemy.sql.expression.delete

        Returns - Delete object to be executed.
        """
        return sa.delete(cls, **kwargs)

    @classmethod
    def bulk_update(cls, **kwargs):
        """Wrapper around sqlalchemy.update() using the table of the model.
        All other kwargs are passed on to that function.

        See: https://docs.sqlalchemy.org/en/14/core/dml.html#sqlalchemy.sql.expression.update

        Returns - Update object to be executed.
        """
        return sa.update(cls, **kwargs)

    @classmethod
    async def count(cls, *where, col=None):
        """Return the count of instances for this class.

        Args -

           *where - filters to apply as with Select.where() (see SQLA docs)

           col - if None (default), the query will be COUNT(*).  If not
                 None, a Column to count (i.e, number of non-NULL values
                 in specified column in rows satisfying the where),
                 ie, COUNT(column). To execute COUNT(DISTINCT column),
                 call distinct() on the column, e.g,:

                    SomeModel.count(col=SomeModel.foo.distinct())

        Note: be careful the where only applies to this model, else
              sqlalchemy will create a cross-join and the count may be
              wildly off if the filters don't protect against a full
              cross product between referenced table rows.

        Note: the intention behind this method is to get counts for
              simple queries, e.g., how many X have a column with value
              Y.  This is great for scripts, dev shells, unit tests,
              etc.  For counts of complex queries, it is probably better
              to build the query manually.

        Returns - integer count
        """
        count = sa.func.count(col) if col is not None else sa.func.count()
        query = sa.select(count).select_from(cls).where(*where)
        res = await cls.execute(query)
        return res.scalar()

    @classmethod
    async def exists(cls, *where, from_=None):
        """Do instances exist?

        Args -
           where - passed to Select.where(); see sqlalchemy docs.
           from_ - if None, defaults to `cls`.

        NB: the where clause must only reference conditions from the
        table referenced by `from_` otherwise results may be unexpected.

        Returns - boolean
        """
        from_ = from_ or cls
        subquery = sa.select(1).select_from(from_).where(*where)
        query = sa.select(subquery.exists())

        res = await cls.execute(query)
        return res.scalar()

    @classmethod
    async def get(cls, pk, /, *, strict=False, user=None, raise_=False, **kwargs):
        """Get instance by primary key

        Args -
           pk - primary key value for model.

           raise_ - raise ModelNotFound if instance is not found.
           strict - if True it's shorthand for: raise_=True, user=cls.cau()
           user - if not None call inst.has_permission(user=user) and if
                  not True raise PermissionDenied.
           kwargs - passed on to AsyncSession.get().

        Return - instance or None if not found (and strict/raise_ is False).
        """
        # process strict
        if strict:
            raise_ = True
            if user is None:
                user = cls.cau()

        # fetch instance
        inst = await dbsession.get().get(cls, pk, **kwargs)
        # raise?
        if inst is None and raise_:
            raise exceptions.ModelNotFound(f"{cls.__name__} not found")
        # permission?
        if (
            inst is not None
            and user is not None
            and not await inst.has_permission(user=user)
        ):
            raise exceptions.PermissionDenied(f"No access to {cls.__name__}")

        return inst

    @classmethod
    def insert(cls, **kwargs):
        """Wrapper around sqlalchemy.dialects.postgresql.insert() using the
        table of the model.  All other kwargs are passed on to that function.

        See: https://docs.sqlalchemy.org/en/14/dialects/postgresql.html

        Returns an Insert object to be executed.
        """
        return sa.dialects.postgresql.insert(cls, **kwargs)

    @classmethod
    async def list(cls, *entities, scalar=True, **kwargs):
        """Return list of instances, delegating search to cls.select() for the query."""
        query = cls.select(*entities, **kwargs)
        result = await cls.execute(query)
        if scalar:
            result = result.scalars()
        return result.all()

    @classmethod
    async def lo_unlink(cls, oid):
        """Unlink (delete) Large Object.

        NB: Eschew using this method: it is prefered that a trigger be
        put in place to manage Large Objects on UPDATE and DELETE for
        each table row.  See:
        https://www.postgresql.org/docs/12/lo.html.
        """
        logger.debug("lo_unlink: %d", oid)
        query = sa.text("SELECT lo_unlink(:oid)")
        await cls.execute(query, params=dict(oid=oid))

    @classmethod
    def NotFound(cls):
        """As a convenience, return exceptions.ModelNotFound instance"""
        return exceptions.ModelNotFound(f"{cls.__name__} not found")

    @classmethod
    async def one(cls, *args, **kwargs):
        """Return one instance or raise ModelNotFound

        See one_or_none() for details.
        """
        entity = await cls.one_or_none(*args, **kwargs)
        if entity is None:
            raise exceptions.ModelNotFound(f"{cls.__name__} instance not found")

        return entity

    @classmethod
    async def one_or_none(cls, *entities, query=None, **kwargs):
        """Return one instance or None if no instance exists.

        Args -
           *entities - passed to cls.select() to build the query.
           query - (sqla Select) - if given, entities and kwargs are
                   ignored and this is used as the query.

        NB: If query is None, the query will be: cls.select(*entities,
            **kwargs).  See .select() for details.

        NB: if the returned Row has many columns, the full Row (tuple) is
        returned.  If the Row has only one column, that column is
        returned, i.e., row[0].

        Raises - raises MultipleResultsFound if more than one record exists.
        """
        query = query if query is not None else cls.select(*entities, **kwargs)
        result = await cls.execute(query)
        row = result.one_or_none()
        return row and (row if len(row._fields) > 1 else row[0])

    @classmethod
    async def resolve(cls, inst, **kwargs):
        """Given an ID or an instance of a model, resolve it to an instance.

        This is shorthand for:

           if not isintance(inst, cls):
              inst = await cls.get(inst, **kwargs)

        This allows methods to receive either an ID (typically from
        outside callers into the model API) or an instance (typically
        from callers within the API) and resolve the reference to an
        instance.  For example, for some arbitrary method:

           def method(self, *, company, user, ...):
              ...
              company = await Company.resolve(company)
              user = await User.resolve(user)

        Depending on kwargs, this will return one of: an instance of
        model, None, or raise an exception.

        See: BaseModel.get() for discussion of kwargs and possible
        raised exceptions.

        Returns: instance of class

        """
        return inst if isinstance(inst, cls) else await cls.get(inst, **kwargs)

    @classmethod
    def select(
        cls,
        *entities,
        where=None,
        group_by=None,
        order_by=None,
        offset=None,
        limit=None,
        distinct=False,
    ):
        """Return select (query) for this model"""
        if not entities:
            entities = (cls,)
        query = sa.select(*entities)
        if where is not None:
            if isinstance(where, (tuple, list)):
                query = query.where(*where)
            else:
                query = query.where(where)
        if group_by is not None:
            query = query.group_by(group_by)
        if order_by is not None:
            if isinstance(order_by, (tuple, list)):
                query = query.order_by(*order_by)
            else:
                query = query.order_by(order_by)
        if offset is not None:
            query = query.offset(offset)
        if limit is not None:
            query = query.limit(limit)
        if distinct:
            query = query.distinct()

        return query

    ######################################################################
    # instance methods
    ######################################################################
    async def _can_delete_conditionally(self):
        """Can this instance be deleted?

        During a call to _delete(), this method is called and if the
        model should NOT be deleted, it will raise an exception
        (exceptions.CannotDeleteModel is a good candidate) See also: _delete().

        Be default this is a no-op, but may be overridden to implement
        conditional deleting.
        """
        pass

    async def _soft_delete(self):
        """A model may implement "soft" deletes, that is, when deleted, a flag
        is set in the database such that it will not be retrieved.

        This method is called during deletion (see _delete()) and if it
        returns True, signaling the model uses soft deletes, no actual
        deletion occurs.  By default it returns False, causing an actual
        deletion.

        NOTE: all conditional requirements are still enforced, even if
        soft deletes are implemented for a model (see: _can_delete_conditionallly()).

        This method should NOT flush to the database.  Recommended: set
        the relevant attribute and allow the dirty state to be picked up
        during the flushing process.  That said, this method is async in
        case access to the database is needed.
        """
        return False

    async def _delete(self, *, force=False):
        """Delete instance without flushing.

        Args -

           force - when True (default: False) do the deletion (and all
                   related cascades).  When False, raise
                   NeedsConfirmation with dict returned from `compute_related()`.

        NB: This is the method to override in subclasses to
            conditionally process the deletion of instances (not the
            delete*() methods, taking care to:

           1. Call super()._delete(...)
           2. No flushing!  (Though low-level calls with .execute() are OK.)

        NB: Some users only have permission to *safely* delete data,
            meaning the data being deleted may not cause any related
            data to be deleted (see __can_delete_noref__).  Also, a call
            to self._can_delete_conditionally() is made to see if the
            instance is in a state that should prevent conditional
            deletion.

        Returns - None

        """
        await self._can_delete_conditionally()

        if not await self._soft_delete():
            await dbsession.get().delete(self)

    async def delete(self, **kwargs):
        """Delete instance, flushing the session.

        See _delete() for details.
        """
        await self._delete(**kwargs)
        await self._flush()

    async def delete_no_flush(self, **kwargs):
        """Delete instance without flushing the session.

        See _delete() for details.
        """
        await self._delete(**kwargs)

    async def has_permission(self, *, user=None):
        """Does the user have permission to access this object?

        Args -
           user - instance of User.  If None, use Current Authenticated User.

        BaseModel implements this very strict default: if the user is an admin user,
        return True, else False.
        """
        user = user or self.cau()
        return user.is_admin

    async def refresh(self, attribute_names=None):
        """Refresh the instance"""
        await dbsession.get().refresh(self, attribute_names=attribute_names)
        return self

    async def _update(self, _create=False, _force=frozenset(), **attrs):
        """Update instance without flushing.

        Attrs -
           _create - if True, this is the intial creation
           _force - should be a set containing attr names that
                    should be set on the instance, regardless of its
                    apispec -or- the string '*', allowing any field.

           **attrs - instance attributes to update

        Which attrs are allowed to be updated depends on `_create` and
        the ApiSpec for creation vs updating.  A disallowed attr is
        ignored.

        NB: This is the method subclasses should override if conditional
            processing on updates is necessary, instead of the update*()
            methods, taking care of three actions:

           1. Must call super()._update(...)
           2. No flushing!  (Though low-level calls with .execute() are OK.)
           3. Return self.

        NB: While this method makes no use of await, the method is async
            because subclass override may likely need to do async
            lookups in the database.  Making this async keeps the
            signature the same across models.

        Returns - self
        """
        allowed_fields = (
            self.apispec.create.model_fields
            if _create
            else self.apispec.update.model_fields
        ).keys() | _force

        for attr, val in attrs.items():
            if {attr, "*"} & allowed_fields:
                setattr(self, attr, val)

        return self

    async def update(self, **attrs):
        """Update instance, then flush.

        See _update() for details.

        Returns - self
        """
        await self._update(**attrs)
        await self._flush()
        return self

    async def update_no_flush(self, **attrs):
        """Update instance without flushing.

        See _update() for details.

        Returns - self
        """
        return await self._update(**attrs)

    async def update_with_lo(self, *, lo_attrname, filename, chunk_size=2**16, **attrs):
        """Update with large object

        Args -

           attrname - (str) name of column on this instance to save the
                      OID of the large object.
           filename - (str|Path) name of file to store
           chunk_size - (int) Size of chunks to read, default: 64K
           attrs - other attributes to update along with lo_attrname

        Using this method prevents a Very Large File from being read
        into memory all at once.

        NB: after creating the large object (and acquiring its OID) this
        method calls self.update(lo_attrname=OID, **attrs) and therefore
        flushes the db session.

        NB: warning: this method does not unlink a previously created
        large object. It is assumed the postgresql "lo" module is
        installed with trigger configured; see:
        https://www.postgresql.org/docs/12/lo.html.

        Returns - self
        """
        logger.debug("lo_writer OPENING: %s (chunk_size: %d)", filename, chunk_size)

        # get new OID
        query = sa.text("SELECT lo_creat(-1)")
        result = await self.execute(query)
        oid = result.scalars().one()

        # query for putting a chunk
        query = sa.text("SELECT lo_put(:oid, :offset, :data)")
        with open(filename, "rb") as bfile:
            offset = 0
            chunk = 0
            params = dict(oid=oid)

            # keep reading until there's no data
            data = bfile.read(chunk_size)
            while data:
                # update offset/data in params
                params["offset"] = offset
                params["data"] = data
                logger.debug(
                    "lo_writer WRITE: chunk %d, offset %d, size %d",
                    chunk,
                    offset,
                    len(data),
                )
                await self.execute(query, params=params)

                # update offset, chunk, data
                offset += len(data)
                chunk += 1
                data = bfile.read(chunk_size)

        logger.debug("lo_writer CLOSING: %d", oid)

        # include the large object attr name in updates
        attrs[lo_attrname] = oid
        return await self.update(**attrs)


def populate_module_with_apispec(module, add_all=True):
   """Populate a module namespace with each BaseModel's apispec.

   For example:

      If module.SomeModel subclasses BaseModel, SomeModelRead,
      SomeModelUpdate, and SomeModelCreate will be added to the
      modules namespace where:

         SomeModelRead = SomeModel.apispec.read
         SomeModelUpdate = SomeModel.apispec.update
         SomeModelCreate = SomeModel.apispec.create

      If add_all is True, the names will also be added to module.__all__

   This is useful in an __init__.py which imports the packages models, e.g.

       import sys
       from sqla_pollux import populate_module_with_apispec


       from abc import ABCModel
       ...
       from xyz import XYZModel

       populate_module_with_apispec(sys.modules[__name__])

   """
   for name, obj in inspect.getmembers(module):
      if isinstance(obj, type) and issubclass(obj, BaseModel) and obj is not BaseModel:
         for attr in "read", "create", "update":
            spec_name = name + attr.capitalize()
            # Add, e.g., User.apispec.read, to globals as UserRead
            setattr(module, spec_name, getattr(obj.apispec, attr))
            # also add to __all__
            if add_all:
               try:
                  all_ = getattr(module, '__all__')
               except AttributeError:
                  all_ = []
                  setattr(module, '__all__', all_)

               all_.append(spec_name)
