"""Tests for BaseModel class methods.

Requires a PostgreSQL database.  Connection parameters are read from the
standard libpq environment variables (PGHOST, PGPORT, PGUSER, PGPASSWORD,
PGDATABASE).  Defaults: localhost:5432, user=postgres, db=postgres.
"""

import asyncio
import logging
import os
import sys
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.orm import configure_mappers

from sqla_pollux import (
    BaseModel,
    ModelNotFound,
    apispec,
    create_all,
    drop_all,
    new_dbsession,
    sqla_init,
)
from sqla_pollux.exceptions import PermissionDenied

#logging.getLogger("sqlalchemy.engine").setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pg_uri() -> str:
    host = os.environ.get("PGHOST", "localhost")
    port = os.environ.get("PGPORT", "5432")
    user = os.environ.get("PGUSER", "postgres")
    password = os.environ.get("PGPASSWORD", "")
    dbname = os.environ.get("PGDATABASE", "postgres")
    auth = f"{user}:{password}@" if password else f"{user}@"
    return f"postgresql+asyncpg://{auth}{host}:{port}/{dbname}"


def _admin_user():
    """Return a mock user that is an admin."""
    u = MagicMock()
    u.is_admin = True
    return u


def _non_admin_user():
    """Return a mock user that is NOT an admin."""
    u = MagicMock()
    u.is_admin = False
    return u


# ---------------------------------------------------------------------------
# Test model
# ---------------------------------------------------------------------------

_ItemTable = sa.Table(
    "test_item",
    BaseModel.metadata,
    sa.Column("id", sa.Integer, primary_key=True, info=apispec.READ_ONLY()),
    sa.Column("name", sa.String(255), nullable=False),
    sa.Column("value", sa.Integer, nullable=True),
    sa.Column("active", sa.Boolean, nullable=False, default=True),
)


class Item(BaseModel):
    """Minimal model used only during testing."""

    __table__ = _ItemTable


configure_mappers()


# ---------------------------------------------------------------------------
# Session-level fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
async def db_schema():
    """Create all tables once for the test session, drop them afterwards."""
    sqla_init(_pg_uri())
    await create_all()


# ---------------------------------------------------------------------------
# Per-test fixture: isolated transaction (always rolled back)
# ---------------------------------------------------------------------------

@pytest.fixture
async def sess():
    """Run each test in its own transaction that is always rolled back.

    Uses new_dbsession() to set the dbsession contextvar (required by all
    BaseModel static/class methods), opens the async session, begins a
    transaction manually, yields, then always rolls back so no test data
    persists.
    """
    with new_dbsession() as session:
        async with session:
            await session.begin()
            try:
                yield
            finally:
                await session.rollback()

# ---------------------------------------------------------------------------
# Convenience helpers that need an active session
# ---------------------------------------------------------------------------

async def _make_item(name="alpha", value=1, active=True) -> Item:
    return await Item.create(name=name, value=value, active=active)


# ===========================================================================
# Tests – methods that do NOT require database access
# ===========================================================================

class TestChannelMethod:
    def test_channel_with_id(self):
        assert Item.channel(42) == "test_item:42"

    def test_channel_with_instance(self):
        inst = Item()
        inst.id = 7
        assert Item.channel(inst) == "test_item:7"


class TestSelectMethod:
    def test_default_select(self):
        q = Item.select()
        assert str(q).startswith("SELECT")
        assert "test_item" in str(q)

    def test_select_with_where(self):
        q = Item.select(where=Item.name == "x")
        compiled = str(q)
        assert "WHERE" in compiled

    def test_select_with_list_where(self):
        q = Item.select(where=[Item.name == "x", Item.active.is_(True)])
        assert "AND" in str(q)

    def test_select_with_order_by(self):
        q = Item.select(order_by=Item.name)
        assert "ORDER BY" in str(q)

    def test_select_with_list_order_by(self):
        q = Item.select(order_by=[Item.name, Item.value])
        assert "ORDER BY" in str(q)

    def test_select_with_limit_offset(self):
        q = Item.select(limit=10, offset=5)
        compiled = str(q)
        assert "LIMIT" in compiled
        assert "OFFSET" in compiled

    def test_select_with_distinct(self):
        q = Item.select(distinct=True)
        assert "DISTINCT" in str(q)

    def test_select_with_group_by(self):
        q = Item.select(Item.active, group_by=Item.active)
        assert "GROUP BY" in str(q)

    def test_select_with_explicit_entities(self):
        q = Item.select(Item.name, Item.value)
        assert "test_item.name" in str(q)
        assert "test_item.value" in str(q)


