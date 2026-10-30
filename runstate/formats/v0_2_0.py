"""Format 0.2.0: the format of runstate before reference by name, and its tables."""

from ._layout import DirectoryLayout

SQLITE_SCRIPT = """
CREATE TABLE IF NOT EXISTS log (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    topic      TEXT NOT NULL,
    name       TEXT,
    request_id TEXT,
    body       TEXT NOT NULL,
    created_at REAL NOT NULL
);
-- latest(topic) is WHERE topic=? ORDER BY seq DESC LIMIT 1, called on every
-- Watcher poll (twice, for stopped/terminated, which usually don't exist yet).
-- Without this it's a full table scan per poll; the index makes it a seek.
CREATE INDEX IF NOT EXISTS idx_log_topic_seq ON log (topic, seq);
-- latest(topic, name=) adds AND name=?, which the index above CANNOT serve: it
-- seeks on topic, then walks the partition backwards post-filtering the name,
-- so a rare / early-only / not-yet-emitted name pays the whole partition on
-- every call, forever (#19).
--
-- An ADDITION, never a replacement. Measured on 300k value records: with
-- (topic, name, seq) ALONE, latest(topic) regresses 0.005ms -> 10.5ms (~2000x),
-- because `name` sits between the equality prefix and the sort key so ORDER BY
-- seq falls back to a temp B-tree. Both indexes; both are hot paths. The
-- (topic, seq) guard in tests/test_channel.py pins that.
CREATE INDEX IF NOT EXISTS idx_log_topic_name_seq ON log (topic, name, seq);
"""

PG_STATEMENTS = (
    """
CREATE TABLE IF NOT EXISTS log (
    run_id     text   NOT NULL,
    seq        bigint NOT NULL,
    topic      text   NOT NULL,
    name       text,
    request_id text,
    body       text   NOT NULL,
    created_at double precision NOT NULL,
    PRIMARY KEY (run_id, seq)
)
""",
    "CREATE INDEX IF NOT EXISTS idx_log_run_topic_seq ON log (run_id, topic, seq)",
    # latest(topic, name=) adds AND name=%s, which the index above cannot serve: it
    # seeks on (run_id, topic) and then SORTS the partition to answer ORDER BY seq
    # DESC (#19). Measured on 300k value records: 2504 shared buffers through a
    # top-N heapsort, vs 4 through an Index Only Scan Backward with this index; an
    # absent name costs the same 2504 today and 3 with it.
    #
    # An ADDITION, never a replacement -- `name` sits between the equality prefix
    # and the sort key, so this index cannot answer the name-less latest(topic).
    #
    # Unlike sqlite, Postgres self-migrates: ensure_schema runs at orchestration
    # startup and CREATE INDEX IF NOT EXISTS reaches every existing row. (Not
    # CONCURRENTLY: it cannot run inside ensure_schema's advisory-lock
    # transaction. On a large live deployment, build it out-of-band first -- the
    # IF NOT EXISTS here then finds it and does nothing.)
    "CREATE INDEX IF NOT EXISTS idx_log_run_topic_name_seq"
    " ON log (run_id, topic, name, seq)",
)

LAYOUT = DirectoryLayout(
    version="0.2.0", sqlite_script=SQLITE_SCRIPT, pg_statements=PG_STATEMENTS
)
