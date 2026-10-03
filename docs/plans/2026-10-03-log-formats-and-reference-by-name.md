# Log formats and reference by name — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every log a format named by the release that introduced it and placed outermost in its address
(stage 1), then make records name what they answer instead of being related by log position, as log format 0.3.0
(stage 2).

**Architecture:**
- **Stage 1** adds a format registry (`runstate/formats/`) and moves the format checks into the two locators.
  Format 0.2.0 lives at `<root>/v0.2.0/<rid>.db` on SQLite and in schema `runstate_v0_2_0` on Postgres.
- **Stage 1** also adds a migration engine (`runstate/migrations/`) that copies a run into the next format's
  address and seals the old log, behind a `runstate migrate` command.
- **Stage 2** ports the measured spike (`spike/reference-by-name`, commit `374c1a2`), renames `honoured` to
  `honored`, adds the Watcher's incremental pending-stops form, and turns the spike's backfill into the
  `0.2.0 → 0.3.0` migration step. The final task flips `LOG_FORMAT` to `0.3.0`.

**Tech Stack:** Python 3.12, sqlite3, psycopg 3 (the optional `[postgres]` extra), pytest, mypy `--strict`,
black. runstate has no runtime dependencies, and this plan adds none.

**Specs (read both before any task):**
- `docs/specs/log-formats.md`
- `docs/specs/reference-by-name.md`

Background: `docs/backlog/identity-in-records.md` (layer 1). Spike evidence:
`docs/review-2026-10-02-reference-by-name.md` (untracked; present in the main checkout only).

## Global Constraints

- **Environment.** Develop in the repo-local env. Run tests with `./.conda/bin/python -m pytest`, or
  `conda activate ./.conda`. In a git worktree `.conda` is absent; use `python -m pytest` from the worktree
  root, which imports the worktree's code because `pyproject.toml` sets pytest `pythonpath = ["."]`.
- **Gates:** `black --check runstate/ tests/`, `mypy --strict runstate`, and `pytest tests/` must pass at every
  commit. The pre-commit hook runs all three. The Postgres tests skip without `RUNSTATE_TEST_PG_DSN`; to run
  them, start a throwaway server as `CLAUDE.md` §"Run the Postgres suite locally" describes.
- **Edits** may use any tool. Applying a committed diff with `git apply --3way`, and checking out a file
  from a commit with `git checkout <commit> -- <path>`, are the intended ways to port spike code.
- **No new runtime dependencies.** Format versions are plain `X.Y.Z`, parsed by `formats.parse`. Do not
  use `packaging`.
- **Format names:** directory `v<X.Y.Z>`, schema `runstate_v<X>_<Y>_<Z>`, constant `runstate.LOG_FORMAT`.
- **Errors:** `LogFormatError` is the base; `LogFormatMismatch` covers a newer or older format, and
  `LogFormatMissing` the legacy unversioned address. They are never converted into `RunNotFound`.
- **Spelling is American:** the wire field is `honored`, never `honoured`.
- **Schema rules.** Present-nullable fields: a field is always present, `null` or `[]` when not applicable,
  never omitted. `additionalProperties: false` everywhere.
- **Format changes happen once.** `LOG_FORMAT` changes from `"0.2.0"` to `"0.3.0"` only in Task 13, the
  same commit that bumps `pyproject.toml`'s version to `0.3.0.dev0`. Between Task 7 and Task 13 the branch
  writes 0.3.0-shaped records under the 0.2.0 address. That is acceptable only because the branch merges as a
  whole: no consumer may pin an intermediate commit.
- **Commits** end with the attribution lines the session provides. Push the branch after each task (the
  repo is private).

## Review Focus

The five conditions a user is most likely to hit that no spec section pins. Each has a test added to the
task that owns its code.

1. **A partly onboarded SQLite root**, holding `v0.2.0/` beside leftover legacy `<rid>.db` files. An
   onboarded run must open; a not-yet-moved run must raise `LogFormatMissing`, never `RunNotFound`.
   (Task 2.)
2. **Run ids with URI- or path-special characters** (`?`, `#`, `%`, spaces) under the versioned path. They
   must round-trip through create, attach and migrate. (Tasks 2 and 5.)
3. **A WAL-mode log with un-checkpointed writes, migrated.** Every record written before the seal must
   appear in the new log. (Tasks 4 and 5.)
4. **Concurrent births into a fresh root**, racing on `mkdir v0.2.0`. Every creator must succeed and share
   one log. (Task 2.)
5. **Request ids that collide with the backfill's minted names**, such as an existing stop named `stop@7`
   beside a nameless stop at seq 7, or a subscription id that already ends in `#1`. Migrated names must
   stay unique. (Task 12.)

---

## File structure

**Stage 1, created:**

| path | responsibility |
|---|---|
| `runstate/formats/__init__.py` | `LOG_FORMAT`; the `FORMATS` registry; `parse`; `older_than`; `newer_in`; the three errors; onboarding text |
| `runstate/formats/_layout.py` | the `Layout` Protocol and `DirectoryLayout` |
| `runstate/formats/v0_2_0.py` | `LAYOUT = DirectoryLayout("0.2.0")` |
| `runstate/migrations/__init__.py` | `Row`, `Step`, `STEPS`, `chain`, `MigrationError`, `migrate` |
| `runstate/migrations/stores.py` | `SqliteStore` and `PostgresStore`: read rows, check liveness, seal, publish |
| `runstate/migrations/seal.py` | `seal_sqlite(path)` and `seal_postgres(conn, schema, run_id)` |
| `runstate/cli.py` | `runstate migrate ...` |
| `tests/test_formats.py`, `tests/test_log_formats_sqlite.py`, `tests/test_log_formats_postgres.py`, `tests/test_seal.py`, `tests/test_migrate.py` | |

**Stage 1, modified:** `runstate/channel/__init__.py`, `runstate/channel/postgres.py`, `runstate/__init__.py`,
`pyproject.toml` (`[project.scripts]`), `tests/test_channel_locators.py`, `tests/test_public_api.py`,
`docs/api.md`, and the docs in Task 6.

**Stage 2, created:**

| path | responsibility |
|---|---|
| `runstate/formats/v0_3_0.py` | `LAYOUT = DirectoryLayout("0.3.0")` |
| `runstate/migrations/v0_2_0_to_v0_3_0.py` | the step |
| `tests/test_reference_by_name.py` | from the spike |
| `tests/test_pending_stops.py` | |
| `tests/test_order_independence.py` | |
| `tests/test_migration_v0_2_0_to_v0_3_0.py` | |

**Stage 2, modified:**
- protocol: `protocol/lifecycle-v0.4.schema.json` → `lifecycle-v0.5`, `protocol/subscription-v0.2.schema.json`
  → `subscription-v0.3`;
- library: `runstate/vocabulary/payloads.py`, `runstate/vocabulary/schedule.py`, `runstate/worker.py`,
  `runstate/observables.py`, `runstate/watcher.py`, `runstate/__init__.py`;
- the failing tests (Task 10), `pyproject.toml` (version), and the docs in Task 13.

---

# Stage 1 — log formats

### Task 1: The format registry

**Files:**
- Create: `runstate/formats/__init__.py`, `runstate/formats/_layout.py`, `runstate/formats/v0_2_0.py`
- Test: `tests/test_formats.py`

**Interfaces:**
- Produces:
  - `runstate.formats.LOG_FORMAT: str` (`"0.2.0"`) and `FORMATS: dict[str, Layout]`;
  - `parse(v: str) -> tuple[int, int, int]`;
  - `older_than(v: str) -> list[tuple[str, Layout]]`, newest first;
  - `newer_in(names: Iterable[str], *, prefix: str, sep: str, than: str) -> list[str]`, sorted ascending;
  - `Layout`, with `version: str`, `sqlite_path(root: Path, run_id: str) -> Path` and `pg_schema() -> str`;
  - `DirectoryLayout(version)`;
  - `LogFormatError`, `LogFormatMismatch(*, found, expected, where)` and `LogFormatMissing(*, where,
    instructions)`;
  - `sqlite_onboarding(root: Path, run_id: str) -> str` and `postgres_onboarding(run_id: str) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_formats.py
from pathlib import Path

import pytest

from runstate import formats
from runstate.formats import (
    LOG_FORMAT,
    DirectoryLayout,
    LogFormatMismatch,
    LogFormatMissing,
    parse,
)


def test_parse_orders_versions_numerically():
    assert parse("0.10.0") > parse("0.9.9")
    with pytest.raises(ValueError):
        parse("0.3.0.dev0")


def test_directory_layout_addresses():
    lay = DirectoryLayout("0.2.0")
    assert lay.sqlite_path(Path("/r"), "abc") == Path("/r/v0.2.0/abc.db")
    assert lay.pg_schema() == "runstate_v0_2_0"


def test_registry_holds_the_current_format():
    assert LOG_FORMAT == "0.2.0"
    assert formats.FORMATS[LOG_FORMAT].version == LOG_FORMAT


def test_older_than_is_newest_first(monkeypatch):
    monkeypatch.setattr(
        formats,
        "FORMATS",
        {v: DirectoryLayout(v) for v in ["0.1.0", "0.2.0", "0.3.0"]},
    )
    assert [v for v, _ in formats.older_than("0.3.0")] == ["0.2.0", "0.1.0"]


def test_newer_in_parses_names_and_ignores_strangers():
    names = ["v0.2.0", "v0.3.0", "v1.0.0", "vx", "notes", "v0.3"]
    assert formats.newer_in(names, prefix="v", sep=".", than="0.2.0") == ["0.3.0", "1.0.0"]
    schemas = ["runstate_v0_3_0", "public", "runstate_v0_1_0"]
    assert formats.newer_in(schemas, prefix="runstate_v", sep="_", than="0.2.0") == ["0.3.0"]


def test_mismatch_message_says_which_way():
    newer = LogFormatMismatch(found="0.3.0", expected="0.2.0", where="/r")
    older = LogFormatMismatch(found="0.1.0", expected="0.2.0", where="/r")
    assert "upgrade runstate" in str(newer)
    assert "runstate migrate" in str(older)


def test_onboarding_text_names_the_move_and_the_sidecars(tmp_path):
    text = formats.sqlite_onboarding(tmp_path, "abc")
    assert "4729fcd" in text and "v0.2.0" in text and "runstate migrate" in text
    assert "-wal" in text and "-shm" in text
    assert isinstance(LogFormatMissing(where="x", instructions=text), formats.LogFormatError)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_formats.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'runstate.formats'`.