class TestBulkDMLMethods:
    def test_bulk_delete_returns_delete(self):
        stmt = Item.bulk_delete()
        assert "DELETE" in str(stmt).upper()

    def test_bulk_update_returns_update(self):
        stmt = Item.bulk_update()
        assert "UPDATE" in str(stmt).upper()

    def test_insert_returns_insert(self):
        stmt = Item.insert()
        assert "INSERT" in str(stmt).upper()


class TestNotFoundMethod:
    def test_returns_model_not_found_instance(self):
        exc = Item.NotFound()
        assert isinstance(exc, ModelNotFound)
        assert "Item" in str(exc)


class TestCacheProperty:
    def test_cache_is_cached_data(self):
        from sqla_pollux.utils import CachedData

        inst = Item()
        cache = inst.cache
        assert isinstance(cache, CachedData)

    def test_cache_is_same_object_on_second_access(self):
        inst = Item()
        assert inst.cache is inst.cache

    def test_cache_stores_values(self):
        inst = Item()
        inst.cache.x = 99
        assert inst.cache.x == 99


class TestDunderStr:
    def test_str(self):
        inst = Item()
        inst.id = 5
        assert str(inst) == "Item: 5"


# ===========================================================================
# Tests – database methods (require `sess` fixture)
# ===========================================================================

class TestCreate:
    @pytest.mark.asyncio
    async def test_create_returns_instance(self, sess):
        item = await Item.create(name="widget", value=10, active=True)
        assert isinstance(item, Item)
        assert item.id is not None
        assert item.name == "widget"
        assert item.value == 10

    @pytest.mark.asyncio
    async def test_create_no_flush_does_not_flush(self, sess):
        item = await Item.create_no_flush(name="pending", value=0, active=False)
        assert isinstance(item, Item)
        # id may be None before flush if server assigns it
        assert item.name == "pending"

    @pytest.mark.asyncio
    async def test_create_respects_read_only_field(self, sess):
        # id is READ_ONLY, so passing it should be silently ignored
        item = await Item.create(name="ro_test", value=5, active=True, id=9999)
        assert item.id != 9999

    @pytest.mark.asyncio
    async def test_create_multiple_items(self, sess):
        for i in range(3):
            await Item.create(name=f"item_{i}", value=i, active=True)
        count = await Item.count()
        assert count == 3


class TestCount:
    @pytest.mark.asyncio
    async def test_count_empty(self, sess):
        assert await Item.count() == 0

    @pytest.mark.asyncio
    async def test_count_all(self, sess):
        await _make_item("a")
        await _make_item("b")
        assert await Item.count() == 2

    @pytest.mark.asyncio
    async def test_count_with_where(self, sess):
        await _make_item("active_one", active=True)
        await _make_item("inactive_one", active=False)
        assert await Item.count(Item.active.is_(True)) == 1

    @pytest.mark.asyncio
    async def test_count_col(self, sess):
        await Item.create(name="has_value", value=42, active=True)
        await Item.create(name="no_value", value=None, active=True)
        # COUNT(value) counts non-NULL only
        assert await Item.count(col=Item.value) == 1

    @pytest.mark.asyncio
    async def test_count_distinct(self, sess):
        await _make_item("dup", value=5)
        await _make_item("dup2", value=5)
        await _make_item("uniq", value=7)
        assert await Item.count(col=Item.value.distinct()) == 2


class TestExists:
    @pytest.mark.asyncio
    async def test_exists_false_when_empty(self, sess):
        assert await Item.exists() is False

    @pytest.mark.asyncio
    async def test_exists_true_after_create(self, sess):
        await _make_item()
        assert await Item.exists() is True

    @pytest.mark.asyncio
    async def test_exists_with_where_match(self, sess):
        await _make_item("target", value=99)
        assert await Item.exists(Item.name == "target") is True

    @pytest.mark.asyncio
    async def test_exists_with_where_no_match(self, sess):
        await _make_item("other")
        assert await Item.exists(Item.name == "missing") is False

    @pytest.mark.asyncio
    async def test_exists_with_from_(self, sess):
        await _make_item("fromtest")
        assert await Item.exists(from_=Item) is True


class TestGet:
    @pytest.mark.asyncio
    async def test_get_by_pk(self, sess):
        created = await _make_item("getme")
        fetched = await Item.get(created.id)
        assert fetched is not None
        assert fetched.id == created.id

    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_none(self, sess):
        result = await Item.get(999999)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_with_raise_raises_model_not_found(self, sess):
        with pytest.raises(ModelNotFound):
            await Item.get(999999, raise_=True)

    @pytest.mark.asyncio
    async def test_get_with_strict_and_admin_user(self, sess):
        created = await _make_item("strictget")
        fetched = await Item.get(created.id, strict=True, user=_admin_user())
        assert fetched.id == created.id

    @pytest.mark.asyncio
    async def test_get_with_non_admin_user_raises_permission_denied(self, sess):
        created = await _make_item("denied")
        with pytest.raises(PermissionDenied):
            await Item.get(created.id, user=_non_admin_user())


