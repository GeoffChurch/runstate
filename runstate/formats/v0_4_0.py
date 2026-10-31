"""Format 0.4.0: commits and lineage (docs/specs/commits-and-lineage.md). Its layout
is 0.3.0's, one directory and one schema further on. Its SQLite table is 0.3.0's;
its Postgres table adds the session stamp.

The stamp (``writer_key``, ``writer_server``; channel-postgres.md, "Liveness") is
the writing connection's advisory-lock key and its server's identity, which the
session probe reads. Both are nullable: a row the migration store copies carries
none, and the probe abstains on it."""

from . import v0_3_0
from ._layout import DirectoryLayout

PG_TABLE = """
CREATE TABLE IF NOT EXISTS log (
    run_id        text   NOT NULL,
    seq           bigint NOT NULL,
    topic         text   NOT NULL,
    name          text,
    request_id    text,
    body          text   NOT NULL,
    created_at    double precision NOT NULL,
    writer_key    bigint,
    writer_server text,
    PRIMARY KEY (run_id, seq)
)
"""

LAYOUT = DirectoryLayout(
    version="0.4.0",
    sqlite_ddl=v0_3_0.LAYOUT.sqlite_ddl,
    pg_ddl=(PG_TABLE, *v0_3_0.LAYOUT.pg_ddl[1:]),
)
