"""SQLAlchemy configuration utilities"""

# stdlib imports
import contextlib
import contextvars
from datetime import datetime, date
import enum
import inspect
import json
import re

# venv imports
from sqlalchemy import (
    event,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
)
from sqlalchemy.orm import (
    declarative_base,
    Mapper,
    Session,
    sessionmaker,
)
from sqlalchemy.sql import compiler
from sqlalchemy.util import (
    await_only,
    IdentitySet,
)

# app imports
from .. import (
    exceptions,
)
from . import (
    generate_model_apispec,
)

# For using "ON DELETE SET NULL(column)".
# See: https://github.com/sqlalchemy/sqlalchemy/issues/11595#issuecomment-2334453427
compiler.FK_ON_DELETE = re.compile(
    r"^(?:RESTRICT|CASCADE|SET NULL|SET NULL\s?(.*)|NO ACTION|SET DEFAULT)$", re.I
)

AFTER_FLUSH_POSTEXEC_QUEUE = "after_flush_postexec_queue"

# for large inserts (e.g. CSV uploads)
MAX_SQL_QUERY_ARGUMENTS = 4000


# ORM base class
Base = declarative_base()

# session factory - bound to engine in sqla_init() which must be called on
# application initialization prior to first database access
SessionFactory = sessionmaker(
    expire_on_commit=False, autoflush=False, class_=AsyncSession, future=True
)

# our per-task database session - keyed on current task's name
dbsession = contextvars.ContextVar(f"{__name__}.dbsession")

# engine - set by sqla_init()
_ENGINE = None


class SqlaJSON:
    """Custom JSON serialization specifically for PostgreSQL when storing
    types JSON and JSONB.  NB: this is different than the
    application-level json serialization.

    In particular, this handles datetime and date objects.
    """

    # regex to match datetime or date as stored by the encoder
    DATE_RE = re.compile(r"<d(t)?:(.*)/>$")

    @classmethod
    def _try_date_parse(cls, value):
        """Check value (str) to see if it's a datetime or date.

        Returns - datetime or date if match else value
        """
        match = cls.DATE_RE.match(value)
        if not match:
            return value

        dt_class = datetime if bool(match.group(1)) else date
        dt_match = match.group(2)

        try:
            value = dt_class.fromisoformat(dt_match)
        except Exception:
            pass

        return value

    @staticmethod
    def _encode_default(obj):
        """Encode special types"""
        if hasattr(obj, "__json__"):
            return obj.__json__()
        if isinstance(obj, datetime):
            return f"<dt:{obj.isoformat()}/>"
        if isinstance(obj, date):
            return f"<d:{obj.isoformat()}/>"
        if isinstance(obj, enum.Enum):
            return obj.name
        if isinstance(obj, set):
            return list(obj)
        raise TypeError(f"Cannot JSON encode {type(obj)}")

    @classmethod
    def _decode_object_hook(cls, obj):
        """The reverse of _json_encode_defaults"""
        for key, val in obj.items():
            if isinstance(val, list):
                for i, item in enumerate(val):
                    if isinstance(item, str):
                        val[i] = cls._try_date_parse(item)
            elif isinstance(val, str):
                obj[key] = cls._try_date_parse(val)
        return obj

    @classmethod
    def dumps(cls, obj):
        """json.dumps for our usage with postgres"""
        return json.dumps(obj, default=cls._encode_default)

    @classmethod
    def loads(cls, obj):
        """json.loads for our usage with postgres"""
        return json.loads(obj, object_hook=cls._decode_object_hook)


@contextlib.contextmanager
def new_dbsession():
    """Create new database session.

    Yields - AsyncSession.  It is the callers responsibility to close
    the session, typically by running in an `async with`; see sample
    below.

    The yielded AsyncSession can be used to manage a transaction, e.g.,
    below, if there's an exception in the inner code block the
    transaction will be aborted, else it will be committed.

       with new_dbsession() as sess:
          async with sess:
             async with sess.begin():
                ...

    NB: this context manager will set the `dbsession` contextvar
    defined in this module and reset when exiting the context.
    """
    session = SessionFactory()
    token = dbsession.set(session)
    try:
        yield session
    finally:
        dbsession.reset(token)


@contextlib.asynccontextmanager
async def new_isolated_trx(user=None):
    """Create new (isolated) database transaction.

    Args -

       user - User instance -or- awaitable that will return a user.  If
              an awaitable, it is awaited within the newly created db session.

    This is a convenience function, in particular for scripts:

       async with new_isolated_trx() as sess:
          # code running in a transaction with ANONYMOUS as the CUA

    or:

       async with new_isolated_trx(some_user) as sess:
          # code running in a transaction with some_user user as the CUA

    or:

       async with new_isolated_trx(User.login(username='admin')) as sess:
          # code running in a transaction with `admin` as the CUA

    """
    with new_dbsession() as sess:
        async with sess:
            async with sess.begin():
                if user:
                    if inspect.isawaitable(user):
                        user = await user
                        if user is None:
                            raise exceptions.ModelNotFound("Could not resolve user")
                    with user:
                        yield sess
                else:
                    yield sess


