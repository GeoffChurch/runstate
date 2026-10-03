"""log-formats.md §4 on postgres: a schema per format."""

import uuid

import pytest

from runstate import (
    LOG_FORMAT,
    LogFormatMismatch,
    LogFormatMissing,
    RunNotFound,
    attach_channel,
    create_channel,
)
from runstate.formats import FORMATS, DirectoryLayout
from runstate.migrations import migrate


@pytest.fixture
def dsn(pg_ready):
    return pg_ready


def _rid():
    return f"fmt-{uuid.uuid4().hex}"


def test_create_writes_into_the_format_schema(dsn):
    import psycopg

    rid = _rid()
    with create_channel(rid, root=dsn, backend="postgres") as ch:
        ch.send({}, topic="value", name="n")
    schema = FORMATS[LOG_FORMAT].pg_schema()
    with psycopg.connect(dsn) as c:
        n = c.execute(
            f"SELECT count(*) FROM {schema}.log WHERE run_id = %s", [rid]
        ).fetchone()
    assert n == (1,)


def test_attach_missing_run_is_not_found(dsn):
    with pytest.raises(RunNotFound):
        attach_channel(_rid(), root=dsn, backend="postgres")


def test_older_schema_holding_the_run_says_migrate(dsn, monkeypatch):
    import psycopg

    from runstate import formats

    monkeypatch.setitem(formats.FORMATS, "0.1.0", DirectoryLayout("0.1.0"))
    rid = _rid()
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("CREATE SCHEMA IF NOT EXISTS runstate_v0_1_0")
        c.execute(
            "CREATE TABLE IF NOT EXISTS runstate_v0_1_0.log (LIKE "
            f"{FORMATS[LOG_FORMAT].pg_schema()}.log INCLUDING ALL)"
        )
        c.execute(
            "INSERT INTO runstate_v0_1_0.log VALUES (%s, 1, 'value', 'n', NULL, '{}', 0)",
            [rid],
        )
    try:
        for locate in (attach_channel, create_channel):
            with pytest.raises(LogFormatMismatch, match="runstate migrate"):
                locate(rid, root=dsn, backend="postgres")
    finally:
        with psycopg.connect(dsn, autocommit=True) as c:
            c.execute("DROP SCHEMA runstate_v0_1_0 CASCADE")


def test_legacy_public_log_holding_the_run_is_missing(dsn):
    import psycopg

    rid = _rid()
    with psycopg.connect(dsn, autocommit=True) as c:
        existed = c.execute("SELECT to_regclass('public.log')").fetchone() != (None,)
        c.execute(
            "CREATE TABLE IF NOT EXISTS public.log (LIKE "
            f"{FORMATS[LOG_FORMAT].pg_schema()}.log INCLUDING ALL)"
        )
        c.execute(
            "INSERT INTO public.log VALUES (%s, 1, 'value', 'n', NULL, '{}', 0)", [rid]
        )
    try:
        with pytest.raises(LogFormatMissing, match="ALTER TABLE"):
            attach_channel(rid, root=dsn, backend="postgres")
    finally:
        # leave the database as found: a leftover public.log is the table an
        # unqualified `log` resolves to, which would let other tests pass vacuously
        with psycopg.connect(dsn, autocommit=True) as c:
            if existed:
                c.execute("DELETE FROM public.log WHERE run_id = %s", [rid])
            else:
                c.execute("DROP TABLE public.log")


def test_a_newer_schema_refuses(dsn):
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("CREATE SCHEMA IF NOT EXISTS runstate_v99_0_0")
    try:
        with pytest.raises(LogFormatMismatch, match="upgrade runstate"):
            create_channel(_rid(), root=dsn, backend="postgres")
    finally:
        with psycopg.connect(dsn, autocommit=True) as c:
            c.execute("DROP SCHEMA runstate_v99_0_0 CASCADE")


# ----- onboarding: the LogFormatMissing instructions, run as written -------------


@pytest.fixture
def scratch_db(dsn):
    """A database of its own, dropped afterwards. Onboarding moves a whole table
    into schema ``runstate_v0_2_0``, which the shared test database already has."""
    import psycopg
    from psycopg.conninfo import make_conninfo

    name = f"rs_scratch_{uuid.uuid4().hex}"
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute(f"CREATE DATABASE {name}")
    try:
        yield make_conninfo(dsn, dbname=name)
    finally:
        with psycopg.connect(dsn, autocommit=True) as c:
            c.execute(f"DROP DATABASE {name} WITH (FORCE)")


def _master_log(dsn, run_id, n):
    """A log as master wrote it: its DDL and appends, unqualified, so they land
    in the schema the connection's search path resolves (the channel's
    constants are master's, byte for byte)."""
    import psycopg

    from runstate.channel.postgres import (
        _CREATE_INDEX,
        _CREATE_NAME_INDEX,
        _CREATE_TABLE,
        _UNCONDITIONAL,
    )

    with psycopg.connect(dsn, autocommit=True) as c:
        for ddl in (_CREATE_TABLE, _CREATE_INDEX, _CREATE_NAME_INDEX):
            c.execute(ddl)
        for i in range(n):
            c.execute(
                _UNCONDITIONAL,
                {
                    "run": run_id,
                    "topic": "value",
                    "name": "n",
                    "rid": None,
                    "body": "{}",
                },
            )