- [ ] **Step 3: Write the implementation**

```python
# runstate/formats/_layout.py
"""How a log is addressed in one format (docs/specs/log-formats.md §3)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class Layout(Protocol):
    """Where a run's log lives in one format, per persistent backend. Memory logs
    have no address and no layout."""

    @property
    def version(self) -> str: ...

    def sqlite_path(self, root: Path, run_id: str) -> Path: ...

    def pg_schema(self) -> str: ...


@dataclass(frozen=True)
class DirectoryLayout:
    """``<root>/v<version>/<rid>.db`` on sqlite; schema ``runstate_v<X>_<Y>_<Z>`` on
    postgres. A later format with a different layout defines its own Layout."""

    version: str

    def sqlite_path(self, root: Path, run_id: str) -> Path:
        return root / f"v{self.version}" / f"{run_id}.db"

    def pg_schema(self) -> str:
        return "runstate_v" + self.version.replace(".", "_")
```

```python
# runstate/formats/v0_2_0.py
"""Format 0.2.0: the format of runstate before reference by name."""

from ._layout import DirectoryLayout

LAYOUT = DirectoryLayout("0.2.0")
```

```python
# runstate/formats/__init__.py
"""Log formats: a log's format is the outermost part of its address
(docs/specs/log-formats.md).

A format is named by the runstate release that introduced it. ``LOG_FORMAT`` is
the one this release reads and writes; ``FORMATS`` holds every known format's
layout, so the locators can recognise an older log and the migration steps can
read it."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from . import v0_2_0
from ._layout import DirectoryLayout, Layout

LOG_FORMAT = "0.2.0"

FORMATS: dict[str, Layout] = {v0_2_0.LAYOUT.version: v0_2_0.LAYOUT}

_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def parse(version: str) -> tuple[int, int, int]:
    """``"X.Y.Z"`` -> a comparable tuple. Anything else is not a format version."""
    m = _VERSION.match(version)
    if m is None:
        raise ValueError(f"not a format version (X.Y.Z): {version!r}")
    return (int(m[1]), int(m[2]), int(m[3]))


def older_than(version: str) -> list[tuple[str, Layout]]:
    """The registered formats older than ``version``, newest first."""
    v = parse(version)
    older = [(k, lay) for k, lay in FORMATS.items() if parse(k) < v]
    return sorted(older, key=lambda kv: parse(kv[0]), reverse=True)


def newer_in(names: Iterable[str], *, prefix: str, sep: str, than: str) -> list[str]:
    """The format versions newer than ``than`` among directory or schema names
    spelled ``<prefix>X<sep>Y<sep>Z``. A newer format's layout cannot be known,
    but its name can."""
    pat = re.compile(
        "^" + re.escape(prefix) + r"(\d+)" + re.escape(sep) + r"(\d+)"
        + re.escape(sep) + r"(\d+)$"
    )
    floor = parse(than)
    found = []
    for n in names:
        m = pat.match(n)
        if m and (int(m[1]), int(m[2]), int(m[3])) > floor:
            found.append(f"{m[1]}.{m[2]}.{m[3]}")
    return sorted(set(found), key=parse)


class LogFormatError(Exception):
    """A log is not in the format this runstate reads."""


class LogFormatMismatch(LogFormatError):
    def __init__(self, *, found: str, expected: str, where: str) -> None:
        self.found, self.expected, self.where = found, expected, where
        if parse(found) > parse(expected):
            action = f"upgrade runstate (this release reads format {expected})"
        else:
            action = f"run `runstate migrate` to move it to format {expected}"
        super().__init__(f"the log at {where} is format {found}, not {expected}: {action}")


class LogFormatMissing(LogFormatError):
    def __init__(self, *, where: str, instructions: str) -> None:
        self.where = where
        super().__init__(instructions)


_BASE = "0.2.0"  # what every log written before versioned addresses can be onboarded as
_BASE_COMMIT = "4729fcd (2026-07-16, lifecycle-v0.4 and launcher-v0.4)"


def sqlite_onboarding(root: Path, run_id: str) -> str:
    return (
        f"{root / (run_id + '.db')} predates versioned addresses, so its format is unknown "
        f"and nothing will infer it. If it was written by runstate at or after {_BASE_COMMIT}, "
        f"it is format {_BASE}: check that its lifecycle.heartbeat bodies carry a `t` field. "
        f"Move it, with any -wal and -shm files beside it, into {root / ('v' + _BASE)}/, "
        f"then run `runstate migrate {root}`. To move every legacy log under the root:\n"
        f"  mkdir -p '{root}/v{_BASE}' && for f in '{root}'/*.db '{root}'/*.db-wal "
        f"'{root}'/*.db-shm; do [ -e \"$f\" ] && mv \"$f\" '{root}/v{_BASE}/'; done"
    )


def postgres_onboarding(run_id: str) -> str:
    schema = DirectoryLayout(_BASE).pg_schema()
    return (
        f"run {run_id!r} is in the unversioned `log` table, which predates versioned "
        f"addresses, so its format is unknown and nothing will infer it. If it was written "
        f"by runstate at or after {_BASE_COMMIT}, it is format {_BASE}. Move the table, "
        f"then run `runstate migrate`:\n"
        f"  CREATE SCHEMA {schema}; ALTER TABLE public.log SET SCHEMA {schema};"
    )


__all__ = [
    "DirectoryLayout",
    "FORMATS",
    "LOG_FORMAT",
    "Layout",
    "LogFormatError",
    "LogFormatMismatch",
    "LogFormatMissing",
    "newer_in",
    "older_than",
    "parse",
    "postgres_onboarding",
    "sqlite_onboarding",
]
```

- [ ] **Step 4: Run the tests and the gates**

Run: `python -m pytest tests/test_formats.py -v && black --check runstate/ tests/ && mypy --strict runstate`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add runstate/formats tests/test_formats.py
git commit -m "feat(formats): the log-format registry, versions and errors (log-formats §2-3)"
```

---

### Task 2: SQLite addresses and the opening checks

**Files:**
- Modify: `runstate/channel/__init__.py` (the `sqlite` branch of `_locate`); `runstate/__init__.py`
  (exports); `tests/test_channel_locators.py`; `tests/test_public_api.py`; `docs/api.md`
- Test: `tests/test_log_formats_sqlite.py`

**Interfaces:**
- Consumes: Task 1.
- Produces: `create_channel` and `attach_channel` for `backend="sqlite"` open
  `<root>/v<LOG_FORMAT>/<rid>.db`, after the five checks of `log-formats.md` §4. Public exports:
  `LOG_FORMAT`, `LogFormatError`, `LogFormatMismatch`, `LogFormatMissing`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_log_formats_sqlite.py
"""log-formats.md §4 on sqlite: the five opening checks, in order."""

import sqlite3
import threading

import pytest

from runstate import (
    LOG_FORMAT,
    LogFormatMismatch,
    LogFormatMissing,
    RunNotFound,
    attach_channel,
    create_channel,
)
from runstate.formats import FORMATS


def _path(root, rid):
    return FORMATS[LOG_FORMAT].sqlite_path(root, rid)


def test_create_births_at_the_current_address(tmp_path):
    ch = create_channel("r1", root=tmp_path)
    ch.send({"x": 1}, topic="value", name="n")
    ch.close()
    assert _path(tmp_path, "r1").exists()
    assert not (tmp_path / "r1.db").exists()


def test_attach_opens_the_current_address(tmp_path):
    create_channel("r1", root=tmp_path).send({}, topic="value", name="n")
    assert attach_channel("r1", root=tmp_path).last_seq() == 1


def test_nothing_anywhere_is_run_not_found(tmp_path):
    with pytest.raises(RunNotFound):
        attach_channel("ghost", root=tmp_path)


def test_a_newer_format_in_the_root_refuses_everything(tmp_path):
    (tmp_path / "v99.0.0").mkdir()
    with pytest.raises(LogFormatMismatch, match="upgrade runstate"):
        create_channel("r1", root=tmp_path)
    with pytest.raises(LogFormatMismatch, match="upgrade runstate"):
        attach_channel("r1", root=tmp_path)


def test_an_older_format_holding_the_run_says_migrate(tmp_path, monkeypatch):
    from runstate import formats
    from runstate.formats import DirectoryLayout

    monkeypatch.setitem(formats.FORMATS, "0.1.0", DirectoryLayout("0.1.0"))
    old = DirectoryLayout("0.1.0").sqlite_path(tmp_path, "r1")
    old.parent.mkdir()
    sqlite3.connect(old).close()
    for locate in (attach_channel, create_channel):
        with pytest.raises(LogFormatMismatch, match="runstate migrate"):
            locate("r1", root=tmp_path)


def test_a_legacy_log_is_missing_never_not_found(tmp_path):
    legacy = tmp_path / "r1.db"
    sqlite3.connect(legacy).close()
    before = legacy.read_bytes()
    for locate in (attach_channel, create_channel):
        with pytest.raises(LogFormatMissing, match="runstate migrate"):
            locate("r1", root=tmp_path)
    assert legacy.read_bytes() == before  # never opened, never mutated
    assert not _path(tmp_path, "r1").exists()  # create did not birth beside it


def test_a_partly_onboarded_root(tmp_path):
    """Review focus 1: onboarded runs open; leftovers raise Missing."""
    create_channel("moved", root=tmp_path).send({}, topic="value", name="n")
    sqlite3.connect(tmp_path / "left.db").close()
    assert attach_channel("moved", root=tmp_path).last_seq() == 1
    with pytest.raises(LogFormatMissing):
        attach_channel("left", root=tmp_path)


@pytest.mark.parametrize("rid", ["a?b", "a#b", "a%20b", "a b"])
def test_special_run_ids_round_trip(tmp_path, rid):
    """Review focus 2."""
    create_channel(rid, root=tmp_path).send({}, topic="value", name="n")
    assert attach_channel(rid, root=tmp_path).last_seq() == 1
    assert _path(tmp_path, rid).exists()


def test_concurrent_births_share_one_log(tmp_path):
    """Review focus 4: racing mkdir of the version directory."""
    errors, chans = [], []

    def birth():
        try:
            c = create_channel("race", root=tmp_path)
            c.send({}, topic="value", name="n")
            chans.append(c)
        except Exception as exc:  # noqa: BLE001 -- the assertion reports it
            errors.append(exc)

    threads = [threading.Thread(target=birth) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert attach_channel("race", root=tmp_path).last_seq() == 8
    for c in chans:
        c.close()


def test_create_does_not_create_a_missing_root(tmp_path):
    with pytest.raises(Exception):
        create_channel("r1", root=tmp_path / "absent")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_log_formats_sqlite.py -v`