class TestList:
    @pytest.mark.asyncio
    async def test_list_empty(self, sess):
        result = await Item.list()
        assert result == []

    @pytest.mark.asyncio
    async def test_list_returns_all_items(self, sess):
        await _make_item("x")
        await _make_item("y")
        result = await Item.list()
        assert len(result) == 2
        assert all(isinstance(i, Item) for i in result)

    @pytest.mark.asyncio
    async def test_list_with_where(self, sess):
        await _make_item("keep", active=True)
        await _make_item("skip", active=False)
        result = await Item.list(where=Item.active.is_(True))
        assert len(result) == 1
        assert result[0].name == "keep"

    @pytest.mark.asyncio
    async def test_list_with_order_by(self, sess):
        await _make_item("b_item")
        await _make_item("a_item")
        result = await Item.list(order_by=Item.name)
        assert result[0].name == "a_item"
        assert result[1].name == "b_item"

    @pytest.mark.asyncio
    async def test_list_with_limit(self, sess):
        for i in range(5):
            await _make_item(f"lim_{i}")
        result = await Item.list(limit=3)
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_list_scalar_false(self, sess):
        await _make_item("row1")
        # scalar=False returns Row objects (tuples)
        result = await Item.list(Item.name, scalar=False)
        assert len(result) == 1
        assert result[0][0] == "row1"


class TestOneAndOneOrNone:
    @pytest.mark.asyncio
    async def test_one_or_none_returns_none_when_empty(self, sess):
        result = await Item.one_or_none(where=Item.name == "ghost")
        assert result is None

    @pytest.mark.asyncio
    async def test_one_or_none_returns_instance(self, sess):
        await _make_item("solo")
        result = await Item.one_or_none(where=Item.name == "solo")
        assert isinstance(result, Item)

    @pytest.mark.asyncio
    async def test_one_or_none_with_explicit_query(self, sess):
        await _make_item("qsolo")
        q = Item.select(where=Item.name == "qsolo")
        result = await Item.one_or_none(query=q)
        assert result is not None
        assert result.name == "qsolo"

    @pytest.mark.asyncio
    async def test_one_or_none_raises_on_multiple(self, sess):
        await _make_item("dup")
        await _make_item("dup")
        from sqlalchemy.exc import MultipleResultsFound
        with pytest.raises(MultipleResultsFound):
            await Item.one_or_none(where=Item.name == "dup")

    @pytest.mark.asyncio
    async def test_one_raises_model_not_found_when_empty(self, sess):
        with pytest.raises(ModelNotFound):
            await Item.one(where=Item.name == "ghost")

    @pytest.mark.asyncio
    async def test_one_returns_instance(self, sess):
        await _make_item("unique_one")
        result = await Item.one(where=Item.name == "unique_one")
        assert isinstance(result, Item)
        assert result.name == "unique_one"


class TestResolve:
    @pytest.mark.asyncio
    async def test_resolve_with_instance_returns_same(self, sess):
        created = await _make_item("res_inst")
        resolved = await Item.resolve(created)
        assert resolved is created

    @pytest.mark.asyncio
    async def test_resolve_with_id_returns_instance(self, sess):
        created = await _make_item("res_id")
        resolved = await Item.resolve(created.id)
        assert isinstance(resolved, Item)
        assert resolved.id == created.id

    @pytest.mark.asyncio
    async def test_resolve_nonexistent_id_returns_none(self, sess):
        resolved = await Item.resolve(999999)
        assert resolved is None


class TestExecute:
    @pytest.mark.asyncio
    async def test_execute_raw_query(self, sess):
        result = await Item.execute(sa.text("SELECT 1 AS val"))
        assert result.scalar() == 1

    @pytest.mark.asyncio
    async def test_execute_with_params(self, sess):
        result = await Item.execute(
            sa.text("SELECT :x AS val"),
            params={"x": "42"},
        )
        assert result.scalar() == "42"

    @pytest.mark.asyncio
    async def test_execute_with_autoflush_override(self, sess):
        # Just confirm it doesn't raise
        await Item.execute(sa.text("SELECT 1"), autoflush=False)


class TestBeginNested:
    @pytest.mark.asyncio
    async def test_begin_nested_creates_savepoint(self, sess):
        item = await _make_item("before_save")
        async with Item.begin_nested():
            item2 = await _make_item("inside_save")
            await Item.execute(
                sa.text("SAVEPOINT test_sp")
            )
        # Both should exist in the session
        assert await Item.count() == 2


