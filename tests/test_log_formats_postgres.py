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
        with psycopg.connect(dsn, autocommit=True) as c:
            c.execute("DELETE FROM public.log WHERE run_id = %s", [rid])


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