Expected: FAIL. `ImportError: cannot import name 'LOG_FORMAT' from 'runstate'` fails the whole module first.

- [ ] **Step 3: Implement the SQLite branch**

In `runstate/channel/__init__.py`, add these imports beside the existing ones:

```python
from .. import formats
from ..formats import LOG_FORMAT, FORMATS, LogFormatMismatch, LogFormatMissing
```

Replace the `if backend == "sqlite":` block of `_locate` with:

```python
    if backend == "sqlite":
        if root is None:
            raise ValueError(
                "the sqlite backend requires a root directory (got root=None)"
            )
        return _locate_sqlite(
            Path(root), run_id, create=create, json_default=json_default
        )
```

Add, above `_locate`:

```python
def _locate_sqlite(
    root: Path,
    run_id: str,
    *,
    create: bool,
    json_default: Callable[[object], object] | None,
) -> Channel:
    """log-formats.md §4, in order: refuse a root holding a newer format; open the
    current address; refuse an older format or the legacy address holding the run;
    else RunNotFound (attach) or a birth at the current address (create)."""
    if root.is_dir():
        newer = formats.newer_in(
            (p.name for p in root.iterdir() if p.is_dir()),
            prefix="v",
            sep=".",
            than=LOG_FORMAT,
        )
        if newer:
            raise LogFormatMismatch(found=newer[-1], expected=LOG_FORMAT, where=str(root))
    path = FORMATS[LOG_FORMAT].sqlite_path(root, run_id)
    if path.exists():
        return SqliteChannel(path, create=create, json_default=json_default)
    for version, layout in formats.older_than(LOG_FORMAT):
        old = layout.sqlite_path(root, run_id)
        if old.exists():
            raise LogFormatMismatch(found=version, expected=LOG_FORMAT, where=str(old))
    legacy = root / f"{run_id}.db"
    if legacy.exists():
        raise LogFormatMissing(
            where=str(legacy), instructions=formats.sqlite_onboarding(root, run_id)
        )
    if not create:
        raise RunNotFound(f"run has no records at {path}")
    path.parent.mkdir(exist_ok=True)  # the root itself must already exist
    return SqliteChannel(path, create=True, json_default=json_default)
```

In `runstate/__init__.py`, import and export the new names. Add to `__all__`, under `# substrate`:

```python
    "LOG_FORMAT",
    "LogFormatError",
    "LogFormatMismatch",
    "LogFormatMissing",
```

and the import line:

```python
from .formats import LOG_FORMAT, LogFormatError, LogFormatMismatch, LogFormatMissing
```

- [ ] **Step 4: Update the existing tests that assumed `<root>/<rid>.db`**

In `tests/test_channel_locators.py`, the foreign-db and corrupt-db tests place their file at the legacy
address, which now raises `LogFormatMissing` before anything opens it. Their intent is "a foreign or corrupt
file *at a run's address*", so move the files to the current address:

- In `test_attach_leaves_foreign_sqlite_byte_identical`, replace `tmp_path / "ghost.db"` with
  `FORMATS[LOG_FORMAT].sqlite_path(tmp_path, "ghost")`, and create its parent first:
  `ghost.parent.mkdir()`. Make the `-wal` and `-shm` assertions use `ghost.with_name("ghost.db-wal")` and
  `ghost.with_name("ghost.db-shm")`.
- In `test_attach_corrupt_db_propagates_not_runnotfound`, do the same for `junk.db`.
- Add `from runstate import LOG_FORMAT` and `from runstate.formats import FORMATS` to the imports.

Run `python -m pytest tests/ -q` and fix each remaining failure the same way. The only acceptable change is
replacing a hand-built `<root>/<rid>.db` with `FORMATS[LOG_FORMAT].sqlite_path(root, rid)`; no assertion may
be weakened.

- [ ] **Step 5: Document the exports**

In `tests/test_public_api.py`, add the four names to `EXPECTED`. In `docs/api.md`, under the substrate
section, add one line per name, each backticked:
- `LOG_FORMAT`: the log format this release reads and writes;
- `LogFormatError`, `LogFormatMismatch` and `LogFormatMissing`: see `docs/specs/log-formats.md` §4.

- [ ] **Step 6: Run everything**

Run: `python -m pytest tests/ -q && black --check runstate/ tests/ && mypy --strict runstate`
Expected: all PASS. Postgres tests skip without a DSN.

- [ ] **Step 7: Commit**

```bash
git add runstate/channel/__init__.py runstate/__init__.py tests/test_log_formats_sqlite.py \
        tests/test_channel_locators.py tests/test_public_api.py docs/api.md
git commit -m "feat(channel): sqlite logs live at <root>/v<format>/<rid>.db, checked at open"
```

---

### Task 3: Postgres schemas and the opening checks

**Files:**
- Modify: `runstate/channel/postgres.py`
- Test: `tests/test_log_formats_postgres.py`

**Interfaces:**
- Consumes: Task 1.
- Produces:
  - `ensure_schema(dsn)` creates schema `runstate_v<LOG_FORMAT>` and its `log` table and indexes.
  - `PostgresChannel(dsn, run_id, create=...)` runs §4's checks, then `SET search_path` to the current
    format's schema, so every existing query is unchanged.
  - `check_format(conn, run_id) -> bool` returns True if the run has rows at the current address.

- [ ] **Step 1: Write the failing tests** (they skip without `RUNSTATE_TEST_PG_DSN`)

```python
# tests/test_log_formats_postgres.py
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
        n = c.execute(f"SELECT count(*) FROM {schema}.log WHERE run_id = %s", [rid]).fetchone()
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
```

- [ ] **Step 2: Run them to verify they fail** (with a DSN set)

Run: `RUNSTATE_TEST_PG_DSN=... python -m pytest tests/test_log_formats_postgres.py -v`
Expected: FAIL. `relation "runstate_v0_2_0.log" does not exist`, because `ensure_schema` still writes to
`public`.

- [ ] **Step 3: Implement**

In `runstate/channel/postgres.py`:

1. Import `from psycopg import sql` and `from .. import formats`, and
   `from ..formats import LOG_FORMAT, FORMATS, LogFormatMismatch, LogFormatMissing`.
2. Replace `ensure_schema`'s body so that it creates the format schema and builds the table inside it:

```python
    schema = FORMATS[LOG_FORMAT].pg_schema()
    with psycopg.connect(dsn) as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_LOCK_KEY,))
        conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
        conn.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema)))
        conn.execute(_CREATE_TABLE)
        conn.execute(_CREATE_INDEX)
        conn.execute(_CREATE_NAME_INDEX)
```

3. Add a module-level function:

```python
def _has_rows(conn: psycopg.Connection[Any], schema: str, run_id: str) -> bool:
    reg = conn.execute("SELECT to_regclass(%s)", [f"{schema}.log"]).fetchone()
    if reg is None or reg[0] is None:
        return False
    hit = conn.execute(
        sql.SQL("SELECT 1 FROM {}.log WHERE run_id = %s LIMIT 1").format(
            sql.Identifier(schema)
        ),
        [run_id],
    ).fetchone()
    return hit is not None


def check_format(conn: psycopg.Connection[Any], run_id: str) -> bool:
    """log-formats.md §4 on postgres. Raises for a newer format in the database,
    or for the run held by an older format or the legacy table. Returns whether
    the run has rows at the current address."""
    names = [r[0] for r in conn.execute("SELECT nspname FROM pg_namespace").fetchall()]
    newer = formats.newer_in(names, prefix="runstate_v", sep="_", than=LOG_FORMAT)
    if newer:
        raise LogFormatMismatch(found=newer[-1], expected=LOG_FORMAT, where="this database")
    if _has_rows(conn, FORMATS[LOG_FORMAT].pg_schema(), run_id):
        return True
    for version, layout in formats.older_than(LOG_FORMAT):
        if _has_rows(conn, layout.pg_schema(), run_id):
            raise LogFormatMismatch(
                found=version, expected=LOG_FORMAT, where=f"schema {layout.pg_schema()}"
            )
    if _has_rows(conn, "public", run_id):
        raise LogFormatMissing(
            where="public.log", instructions=formats.postgres_onboarding(run_id)
        )
    return False
```

4. In `PostgresChannel.__init__`, right after `SET lock_timeout`, run the check and set the search path,
   closing the connection if the check raises:

```python
        try:
            has_rows = check_format(self._conn, run_id)
        except BaseException:
            self._conn.close()
            raise
        self._conn.execute(
            sql.SQL("SET search_path TO {}").format(
                sql.Identifier(FORMATS[LOG_FORMAT].pg_schema())
            )
        )
```

   Then, in the `if not create:` branch, replace the `SELECT COALESCE(MAX(seq), 0)` existence probe with
   `if not has_rows: ... raise RunNotFound(...)`, keeping its `close()` and message. The existing
   `to_regclass('log')` probe now resolves inside the format schema; leave it where it is, after the
   `SET search_path`.

- [ ] **Step 4: Run the Postgres suite**

Run, with a throwaway server per `CLAUDE.md`:
`RUNSTATE_TEST_PG_DSN=... python -m pytest tests/ -q && mypy --strict runstate && black --check runstate/ tests/`
Expected: all PASS, including every existing Postgres test, which now runs inside `runstate_v0_2_0`.

- [ ] **Step 5: Commit**

```bash
git add runstate/channel/postgres.py tests/test_log_formats_postgres.py
git commit -m "feat(postgres): a schema per log format, checked at open"
```

---

### Task 4: Sealing

**Files:**
- Create: `runstate/migrations/seal.py`, `runstate/migrations/__init__.py` (empty for now: a one-line
  docstring)
- Modify: `tests/conftest.py`, adding the `crashed_wal_writer` fixture below, which Task 5 reuses
- Test: `tests/test_seal.py`