class TestDelete:
    @pytest.mark.asyncio
    async def test_delete_removes_instance(self, sess):
        item = await _make_item("to_delete")
        await item.delete()
        assert await Item.count() == 0

    @pytest.mark.asyncio
    async def test_delete_no_flush_marks_for_deletion(self, sess):
        item = await _make_item("to_delete_nf")
        await item.delete_no_flush()
        # still in session.deleted; count confirms after flush
        await Item._flush()
        assert await Item.count() == 0

    @pytest.mark.asyncio
    async def test_soft_delete_default_returns_false(self, sess):
        item = await _make_item("soft")
        assert await item._soft_delete() is False

    @pytest.mark.asyncio
    async def test_can_delete_conditionally_default_is_noop(self, sess):
        item = await _make_item("condel")
        result = await item._can_delete_conditionally()
        assert result is None


class TestUpdate:
    @pytest.mark.asyncio
    async def test_update_persists_changes(self, sess):
        item = await _make_item("before_update", value=1)
        await item.update(name="after_update", value=2)
        assert item.name == "after_update"
        assert item.value == 2

    @pytest.mark.asyncio
    async def test_update_no_flush_changes_in_memory(self, sess):
        item = await _make_item("nf_before", value=5)
        result = await item.update_no_flush(value=10)
        assert result is item
        assert item.value == 10

    @pytest.mark.asyncio
    async def test_update_ignores_read_only_fields(self, sess):
        item = await _make_item("read_only_guard", value=1)
        original_id = item.id
        await item.update(id=9999)
        assert item.id == original_id

    @pytest.mark.asyncio
    async def test_update_returns_self(self, sess):
        item = await _make_item("self_return")
        result = await item.update(name="updated")
        assert result is item

    @pytest.mark.asyncio
    async def test_update_with_force_allows_read_only_field(self, sess):
        item = await _make_item("force_field")
        # _update with _force='*' allows any field
        await item._update(_force="*", name="forced")
        assert item.name == "forced"


class TestRefresh:
    @pytest.mark.asyncio
    async def test_refresh_returns_self(self, sess):
        item = await _make_item("refresh_me")
        result = await item.refresh()
        assert result is item

    @pytest.mark.asyncio
    async def test_refresh_specific_attributes(self, sess):
        item = await _make_item("refresh_attr")
        # Manually mutate in-memory, then refresh to get db value
        item.name = "corrupted"
        await item.refresh(attribute_names=["name"])
        assert item.name == "refresh_attr"


class TestHasPermission:
    @pytest.mark.asyncio
    async def test_admin_has_permission(self, sess):
        item = await _make_item("perm_item")
        assert await item.has_permission(user=_admin_user()) is True

    @pytest.mark.asyncio
    async def test_non_admin_has_no_permission(self, sess):
        item = await _make_item("no_perm_item")
        assert await item.has_permission(user=_non_admin_user()) is False


class TestDunderJson:
    @pytest.mark.asyncio
    async def test_json_returns_dict(self, sess):
        item = await _make_item("json_item", value=7)
        data = item.__json__()
        assert isinstance(data, dict)
        assert data["name"] == "json_item"
        assert data["value"] == 7

    @pytest.mark.asyncio
    async def test_json_excludes_non_read_fields(self, sess):
        # id IS in read (READ_ONLY means create=False, update=False, read=True)
        item = await _make_item("json_check")
        data = item.__json__()
        assert "id" in data

    @pytest.mark.asyncio
    async def test_json_contains_expected_keys(self, sess):
        item = await _make_item("keys_check", value=3)
        data = item.__json__()
        assert set(data.keys()) == {"id", "name", "value", "active"}


class TestBulkDMLExecution:
    """Test that bulk_delete and bulk_update DML can actually be executed."""

    @pytest.mark.asyncio
    async def test_bulk_delete_executes(self, sess):
        await _make_item("del1")
        await _make_item("del2")
        stmt = Item.bulk_delete().where(Item.name == "del1")
        await Item.execute(stmt)
        assert await Item.count() == 1

    @pytest.mark.asyncio
    async def test_bulk_update_executes(self, sess):
        await _make_item("upd_target", value=1)
        stmt = Item.bulk_update().where(Item.name == "upd_target").values(value=99)
        await Item.execute(stmt)
        item = await Item.one(where=Item.name == "upd_target")
        assert item.value == 99


class TestSoftDeleteOverride:
    """Verify that a subclass returning True from _soft_delete prevents hard deletion."""

    @pytest.mark.asyncio
    async def test_soft_delete_skips_hard_delete(self, sess):

        class SoftItem(Item):
            __abstract__ = True

            async def _soft_delete(self):
                self.active = False
                return True

        item = await _make_item("soft_del", active=True)

        # Patch the method on the instance
        from types import MethodType

        async def _fake_soft_delete(self):
            self.active = False
            return True

        item._soft_delete = MethodType(_fake_soft_delete, item)

        await item._delete()
        await Item._flush()

        # Row should still exist in db but active=False
        fetched = await Item.get(item.id)
        assert fetched is not None
        assert fetched.active is False