def _onboarding_sql(dsn, run_id):
    """The SQL the LogFormatMissing message gives, verbatim."""
    with pytest.raises(LogFormatMissing) as exc:
        attach_channel(run_id, root=dsn, backend="postgres")
    lines = [ln for ln in str(exc.value).splitlines() if ln.startswith("  ")]
    assert lines
    return "\n".join(lines)


def _with_search_path(dsn, schema):
    """The DSN, its connections resolving unqualified names in ``schema`` first,
    as a consumer's DSN may (``options=-csearch_path=...``)."""
    import psycopg
    from psycopg.conninfo import make_conninfo

    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute(f"CREATE SCHEMA {schema}")
    return make_conninfo(dsn, options=f"-csearch_path={schema}")


@pytest.mark.parametrize("schema", ["public", "myapp"])
def test_onboarding_leaves_a_tombstone_an_old_writer_cannot_use(scratch_db, schema):
    """C1 on postgres: master's ensure_schema recreates an absent ``log``, so
    moving the table, and nothing more, let a writer from before versioned
    addresses start every run over in a fresh one. The instructions leave an
    empty ``log`` with the old columns, whose trigger refuses every insert, in
    the schema the table was found in. And HEAD's checks answer as they would
    without it, before ``migrate`` and after."""
    import psycopg

    from runstate.channel.postgres import ensure_schema
    from runstate.migrations.stores import PostgresStore

    dsn = scratch_db if schema == "public" else _with_search_path(scratch_db, schema)
    _master_log(dsn, "r1", 3)
    ensure_schema(dsn)
    onboard = _onboarding_sql(dsn, "r1").strip()
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute(onboard)

    _master_log(dsn, "r1", 0)  # master's ensure_schema: a no-op on the tombstone
    with psycopg.connect(dsn, autocommit=True) as c:
        with pytest.raises(psycopg.errors.RaiseException, match="upgrade runstate"):
            _master_log(dsn, "r1", 1)  # master's create: its append is refused
        # master's attach reads MAX(seq) of the run, and 0 is its RunNotFound
        assert c.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM log WHERE run_id = 'r1'"
        ).fetchone() == (0,)
        assert c.execute(f"SELECT count(*) FROM {schema}.log").fetchone() == (0,)

    for locate in (attach_channel, create_channel):  # an older format holds it
        with pytest.raises(LogFormatMismatch, match="runstate migrate"):
            locate("r1", root=dsn, backend="postgres")
    assert migrate(PostgresStore(dsn), None, to=LOG_FORMAT) == ["r1"]
    with attach_channel("r1", root=dsn, backend="postgres") as ch:
        assert ch.last_seq() == 3
    with pytest.raises(RunNotFound):
        attach_channel("r2", root=dsn, backend="postgres")
    assert onboard.startswith("BEGIN;") and onboard.endswith("COMMIT;")  # whole or not


def test_a_legacy_log_on_the_dsns_own_search_path_is_missing(scratch_db):
    """I3: master's statements were unqualified, so a DSN carrying its own
    search path put master's log in that schema, not in ``public``. The legacy
    check looks where master did, under the connection's own search path: the
    run is missing (never not found), the instructions name the schema, and
    create births nothing over the old run."""
    import psycopg

    from runstate.channel.postgres import ensure_schema

    dsn = _with_search_path(scratch_db, "myapp")
    _master_log(dsn, "r1", 3)
    ensure_schema(dsn)
    for locate in (attach_channel, create_channel):
        with pytest.raises(LogFormatMissing, match='schema "myapp"') as exc:
            locate("r1", root=dsn, backend="postgres")
        assert exc.value.where == "myapp.log"
    with psycopg.connect(dsn) as c:
        assert c.execute(
            f"SELECT count(*) FROM {FORMATS[LOG_FORMAT].pg_schema()}.log"
        ).fetchone() == (0,)


def test_an_unrelated_log_table_is_not_a_legacy_log(scratch_db):
    """M4: another application's ``log``, with no ``run_id`` column, is none of
    runstate's. The legacy check passes over it, where it raised UndefinedColumn
    on every open."""
    import psycopg

    from runstate.channel.postgres import ensure_schema

    with psycopg.connect(scratch_db, autocommit=True) as c:
        c.execute("CREATE TABLE public.log (id int, message text)")
        c.execute("INSERT INTO public.log VALUES (1, 'not ours')")
    ensure_schema(scratch_db)
    with pytest.raises(RunNotFound):
        attach_channel("r1", root=scratch_db, backend="postgres")
    with create_channel("r1", root=scratch_db, backend="postgres") as ch:
        ch.send({}, topic="value", name="n")
    with attach_channel("r1", root=scratch_db, backend="postgres") as ch:
        assert ch.last_seq() == 1