Add to `tests/conftest.py`:

```python
@pytest.fixture
def crashed_wal_writer():
    """Review focus 3: write n records in WAL mode from a process that dies without
    closing, leaving its frames un-checkpointed in the WAL -- the realistic state
    of a log whose writer crashed before it was migrated."""
    import subprocess
    import sys

    def write(path, n):
        code = (
            "import os; from runstate.channel.sqlite import SqliteChannel; "
            f"ch = SqliteChannel({str(path)!r}); "
            f"[ch.send({{'i': i}}, topic='value', name='n') for i in range({n})]; "
            "os._exit(0)"
        )
        env = {**os.environ, "RUNSTATE_SQLITE_JOURNAL_MODE": "WAL"}
        subprocess.run([sys.executable, "-c", code], check=True, env=env)

    return write
```

**Interfaces:**
- Produces:
  - `seal_sqlite(path: Path) -> None`
  - `seal_postgres(conn: psycopg.Connection, schema: str, run_id: str) -> None`. It must be called inside a
    transaction; it takes `LOCK TABLE <schema>.log IN SHARE ROW EXCLUSIVE MODE`, which holds until the
    caller commits.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_seal.py
import os
import sqlite3
import uuid

import pytest

from runstate.channel.sqlite import SqliteChannel
from runstate.migrations.seal import seal_sqlite

# Sealing is by file permissions, which the root user bypasses (some CI containers run as
# root). The seal still happens; only "the write is refused" cannot be observed as root.
_AS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


def test_seal_keeps_crashed_wal_frames_and_refuses_writes(
    tmp_path, monkeypatch, crashed_wal_writer
):
    path = tmp_path / "r.db"
    crashed_wal_writer(path, 5)
    assert path.with_name("r.db-wal").exists()  # the frames are still in the WAL
    seal_sqlite(path)
    assert not path.with_name("r.db-wal").exists()
    ro = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    assert ro.execute("SELECT count(*) FROM log").fetchone() == (5,)
    ro.close()
    assert os.stat(path).st_mode & 0o222 == 0
    if not _AS_ROOT:
        # an old writer opening the sealed log fails loudly, under either journal mode
        for mode in ("WAL", "DELETE"):
            monkeypatch.setenv("RUNSTATE_SQLITE_JOURNAL_MODE", mode)
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                with SqliteChannel(path) as ch:
                    ch.send({"late": True}, topic="value", name="n")


def test_sealed_postgres_run_refuses_inserts(pg_ready):
    import psycopg

    from runstate.channel.postgres import ensure_schema
    from runstate.formats import FORMATS, LOG_FORMAT
    from runstate.migrations.seal import seal_postgres

    ensure_schema(pg_ready)
    schema = FORMATS[LOG_FORMAT].pg_schema()
    rid, other = f"seal-{uuid.uuid4().hex}", f"seal-{uuid.uuid4().hex}"
    with psycopg.connect(pg_ready) as c:
        with c.transaction():
            seal_postgres(c, schema, rid)
        with pytest.raises(psycopg.errors.RaiseException, match="sealed"):
            with c.transaction():
                c.execute(
                    f"INSERT INTO {schema}.log VALUES (%s, 1, 'value', 'n', NULL, '{{}}', 0)",
                    [rid],
                )
        with c.transaction():  # other runs are untouched
            c.execute(
                f"INSERT INTO {schema}.log VALUES (%s, 1, 'value', 'n', NULL, '{{}}', 0)",
                [other],
            )
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_seal.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'runstate.migrations'`.

- [ ] **Step 3: Implement**

```python
# runstate/migrations/seal.py
"""Sealing: a sealed log is readable and refuses every write
(docs/specs/log-formats.md §5)."""

from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import psycopg