def sqla_init(uri, **engine):
    """Initialize sqla engine, binding it to the session factory.

    Returns - the initialized engine instance.
    """
    # pylint: disable=global-statement
    global _ENGINE
    # create async engine
    _ENGINE = create_async_engine(
        uri, json_serializer=SqlaJSON.dumps, json_deserializer=SqlaJSON.loads, **engine
    )

    # bind our db session to engine
    SessionFactory.configure(bind=_ENGINE)

    return _ENGINE


async def run_sync(fn, *args, **kwargs):
    """Convenience for calling .run_sync() on our configured AsyncConnection within a transaction"""
    # async engine
    async with _ENGINE.begin() as conn:
        return await conn.run_sync(fn, *args, **kwargs)


async def drop_all():
    """Drop all tables - must have already called sqla_init()"""
    # drop tables
    await run_sync(Base.metadata.drop_all)


async def create_all():
    """Create all tables - must have already called sqla_init()"""
    await run_sync(Base.metadata.create_all)


######################################################################
# Register SQLA events
######################################################################


@event.listens_for(Session, "before_flush")
def receive_before_flush(session, _flush_context, _instances):
    """Listen for the 'before_flush' event and for the session's `new`,
    `dirty`, and 'deleted' lists call on_{create,update,delete} as
    appropriate.
    """
    # We will keep track of each object we visit
    visited = IdentitySet()

    # ensure no auto-flushing during this work...
    autoflush = session.autoflush
    try:
        session.autoflush = False

        # Any time a validator/trigger is called, `triggered` will be
        # set to True, so we continue the loop until all non-visited
        # objects are considered. We do this to make sure we consider
        # objects that might be created/modified/deleted by a called
        # trigger.
        triggered = True
        while triggered:
            # we assume no triggers will be called, thus breaking out of
            # the loop as soon as all objects are visited.
            triggered = False

            for state, method_name in (
                (session.new, "on_create"),
                (session.dirty, "on_update"),
                (session.deleted, "on_delete"),
            ):
                for obj in state:
                    if obj in visited:
                        continue
                    visited.add(obj)
                    method = getattr(obj, method_name, None)
                    if method:
                        triggered = True
                        await_only(method(session))

    finally:
        session.autoflush = autoflush


@event.listens_for(Session, "after_flush")
def receive_after_flush(session, _flush_context):
    """Listen for the 'after_flush' event, in which objects can still be
    inspected in the 'new', 'dirty' and 'deleted' lists, but changed
    attributes have been persisted (and attributes like serial IDs are
    now available).

    Args:
       session: The active session to be inspected.
       flush_context: The context of the flush that has just executed.

    """
    for state, method_name in (
        (session.new, "on_post_create"),
        (session.dirty, "on_post_update"),
        (session.deleted, "on_post_delete"),
    ):
        for obj in state:
            method = getattr(obj, method_name, None)
            if method:
                await_only(method(session))


@event.listens_for(Session, "after_flush_postexec")
def on_after_flush_postexec(session, _flush_context):
    """Listen for 'after_flush_postexec' and call any registered functions."""
    queue = session.info.setdefault(AFTER_FLUSH_POSTEXEC_QUEUE, [])
    try:
        for func, args, kwargs in queue:
            if inspect.isawaitable(func):
                await_only(func)
            else:  # pragma: no cover
                func(*args, **kwargs)
    finally:
        # delete queue
        del session.info[AFTER_FLUSH_POSTEXEC_QUEUE]


@event.listens_for(Session, "after_rollback")
def on_after_rollback(session):
    """Listen for 'after_rollback' and close any coroutines in our AFTER_FLUSH_POSTEXEC_QUEUE"""
    queue = session.info.setdefault(AFTER_FLUSH_POSTEXEC_QUEUE, [])
    try:
        for func, *_ in queue:
            if inspect.isawaitable(func):
                func.close()
    finally:
        # delete queue
        del session.info[AFTER_FLUSH_POSTEXEC_QUEUE]


@event.listens_for(Mapper, "mapper_configured")
def add_apispec(_mapper, model):
    """Add attributes to models"""
    if not hasattr(model, '__apispec_excludes__'):
        # we only process apispec-aware models
        return

    model.apispec = generate_model_apispec(model)
    model.objects = model.select()
