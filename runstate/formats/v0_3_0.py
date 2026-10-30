"""Format 0.3.0: reference by name (docs/specs/reference-by-name.md). Its layout
is 0.2.0's, one directory and one schema further on. Its SQLite table is 0.2.0's;
its Postgres table adds the session stamp.

The stamp (``writer_key``, ``writer_server``; channel-postgres.md, "Liveness") is
the writing connection's advisory-lock key and its server's identity, which the
session probe reads. Both are nullable: a row the migration store copies carries
none, and the probe abstains on it. On this branch the live channel writes the
stamp under 0.3.0's address until the flip, which moves this table to 0.4.0's
layout and gives 0.3.0 back 0.2.0's (commits-and-lineage.md §11)."""

from . import v0_2_0
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
    version="0.3.0",
    sqlite_script=v0_2_0.SQLITE_SCRIPT,
    pg_statements=(PG_TABLE, v0_2_0.PG_TOPIC_INDEX, v0_2_0.PG_NAME_INDEX),
)