def seal_sqlite(path: Path) -> None:
    """Checkpoint, leave WAL so the file opens read-only with no sidecars, then
    clear every write bit. An old writer's next append fails with 'attempt to
    write a readonly database'."""
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("PRAGMA journal_mode=DELETE")
    finally:
        conn.close()
    mode = os.stat(path).st_mode
    os.chmod(path, mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


_SEAL_DDL = """
CREATE TABLE IF NOT EXISTS {schema}.sealed_runs (run_id text PRIMARY KEY);
CREATE OR REPLACE FUNCTION {schema}.refuse_sealed() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM {schema}.sealed_runs WHERE run_id = NEW.run_id) THEN
    RAISE EXCEPTION 'runstate: run % is sealed in this log format', NEW.run_id;
  END IF;
  RETURN NEW;
END $$;
CREATE OR REPLACE TRIGGER refuse_sealed BEFORE INSERT ON {schema}.log
  FOR EACH ROW EXECUTE FUNCTION {schema}.refuse_sealed();
"""


def seal_postgres(conn: "psycopg.Connection[Any]", schema: str, run_id: str) -> None:
    """Seal one run in one format's schema. Call inside the caller's transaction;
    the table lock blocks concurrent inserts until the caller commits, so nothing
    slips in between the seal and the copy."""
    from psycopg import sql

    ident = sql.Identifier(schema)
    conn.execute(sql.SQL("LOCK TABLE {}.log IN SHARE ROW EXCLUSIVE MODE").format(ident))
    for stmt in _SEAL_DDL.split(";\n"):
        if stmt.strip():
            conn.execute(sql.SQL(stmt).format(schema=ident))  # type: ignore[arg-type]
    conn.execute(
        sql.SQL("INSERT INTO {}.sealed_runs VALUES (%s) ON CONFLICT DO NOTHING").format(ident),
        [run_id],
    )
```

`CREATE OR REPLACE TRIGGER` needs Postgres 14 or later. If the CI server is older, use
`DROP TRIGGER IF EXISTS refuse_sealed ON {schema}.log;` followed by `CREATE TRIGGER`. Check `SHOW
server_version` on the CI service image, `.github/workflows/tests.yml`, and say which you used in the commit
message. The `split(";\n")` must not split the function body. Its body contains `;` only at line ends inside
`$$`, so verify with the Postgres test, and if the split breaks the body, execute the function as one
statement on its own.

- [ ] **Step 4: Run the tests and gates**

Run: `python -m pytest tests/test_seal.py -v && mypy --strict runstate && black --check runstate/ tests/`
Expected: PASS. The Postgres test passes with a DSN and skips without one.

- [ ] **Step 5: Commit**

```bash
git add runstate/migrations tests/test_seal.py
git commit -m "feat(migrations): seal a log so it reads and refuses writes (log-formats §5)"
```

---

### Task 5: The migration engine and `runstate migrate`

**Files:**
- Modify: `runstate/migrations/__init__.py`
- Create: `runstate/migrations/stores.py`, `runstate/cli.py`
- Modify: `pyproject.toml` (add `[project.scripts] runstate = "runstate.cli:main"`)
- Test: `tests/test_migrate.py`

**Interfaces:**
- Consumes: Tasks 1, 3 and 4.
- Produces:
  - `Row(NamedTuple)`: `seq: int`, `topic: str`, `name: str | None`, `request_id: str | None`, `body: str`
    (the stored JSON text, kept byte-for-byte unless a step changes it), `created_at: float`.
  - `Step(Protocol)`:
    - `FROM: str` and `TO: str`;
    - `def is_live(self, rows: list[Row]) -> bool`: liveness read with the `FROM` format's semantics;
    - `def transform(self, rows: list[Row]) -> list[Row]`.
  - `STEPS: tuple[Step, ...]` (empty in stage 1).
  - `chain(start: str, target: str) -> list[Step]`.
  - `MigrationError(Exception)`.
  - `migrate(store: Store, run_ids: list[str] | None, *, to: str) -> list[str]`, which returns the run ids
    it migrated.
  - In `stores.py`, the `Store` Protocol, implemented by `SqliteStore(root: Path)` and
    `PostgresStore(dsn: str)`, with:
    - `formats_of(run_ids: list[str] | None) -> dict[str, str]` (run id → format);
    - `migrate_one(step: Step, run_id: str) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_migrate.py
"""log-formats.md §6 with test-only formats and steps."""

import json

import pytest

from runstate import formats, migrations
from runstate.channel.sqlite import SqliteChannel
from runstate.formats import DirectoryLayout
from runstate.migrations import MigrationError, Row, chain, migrate
from runstate.migrations.stores import SqliteStore


class Tag:
    """A toy step: stamps {"tag": TO} into every body."""

    def __init__(self, src, dst, live=False, boom_at=None):
        self.FROM, self.TO, self._live, self._boom = src, dst, live, boom_at

    def is_live(self, rows):
        return self._live

    def transform(self, rows):
        if self._boom is not None:
            raise RuntimeError("boom")
        return [r._replace(body=json.dumps({**json.loads(r.body), "tag": self.TO})) for r in rows]


@pytest.fixture
def toy(monkeypatch):
    for v in ["8.0.0", "8.1.0", "8.2.0"]:
        monkeypatch.setitem(formats.FORMATS, v, DirectoryLayout(v))
    steps = (Tag("8.0.0", "8.1.0"), Tag("8.1.0", "8.2.0"))
    monkeypatch.setattr(migrations, "STEPS", steps)
    return steps


def _seed(root, version, rid, n=3):
    path = DirectoryLayout(version).sqlite_path(root, rid)
    path.parent.mkdir(exist_ok=True)
    ch = SqliteChannel(path)
    for i in range(n):
        ch.send({"i": i}, topic="value", name="x")
    ch.close()
    return path


def _bodies(path):
    import sqlite3

    c = sqlite3.connect(path)
    out = [json.loads(b) for (b,) in c.execute("SELECT body FROM log ORDER BY seq")]
    c.close()
    return out


def test_chain_follows_declared_edges(toy):
    assert [s.TO for s in chain("8.0.0", "8.2.0")] == ["8.1.0", "8.2.0"]


def test_chain_refuses_no_path_and_forks(toy, monkeypatch):
    with pytest.raises(MigrationError, match="no path"):
        chain("8.2.0", "8.0.0")
    monkeypatch.setattr(migrations, "STEPS", toy + (Tag("8.0.0", "8.2.0"),))
    with pytest.raises(MigrationError, match="two steps"):
        chain("8.0.0", "8.2.0")


def test_migrate_copies_seals_and_preserves_seq(toy, tmp_path):
    old = _seed(tmp_path, "8.0.0", "r1")
    assert migrate(SqliteStore(tmp_path), None, to="8.2.0") == ["r1"]
    new = DirectoryLayout("8.2.0").sqlite_path(tmp_path, "r1")
    assert [b["tag"] for b in _bodies(new)] == ["8.2.0"] * 3
    assert [b["i"] for b in _bodies(new)] == [0, 1, 2]
    assert _bodies(old) == [{"i": 0}, {"i": 1}, {"i": 2}]  # non-destructive
    import os

    assert os.stat(old).st_mode & 0o222 == 0  # sealed


@pytest.mark.parametrize("rid", ["a?b", "a#b", "a b"])
def test_migrate_special_run_ids(toy, tmp_path, rid):
    """Review focus 2."""
    _seed(tmp_path, "8.0.0", rid)
    assert migrate(SqliteStore(tmp_path), [rid], to="8.1.0") == [rid]
    assert DirectoryLayout("8.1.0").sqlite_path(tmp_path, rid).exists()


def test_a_live_run_is_refused_and_untouched(toy, monkeypatch, tmp_path):
    old = _seed(tmp_path, "8.0.0", "r1")
    before = old.read_bytes()
    monkeypatch.setattr(migrations, "STEPS", (Tag("8.0.0", "8.1.0", live=True),))
    with pytest.raises(MigrationError, match="live episode"):
        migrate(SqliteStore(tmp_path), ["r1"], to="8.1.0")
    import os

    assert os.stat(old).st_mode & 0o200  # not sealed: refused before the seal
    assert old.read_bytes() == before
    assert not DirectoryLayout("8.1.0").sqlite_path(tmp_path, "r1").exists()


def test_a_failure_leaves_it_sealed_and_a_retry_completes(toy, monkeypatch, tmp_path):
    _seed(tmp_path, "8.0.0", "r1")
    monkeypatch.setattr(migrations, "STEPS", (Tag("8.0.0", "8.1.0", boom_at=0),))
    with pytest.raises(RuntimeError, match="boom"):
        migrate(SqliteStore(tmp_path), ["r1"], to="8.1.0")
    target = DirectoryLayout("8.1.0").sqlite_path(tmp_path, "r1")
    assert not target.exists()
    assert not list(target.parent.glob(".*.tmp"))  # no partial file left visible
    monkeypatch.setattr(migrations, "STEPS", (Tag("8.0.0", "8.1.0"),))
    assert migrate(SqliteStore(tmp_path), ["r1"], to="8.1.0") == ["r1"]
    assert target.exists()


def test_wal_frames_of_a_crashed_writer_survive(toy, tmp_path, crashed_wal_writer):
    """Review focus 3: a writer that died leaves frames in the WAL; the copy holds them."""
    path = DirectoryLayout("8.0.0").sqlite_path(tmp_path, "r1")
    path.parent.mkdir()
    crashed_wal_writer(path, 50)
    migrate(SqliteStore(tmp_path), ["r1"], to="8.1.0")
    new = DirectoryLayout("8.1.0").sqlite_path(tmp_path, "r1")
    assert [b["i"] for b in _bodies(new)] == list(range(50))


def test_cli_migrate(toy, tmp_path, capsys):
    from runstate.cli import main

    _seed(tmp_path, "8.0.0", "r1")
    assert main(["migrate", str(tmp_path), "--to", "8.1.0"]) == 0
    assert "r1" in capsys.readouterr().out
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_migrate.py -v`
Expected: FAIL with `ImportError: cannot import name 'MigrationError' from 'runstate.migrations'`.

- [ ] **Step 3: Implement the engine**

```python
# runstate/migrations/__init__.py
"""Migration steps between log formats (docs/specs/log-formats.md §6).

A step declares its FROM and TO formats, reads a run's rows in FROM, and returns
its rows in TO. Steps are retained, never deleted: pinned consumers upgrade on
their own schedule, so any old format may still need moving forward."""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple, Protocol

from .. import formats

if TYPE_CHECKING:
    from .stores import Store


class Row(NamedTuple):
    seq: int
    topic: str
    name: str | None
    request_id: str | None
    body: str  # the stored JSON text, byte-for-byte unless a step rewrites it
    created_at: float


class Step(Protocol):
    FROM: str
    TO: str

    def is_live(self, rows: list[Row]) -> bool: ...

    def transform(self, rows: list[Row]) -> list[Row]: ...


STEPS: tuple[Step, ...] = ()


class MigrationError(Exception):
    pass


def chain(start: str, target: str) -> list[Step]:
    edges: dict[str, Step] = {}
    for s in STEPS:
        if s.FROM in edges:
            raise MigrationError(f"two steps leave format {s.FROM}")
        edges[s.FROM] = s
    out: list[Step] = []
    at = start
    while at != target:
        step = edges.get(at)
        if step is None or formats.parse(step.TO) > formats.parse(target):
            raise MigrationError(f"no path from format {start} to {target}")
        out.append(step)
        at = step.TO
    return out


def migrate(store: "Store", run_ids: list[str] | None, *, to: str) -> list[str]:
    """Move each run (every run under the store when None) to format ``to``,
    one step at a time. Returns the run ids that moved."""
    moved = []
    for run_id, fmt in sorted(store.formats_of(run_ids).items()):
        if fmt == to:
            continue
        for step in chain(fmt, to):
            store.migrate_one(step, run_id)
        moved.append(run_id)
    return moved
```

```python
# runstate/migrations/stores.py
"""Where a migration reads and writes, per backend: one Strategy each."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Protocol
from urllib.request import pathname2url

from .. import formats
from ..channel.sqlite import _SCHEMA
from . import MigrationError, Row, Step
from .seal import seal_postgres, seal_sqlite


class Store(Protocol):
    def formats_of(self, run_ids: list[str] | None) -> dict[str, str]: ...

    def migrate_one(self, step: Step, run_id: str) -> None: ...


def _read_sqlite(path: Path) -> list[Row]:
    conn = sqlite3.connect(f"file:{pathname2url(str(path))}?mode=ro", uri=True)
    try:
        return [
            Row(*r)
            for r in conn.execute(
                "SELECT seq, topic, name, request_id, body, created_at FROM log ORDER BY seq"
            )
        ]
    finally:
        conn.close()


class SqliteStore:
    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def formats_of(self, run_ids: list[str] | None) -> dict[str, str]:
        """Each run's newest registered format present under the root."""
        found: dict[str, str] = {}
        for version in sorted(formats.FORMATS, key=formats.parse):
            layout = formats.FORMATS[version]
            if run_ids is None:
                d = layout.sqlite_path(self._root, "x").parent
                rids = [p.stem for p in d.glob("*.db")] if d.is_dir() else []
            else:
                rids = [r for r in run_ids if layout.sqlite_path(self._root, r).exists()]
            for r in rids:
                found[r] = version
        missing = set(run_ids or ()) - set(found)
        if missing:
            raise MigrationError(f"no log in any known format for {sorted(missing)}")
        return found

    def migrate_one(self, step: Step, run_id: str) -> None:
        """Refuse a live run untouched; seal (unless an earlier failed attempt
        already did); re-read, since the seal checkpoints any WAL frames; then
        write the new log to a hidden temporary file and rename it into place."""
        src = formats.FORMATS[step.FROM].sqlite_path(self._root, run_id)
        dst = formats.FORMATS[step.TO].sqlite_path(self._root, run_id)
        if os.stat(src).st_mode & 0o222:  # not yet sealed
            if step.is_live(_read_sqlite(src)):
                raise MigrationError(f"run {run_id!r} has a live episode; stop it first")
            seal_sqlite(src)
        out = step.transform(_read_sqlite(src))
        dst.parent.mkdir(exist_ok=True)
        tmp = dst.with_name(f".{dst.name}.tmp")
        tmp.unlink(missing_ok=True)
        conn = sqlite3.connect(tmp, isolation_level=None)
        try:
            conn.executescript(_SCHEMA)
            conn.execute("BEGIN")
            conn.executemany(
                "INSERT INTO log (seq, topic, name, request_id, body, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                out,
            )
            conn.execute("COMMIT")
        finally:
            conn.close()
        os.replace(tmp, dst)
```

`_read_sqlite` opens `mode=ro`. On a WAL log whose writer crashed, a read-only open cannot recover the WAL
when the `-shm` is missing. If `test_wal_frames_of_a_crashed_writer_survive` fails on the *pre-seal*
liveness read for that reason, make the pre-seal read open read-write with `isolation_level=None` and
perform no write, so SQLite can recover the WAL. Keep the post-seal read `mode=ro`.

```python
# runstate/migrations/stores.py (continued)
class PostgresStore:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def formats_of(self, run_ids: list[str] | None) -> dict[str, str]:
        import psycopg
        from psycopg import sql

        found: dict[str, str] = {}
        with psycopg.connect(self._dsn) as conn:
            for version in sorted(formats.FORMATS, key=formats.parse):
                schema = formats.FORMATS[version].pg_schema()
                reg = conn.execute("SELECT to_regclass(%s)", [f"{schema}.log"]).fetchone()
                if reg is None or reg[0] is None:
                    continue
                q = sql.SQL("SELECT DISTINCT run_id FROM {}.log").format(sql.Identifier(schema))
                for (r,) in conn.execute(q).fetchall():
                    if run_ids is None or r in run_ids:
                        found[r] = version
        missing = set(run_ids or ()) - set(found)
        if missing:
            raise MigrationError(f"no log in any known format for {sorted(missing)}")
        return found

    def migrate_one(self, step: Step, run_id: str) -> None:
        import psycopg
        from psycopg import sql

        src = sql.Identifier(formats.FORMATS[step.FROM].pg_schema())
        dst_name = formats.FORMATS[step.TO].pg_schema()
        dst = sql.Identifier(dst_name)
        with psycopg.connect(self._dsn) as conn, conn.transaction():
            rows = [
                Row(*r)
                for r in conn.execute(
                    sql.SQL(
                        "SELECT seq, topic, name, request_id, body, created_at"
                        " FROM {}.log WHERE run_id = %s ORDER BY seq"
                    ).format(src),
                    [run_id],
                )
            ]
            if step.is_live(rows):
                raise MigrationError(f"run {run_id!r} has a live episode; stop it first")
            seal_postgres(conn, formats.FORMATS[step.FROM].pg_schema(), run_id)
            from ..channel.postgres import _CREATE_INDEX, _CREATE_NAME_INDEX, _CREATE_TABLE

            conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(dst))
            conn.execute(sql.SQL("SET LOCAL search_path TO {}").format(dst))
            for ddl in (_CREATE_TABLE, _CREATE_INDEX, _CREATE_NAME_INDEX):
                conn.execute(ddl)
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO log (run_id, seq, topic, name, request_id, body, created_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    [(run_id, *r) for r in step.transform(rows)],
                )
```

```python
# runstate/cli.py
"""The ``runstate`` command: ``runstate migrate <root> [<rid> ...] [--to V]``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import formats
from .migrations import migrate
from .migrations.stores import PostgresStore, SqliteStore, Store


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="runstate")
    sub = p.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("migrate", help="move logs forward to a newer log format")
    m.add_argument("root", help="a directory (sqlite) or a DSN (postgres)")
    m.add_argument("run_ids", nargs="*", help="default: every run under the root")
    m.add_argument("--to", default=formats.LOG_FORMAT)
    m.add_argument("--backend", choices=["sqlite", "postgres"], default="sqlite")
    args = p.parse_args(argv)
    store: Store = (
        PostgresStore(args.root) if args.backend == "postgres" else SqliteStore(Path(args.root))
    )
    for rid in migrate(store, args.run_ids or None, to=args.to):
        print(rid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

In `pyproject.toml`, add:

```toml
[project.scripts]
runstate = "runstate.cli:main"
```

- [ ] **Step 4: Run the tests and gates**

Run: `python -m pytest tests/test_migrate.py tests/test_seal.py -v && python -m pytest tests/ -q && mypy --strict runstate && black --check runstate/ tests/`
Expected: all PASS. Add a Postgres migration test mirroring `test_migrate_copies_seals_and_preserves_seq`
through `PostgresStore`, using `pg_ready`, toy schemas `runstate_v8_0_0` and `runstate_v8_1_0`, and dropping
both in `finally`. It must pass with a DSN. On Postgres the seal and the copy are one transaction, so a failed
migration rolls back completely, leaving the run unsealed and unmigrated. That's stricter than SQLite's
"sealed and unmigrated", and §6's retry rule holds for both. Add a test that a transform raising leaves the
old schema's row count and the absence of a `sealed_runs` entry unchanged.

- [ ] **Step 5: Commit**

```bash
git add runstate/migrations runstate/cli.py pyproject.toml tests/test_migrate.py
git commit -m "feat(migrations): chained, copying, sealing migrations and \`runstate migrate\`"
```

---

### Task 6: Stage-1 docs

**Files (modify):** `docs/specs/channel-locators.md`, `docs/specs/channel-postgres.md`, `docs/specs/store.md`,
`docs/design-v0.2.md`, `CLAUDE.md`, `docs/backlog/release-and-stability-contract.md`,
`docs/specs/log-formats.md`.

- [ ] **Step 1:** In `channel-locators.md`, add a section "Addresses and formats". It says where a log lives
  (`<root>/v<format>/<rid>.db`; a schema per format on Postgres) and lists the five opening checks with
  their errors. Link `log-formats.md` §4; don't copy it.
- [ ] **Step 2:** In `channel-postgres.md`, say that the shared `log` table lives in schema
  `runstate_v<format>`, that `ensure_schema` creates it, and that the channel sets `search_path` at open.
  Note that a transaction-mode pooler drops session settings, matching the existing advisory-lock caveat.
- [ ] **Step 3:** In `store.md`:
  - update the placement recipe so a log's path is `<root>/v<format>/<rid>.db`;
  - add to Recipe 3 (garbage collection): "collecting a run deletes its log at its format's address".
- [ ] **Step 4:** In `design-v0.2.md` §4:
  - one paragraph: the locators check a log's format at open (`log-formats.md`);
  - a dated revision-history entry.
- [ ] **Step 5:** In `CLAUDE.md`, beside the pinning note, add the upgrade procedure: bump the consumer's
  pin, onboard legacy logs as the error message instructs, then `runstate migrate`.
- [ ] **Step 6:** In `release-and-stability-contract.md` §(b), mark it **resolved by
  `docs/specs/log-formats.md`**: option 3, with detection replaced by the address.
- [ ] **Step 7:** In `log-formats.md`:
  - set the status to `IMPLEMENTED <date>`;
  - add two findings from implementation:
    - §5: sealing by file permissions does not bind the root user;
    - §6: on Postgres the seal and the copy are one transaction, so a failure leaves the run untouched
      rather than sealed.
- [ ] **Step 8:** Commit:

```bash
git add docs CLAUDE.md
git commit -m "docs: log formats implemented -- locators, postgres schemas, placement, upgrade procedure"
```

---

# Stage 2 — reference by name (format 0.3.0)

The spike commit `374c1a2` (branch `spike/reference-by-name`) holds a working, measured implementation. Its
base is `72d9c3f`. Stage 2 ports it onto this branch, then makes the spec's changes on top:
- `honoured` → `honored`;
- the incremental `Watcher.pending_stops`;
- the backfill turned into a migration step.

Its report is `docs/review-2026-10-02-reference-by-name.md`.

### Task 7: Wire shapes — schemas and payloads

**Files:** `protocol/lifecycle-v0.4.schema.json` → `protocol/lifecycle-v0.5.schema.json`,
`protocol/subscription-v0.2.schema.json` → `protocol/subscription-v0.3.schema.json`,
`runstate/vocabulary/payloads.py`, `runstate/vocabulary/schedule.py`, `runstate/__init__.py`,
`tests/test_payloads.py`, `tests/test_schema.py`.

**Interfaces:**
- Produces:
  - `Topic.LIFECYCLE_BOUND = "lifecycle.bound"`;
  - `Heartbeat(step, consumed_seq, t, claim_seq: int)`;
  - `Stopped(completed, error, final_step, t, claim_seq: int | None, honored: list[str])`;
  - `Bound(claim_seq: int)`, with `TOPIC = Topic.LIFECYCLE_BOUND`;
  - `runstate.Bound` exported.

- [ ] **Step 1: Apply the spike's wire-shape diff**

```bash
git diff 72d9c3f 374c1a2 -- protocol/ runstate/vocabulary/payloads.py runstate/vocabulary/schedule.py \
  | git apply --3way
git diff 72d9c3f 374c1a2 -- runstate/__init__.py | git apply --3way   # adds Bound; keep stage 1's exports
```

Resolve any conflict in `runstate/__init__.py` by keeping both sides' exports.

- [ ] **Step 2: Rename `honoured` → `honored`** with the Edit tool (`replace_all: true`) in
  `protocol/lifecycle-v0.5.schema.json` and `runstate/vocabulary/payloads.py`. Grep to confirm none remain:
  `grep -rn honour protocol runstate`. Expected: no output.

- [ ] **Step 3: Write the failing shape tests.** In `tests/test_payloads.py` and `tests/test_schema.py`, update
  the class-A shape tests named in the spike report:
  - `test_heartbeat_body_is_pinned`, `test_stopped_error_and_final_step_present_nullable` and
    `test_convention_dataclasses_serialize…` assert the 0.3.0 bodies, including `claim_seq` and
    `honored: []`;
  - `test_topic_enum_matches_reserved_set` includes `lifecycle.bound`;
  - `test_public_surface_is_stable` includes `Bound`, and `docs/api.md` documents it.

  Add one test:

```python
def test_bound_and_stopped_round_trip_through_the_schema():
    from runstate.vocabulary.payloads import Bound, Stopped
    from dataclasses import asdict
    assert asdict(Bound(claim_seq=3)) == {"claim_seq": 3}
    s = Stopped(completed=True, error=None, final_step=9, t=1.0, claim_seq=3, honored=["s1"])
    assert asdict(s)["honored"] == ["s1"]
```

  Then make `tests/test_schema.py` validate a `lifecycle.bound` envelope and a `control.stop` that has no
  `request_id` (which must fail validation).

- [ ] **Step 4: Run the shape tests**

Run: `python -m pytest tests/test_payloads.py tests/test_schema.py tests/test_public_api.py -v`
Expected: PASS. The rest of the suite is red until Task 8; that's expected.

- [ ] **Step 5: Commit** with `--no-verify`, and say so in the message (the suite is red by design until
  Task 8):

```bash
git add protocol runstate/vocabulary runstate/__init__.py tests/test_payloads.py tests/test_schema.py \
        tests/test_public_api.py docs/api.md
git commit --no-verify -m "feat(vocabulary): lifecycle-v0.5 and subscription-v0.3 wire shapes (suite red until the next task)"
```

---

### Task 8: Worker, observables and Watcher read and write by name

**Files:** `runstate/worker.py`, `runstate/observables.py`, `runstate/watcher.py`; create
`tests/test_reference_by_name.py`.

**Interfaces:**
- Consumes: Task 7.
- Produces, with names from the spike and `honoured` renamed:
  - `observables._claim_name(e) -> tuple[bool, int | None]` and `observables._honored(e) -> list[str]`;
  - `observables.lease_void(...)` and `observables.current_heartbeat(channel, claim)`;
  - `_terminal_stopped(channel, claim, strict)`;
  - `Worker._die(...)`, the compare-and-swap loop over a fully read control tail;
  - `Worker._spent_stops: set[str]`.

- [ ] **Step 1: Apply the spike's library diff**

```bash
git diff 72d9c3f 374c1a2 -- runstate/worker.py runstate/observables.py runstate/watcher.py \
  | git apply --3way
git checkout 374c1a2 -- tests/test_reference_by_name.py
```

- [ ] **Step 2: Rename** with Edit (`replace_all: true`) in all four files:
  - `_honoured` → `_honored`;
  - `honoured=` → `honored=`;
  - `"honoured"` → `"honored"`;
  - prose "honoured" → "honored".

  Confirm with `grep -rn honour runstate tests`. Expected: no output.

- [ ] **Step 3: Run the scenario suite**

Run: `python -m pytest tests/test_reference_by_name.py -v`
Expected: PASS on memory and SQLite (19 tests × backends; Postgres with a DSN). If a test fails only because
stage 1 changed a path or an export, fix the test the way Task 2 Step 4 did, and change nothing else.

- [ ] **Step 4: Run the gates**

Run: `mypy --strict runstate && black --check runstate/ tests/`
Expected: PASS. The full suite still has the pre-existing failures that Task 10 fixes. List them with
`python -m pytest tests/ -q 2>&1 | grep FAILED`, and check that every one is named in the spike report's T1
classification (classes A, A′ and B, plus the fixture-shape list). Any failure *not* in that report is a
regression; stop and investigate.

- [ ] **Step 5: Commit** with `--no-verify` and the reason:

```bash
git add runstate/worker.py runstate/observables.py runstate/watcher.py tests/test_reference_by_name.py
git commit --no-verify -m "feat: records name what they answer -- worker, folds, watcher (port of 374c1a2; old tests fixed next)"
```

---

### Task 9: `Watcher.pending_stops`, the incremental form

**Files:** `runstate/watcher.py`; test `tests/test_pending_stops.py`; `docs/api.md` documents the method.

**Interfaces:**
- Consumes: Task 8 (`observables._honored`, `undischarged_stops`).
- Produces: `Watcher.pending_stops(run_id: str) -> list[Envelope]`, sorted by seq.

- [ ] **Step 1: Write the failing property test**

```python
# tests/test_pending_stops.py
"""reference-by-name §5: the Watcher's incremental pending stops equal the pure
fold after every record, on random histories."""

import random

import pytest

from runstate import Watcher, create_channel, undischarged_stops
from runstate.vocabulary.payloads import Topic


def _random_history(rng, n):
    ids = [f"s{i}" for i in range(6)]
    for _ in range(n):
        kind = rng.choice(["stop", "stop", "nameless", "stopped", "nak", "value"])
        if kind == "stop":
            yield {}, Topic.CONTROL_STOP, rng.choice(ids)
        elif kind == "nameless":
            yield {}, Topic.CONTROL_STOP, None
        elif kind == "stopped":
            honored = rng.sample(ids, rng.randint(0, 3))
            body = {
                "completed": False, "error": None, "final_step": None, "t": 0.0,
                "claim_seq": None, "honored": honored,
            }
            yield body, Topic.LIFECYCLE_STOPPED, None
        elif kind == "nak":
            yield {"reason": "malformed", "message": ""}, Topic.LIFECYCLE_NAK, rng.choice(ids)
        else:
            yield {"value": 1, "step": 0, "t": None}, "value", None


@pytest.mark.parametrize("seed", range(25))
def test_incremental_equals_pure_after_every_record(tmp_path, seed):
    rng = random.Random(seed)
    ch = create_channel(f"r{seed}", root=tmp_path, backend="memory")
    w = Watcher()
    w.observe(f"r{seed}", ch)
    for body, topic, rid in _random_history(rng, 60):
        ch.send(body, topic=topic, request_id=rid, name="x" if topic == "value" else None)
        if rng.random() < 0.5:  # poll at irregular moments, like a real caller
            got = [e.seq for e in w.pending_stops(f"r{seed}")]
            assert got == [e.seq for e in undischarged_stops(ch)]
    assert [e.seq for e in w.pending_stops(f"r{seed}")] == [
        e.seq for e in undischarged_stops(ch)
    ]


def test_a_spent_id_reused_is_dead_on_arrival(tmp_path):
    ch = create_channel("r", root=tmp_path, backend="memory")
    w = Watcher()
    w.observe("r", ch)
    ch.send({}, topic=Topic.CONTROL_STOP, request_id="s1")
    ch.send({"reason": "malformed", "message": ""}, topic=Topic.LIFECYCLE_NAK, request_id="s1")
    assert w.pending_stops("r") == []
    ch.send({}, topic=Topic.CONTROL_STOP, request_id="s1")
    assert w.pending_stops("r") == [] == undischarged_stops(ch)


def test_reads_only_new_records(tmp_path):
    ch = create_channel("r", root=tmp_path, backend="memory")
    w = Watcher()
    w.observe("r", ch)
    for i in range(100):
        ch.send({}, topic=Topic.CONTROL_STOP, request_id=f"s{i}")
    w.pending_stops("r")
    calls = []
    real_read = ch.read
    ch.read = lambda **kw: (calls.append(kw.get("after")), real_read(**kw))[1]  # type: ignore[method-assign]
    ch.send({}, topic=Topic.CONTROL_STOP, request_id="new")
    assert len(w.pending_stops("r")) == 101
    assert calls == [100]  # one read, starting after everything already seen
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_pending_stops.py -v`
Expected: FAIL with `AttributeError: 'Watcher' object has no attribute 'pending_stops'`.

- [ ] **Step 3: Implement.** In `runstate/watcher.py`, add beside `_RunState`:

```python
@dataclass
class _PendingStops:
    """Incremental ``undischarged_stops`` for one run (reference-by-name §5).

    The unanswered stops by id, every id an answer has named (spent: a later
    stop reusing one is dead on arrival, exactly as the pure fold treats it),
    and a read cursor of its own, never the event cursor. Each call reads only
    what is new, so a long run costs O(new records) per poll, not O(log)."""

    cursor: int = 0
    pending: dict[str, Envelope] = field(default_factory=dict)
    spent: set[str] = field(default_factory=set)

    def update(self, channel: Channel) -> list[Envelope]:
        for e in channel.read(
            after=self.cursor,
            topics=[Topic.CONTROL_STOP, Topic.LIFECYCLE_STOPPED, Topic.LIFECYCLE_NAK],
        ):
            self.cursor = e.seq
            if e.topic == Topic.CONTROL_STOP:
                if e.request_id is not None and e.request_id not in self.spent:
                    self.pending[e.request_id] = e
            elif e.topic == Topic.LIFECYCLE_STOPPED:
                for rid in _honored(e):
                    self.spent.add(rid)
                    self.pending.pop(rid, None)
            elif e.request_id is not None:  # a nak names what it refuses
                self.spent.add(e.request_id)
                self.pending.pop(e.request_id, None)
        return sorted(self.pending.values(), key=lambda e: e.seq)
```

Add `stops: _PendingStops = field(default_factory=_PendingStops)` to `_RunState`, and the method to
`Watcher`:

```python
    def pending_stops(self, run_id: str) -> list[Envelope]:
        """The run's stops no answer names -- ``undischarged_stops``, kept
        incrementally: each call reads only records new since the last."""
        st = self._runs[run_id]
        return st.stops.update(st.channel)
```

Import `_honored` from `.observables`. Document `Watcher.pending_stops` in `docs/api.md` next to
`undischarged_stops`.

- [ ] **Step 4: Run the tests and gates**

Run: `python -m pytest tests/test_pending_stops.py -v && mypy --strict runstate && black --check runstate/ tests/`
Expected: PASS.

- [ ] **Step 5: Commit** (`--no-verify` still applies until Task 10):

```bash
git add runstate/watcher.py tests/test_pending_stops.py docs/api.md
git commit --no-verify -m "feat(watcher): pending_stops, the incremental undischarged_stops"
```

---

### Task 10: Rewrite the pre-existing tests that pin positions or old shapes

**Files:** `tests/test_worker.py`, `tests/test_observables.py`, `tests/test_watcher.py`,
`tests/test_memoizer.py`, `tests/test_service_worker.py`, `tests/test_subscription.py`,
`tests/test_schedule.py`, `tests/test_implementers_guide.py`, `docs/implementers-guide.md`. Edit only the
files that actually hold the failures.

The full list is the spike report's T1 table (`docs/review-2026-10-02-reference-by-name.md` §4 T1). Treat
each class as follows:

- **Fixture shapes** (37 tests and 3 modules: memoizer 15, observables 14, watcher 8, plus the payloads,
  schema and guide modules). Hand-composed records gain their 0.3.0 fields:
  - heartbeats get `claim_seq` naming the `started` they follow;
  - `stopped` gets `claim_seq` and `honored`;
  - stops get a `request_id`.

  Pass all of these through one small helper at the top of each module:

  ```python
  def hb(ch, step, consumed_seq, claim_seq, t=0.0):
      return ch.send({"step": step, "consumed_seq": consumed_seq, "t": t, "claim_seq": claim_seq},
                     topic="lifecycle.heartbeat")
  ```

  Assertions do not change.
- **Class A (15), emitted-body equality and closed sets:** update the expected bodies and sets to 0.3.0.
  Update `docs/implementers-guide.md`'s examples, which `test_valid_examples…` validates, so they show
  `claim_seq`, `honored` and a `request_id` on every stop.
- **Class A′ (9), fixtures the named world can't build** (a heartbeat with no claim): add the `started` the
  heartbeat names. The three `await_consumed` tests that hung on the spike must now pass within their
  existing timeouts.
- **Class B (12), positional semantics, intended changes:** replace each with a test of the named behavior,
  and keep the name with `_by_name` appended. Use the spike report's per-test reasons:
  - **stop needs an id:** a nameless stop is naked, not pending;
  - **a spent id is dead;**
  - **an answer counts wherever it lands;**
  - **a naked stop is answered by its nak;**
  - **a lease is void only through its registering episode** (`lifecycle.bound`). Each class-B fixture that
    fabricates a `started` that never took in the lease becomes a test that the lease is **not** void.

- [ ] **Step 1:** Run `python -m pytest tests/ -q 2>&1 | grep FAILED > /tmp/failing.txt`, and check the list
  against the report.
- [ ] **Step 2:** Fix the fixture-shape class module by module. Run that module after each.
- [ ] **Step 3:** Fix class A, including the guide's examples.
- [ ] **Step 4:** Fix class A′.
- [ ] **Step 5:** Rewrite class B, one replacement test at a time, each failing first (run it against the
  pre-Task-8 code by name if useful) and then passing.
- [ ] **Step 6:** Run the full suite with and without a Postgres DSN, plus `mypy --strict runstate` and
  `black --check runstate/ tests/`. Expected: everything green, with no `--no-verify` from here on.
- [ ] **Step 7: Commit** normally; the hook must pass:

```bash
git add tests docs/implementers-guide.md
git commit -m "test: pin reference by name -- fixtures in 0.3.0 shape, positional tests replaced by named ones"
```

---

### Task 11: Order independence as a property test

**Files:** create `tests/test_order_independence.py`.

**Interfaces:** consumes the folds from Task 8, and the spike harness at
`/tmp/claude-1641171234/-home-gchurchill-src-runstate/fd6b9e26-2832-4ee3-a3ce-199057df78bd/scratchpad/rbn/`
(`scenarios.py`, `t2.py`). If that scratch directory is gone, rebuild the generator from §T2's description in
the spike report.

- [ ] **Step 1:** Port the generator and the reordering into the test file:
  - **histories** drive the real `Worker` through 1–4 episodes ending in stop, completion, error, crash,
    displacement or retire;
  - **interleaved with them:** stops, subscriptions, third-party releases and raw value sends;
  - **each record is tagged with its writer;**
  - **the reorderings are random linear extensions** of four orders: each writer's own order, the order
    among claims, "a record follows what it names", and "a launch's death follows its claim";
  - **the replay** writes each reordering into a fresh memory channel, renaming each claim's `claim_seq` to
    its new seq.

  Assert that `undischarged_stops`, `live_demand`, `live_episode`, `progress` and `peek_terminal`'s verdict
  record are identical across reorderings. The spike's only residual (malformed records naming nothing) is
  excluded by construction: the generator writes no malformed records.
- [ ] **Step 2:** Size it with `N = int(os.environ.get("RUNSTATE_ORDER_HISTORIES", "40"))` histories ×
  `K = int(os.environ.get("RUNSTATE_ORDER_PERMUTATIONS", "8"))` reorderings, fixed seed. Measure the default
  and keep it under about 1 s (`python -m pytest tests/test_order_independence.py --durations=1`). The full
  spike run is `RUNSTATE_ORDER_HISTORIES=2000 RUNSTATE_ORDER_PERMUTATIONS=40`.
- [ ] **Step 3:** Run it at both sizes; both must pass.
- [ ] **Step 4: Commit.**

```bash
git add tests/test_order_independence.py
git commit -m "test: the named reads are invariant under causal reordering"
```

---

### Task 12: The `0.2.0 → 0.3.0` migration step

**Files:** create `runstate/formats/v0_3_0.py` and `runstate/migrations/v0_2_0_to_v0_3_0.py`; modify
`runstate/formats/__init__.py` (register 0.3.0) and `runstate/migrations/__init__.py` (register the step);
create `tests/test_migration_v0_2_0_to_v0_3_0.py`.

**Interfaces:**
- Consumes: Task 5 (`Row` and `Step`), and the spike's backfill
  (`git show 374c1a2:scripts/backfill_reference_by_name.py`, function `backfill(envs) -> Result`).
- Produces: `STEPS = (V0_2_0_to_V0_3_0(),)`, and `FORMATS` holding `"0.3.0"`. `LOG_FORMAT` stays `"0.2.0"`
  until Task 13.

- [ ] **Step 1: Write the failing tests.** Use golden 0.2.0 logs built with **master's** behavior. Build them
  with the 0.2.0 shapes by hand (`started`, heartbeats without `claim_seq`, nameless stops, positional
  answers), mirroring the spike's T4 synthetic world. Tests:

```python
# tests/test_migration_v0_2_0_to_v0_3_0.py
import json

from runstate.migrations import Row
from runstate.migrations.v0_2_0_to_v0_3_0 import V0_2_0_to_V0_3_0

step = V0_2_0_to_V0_3_0()


def r(seq, topic, body, rid=None, name=None, t=0.0):
    return Row(seq, topic, name, rid, json.dumps(body, separators=(",", ":")), t)


def test_names_are_what_the_positional_rule_said():
    rows = [
        r(1, "lifecycle.started", {"handle": "local://h/1", "t": 0.0}),
        r(2, "control.stop", {}),  # nameless
        r(3, "lifecycle.heartbeat", {"step": 1, "consumed_seq": 2, "t": 1.0}),
        r(4, "lifecycle.stopped", {"completed": False, "error": None, "final_step": 1, "t": 2.0}),
    ]
    out = {x.seq: json.loads(x.body) for x in step.transform(rows)}
    stop_id = [x for x in step.transform(rows) if x.seq == 2][0].request_id
    assert stop_id == "stop@2"
    assert out[3]["claim_seq"] == 1
    assert out[4]["claim_seq"] == 1 and out[4]["honored"] == ["stop@2"]


def test_unchanged_records_keep_their_bytes():
    raw = r(1, "value", {"value": 0.1, "step": 0, "t": None}, name="loss")
    assert step.transform([raw]) == [raw]


def test_minted_names_never_collide():
    """Review focus 5."""
    rows = [
        r(1, "lifecycle.started", {"handle": "local://h/1", "t": 0.0}),
        r(2, "control.stop", {}, rid="stop@3"),   # a user id shaped like a minted one
        r(3, "control.stop", {}),                  # nameless at seq 3 -> would mint stop@3
        r(4, "lifecycle.stopped", {"completed": False, "error": None, "final_step": 0, "t": 1.0}),
    ]
    out = step.transform(rows)
    ids = [x.request_id for x in out if x.topic == "control.stop"]
    assert len(set(ids)) == 2
    honored = json.loads([x for x in out if x.topic == "lifecycle.stopped"][0].body)["honored"]
    assert sorted(honored) == sorted(ids)


def test_a_live_episode_is_live():
    rows = [r(1, "lifecycle.started", {"handle": "local://otherhost/1", "t": 0.0})]
    assert step.is_live(rows)  # unresolvable foreign handle reads as live
```

  Add a test that migrates a whole golden SQLite log with `migrate(SqliteStore(root), None, to="0.3.0")` and
  checks `peek_terminal`, `live_episode`, `undischarged_stops` and `live_demand`. Read the 0.3.0 log by
  opening it directly with `SqliteChannel`, since `LOG_FORMAT` is still 0.2.0. Compare against master's
  values for the same golden log, recorded as literals in the test, and allow only the spike's two known
  classes of difference: the stale-beat leak, and a naked stop answered by its nak.

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_migration_v0_2_0_to_v0_3_0.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

- `runstate/formats/v0_3_0.py`: `LAYOUT = DirectoryLayout("0.3.0")`. Register it in
  `formats/__init__.py`'s `FORMATS`.
- `runstate/migrations/v0_2_0_to_v0_3_0.py`: a class `V0_2_0_to_V0_3_0` with `FROM = "0.2.0"`,
  `TO = "0.3.0"`.
  - `transform(rows)`:
    1. convert `Row`s to `Envelope`s (`json.loads(body)`);
    2. run the spike's `backfill` logic, copied in as module-level functions, with `honoured` renamed to
       `honored`;
    3. map the results back to `Row`s. Keep each original row's `body` text byte-for-byte when its body is
       unchanged. Keep `created_at` by seq. Appended `lifecycle.bound` rows take the maximum `created_at` of
       the input.
  - **Make minted names collision-free:** `_fresh` must check the run's existing request ids. The spike's
    `_fresh(base, taken)` does; make sure `taken` is seeded with every request id in the run.
  - `is_live(rows)`: the 0.2.0 positional rule. The latest `started` is live if no `stopped` follows it, and
    its handle does not resolve dead (`runstate.vocabulary.handle.resolve(handle)` is not `False`), so an
    unresolvable foreign handle reads as live.
- Register `STEPS = (V0_2_0_to_V0_3_0(),)` in `runstate/migrations/__init__.py`. Import it at the bottom of
  the module, so the module's own names are defined first.

- [ ] **Step 4: Run the tests and gates**

Run: `python -m pytest tests/test_migration_v0_2_0_to_v0_3_0.py tests/test_migrate.py -v && python -m pytest tests/ -q && mypy --strict runstate && black --check runstate/ tests/`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add runstate/formats runstate/migrations tests/test_migration_v0_2_0_to_v0_3_0.py
git commit -m "feat(migrations): the 0.2.0 -> 0.3.0 step, from the measured backfill"
```

---

### Task 13: Introduce format 0.3.0, and the stage-2 docs

This is the only commit that changes the format, as `log-formats.md` §2 rule 3 requires.

**Files:** `runstate/formats/__init__.py` (`LOG_FORMAT = "0.3.0"`), `pyproject.toml`
(`version = "0.3.0.dev0"`), `tests/test_formats.py` (the current-format assertion), and the docs below.

- [ ] **Step 1:** Set `LOG_FORMAT = "0.3.0"` and the package version to `0.3.0.dev0`. Update
  `test_registry_holds_the_current_format` to expect `"0.3.0"`.
- [ ] **Step 2:** Run the full suite with and without a Postgres DSN. Expected: green. Logs are now born at
  `<root>/v0.3.0/` and in schema `runstate_v0_3_0`; tests that used `FORMATS[LOG_FORMAT]` follow
  automatically. Add one test confirming that a root holding a `v0.2.0` log for a run now raises
  `LogFormatMismatch` with "runstate migrate".
- [ ] **Step 3: Docs.**
  - **`design-v0.2.md`:**
    - §7: replace "a standing fact's eliminator must follow it by `seq`" with `reference-by-name.md` §2's
      rule;
    - §10: lifecycle-v0.5 and subscription-v0.3;
    - a revision-history entry.
  - **`stop-discharge.md`:** the discharge names its stops, and the dying breath is a compare-and-swap over
    a fully read tail.
  - **`service-worker.md`:** the answer fold by name; spent ids.
  - **`time-lease-boundary.md`:** the recordless boundary void becomes `lifecycle.bound`, and the
    renewing-client gap is accepted. Note that a client helper renewing on a new claim could shrink it.
  - **`observables.md`:** the named reads; `Watcher.pending_stops`.
  - **`CLAUDE.md`:** the architecture notes (`honored`, `claim_seq`, `lifecycle.bound`).
  - **Backlog:**
    - `identity-in-records.md` layer 1 → IMPLEMENTED;
    - `index.md`'s `discharge-by-id` entry → realised;
    - `protocol-algebra.md` L2's list of positional rules → named.
  - **Specs:** `reference-by-name.md` status → IMPLEMENTED.
- [ ] **Step 4: Commit**

```bash
git add runstate/formats/__init__.py pyproject.toml tests/test_formats.py docs CLAUDE.md
git commit -m "feat: log format 0.3.0 -- reference by name (package 0.3.0.dev0)"
```

- [ ] **Step 5:** Push the branch and open a PR to `master`, merged by **merge commit** (the owner's
  preference). The PR body names this plan and both specs, and says the consumers stay pinned at `72d9c3f`
  until each chooses to upgrade.
