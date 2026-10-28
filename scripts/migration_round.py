#!/usr/bin/env python3
"""The migration round's corpus runner (docs/plans/2026-10-23-migration-round.md,
Tasks 11 and 22): real logs through the shipped 0.2.0 -> 0.3.0 step and the
0.3.0 -> 0.4.0 step, under a consumer's configuration, with every read the
migration changes attributed to a declared divergence.

    scripts/migration_round.py ROOT... --out DIR --max-rows N --max-bytes B \\
        --cap C --budget S --run-on K \\
        [--assume MODULE:ATTR] [--resolve PROJECTION=CHOOSER] [--accept DIVERGENCE]

THE CONFIGURATION is what ``runstate migrate`` takes (log-formats.md §6): the facts
(``--assume``), the choosers (``--resolve``) and the acceptances (``--accept``),
parsed once by the shared mechanism over both steps, and the limits, which change
results and so are required here, at the edge, and recorded in the summary. A
consumer's own configuration is its module of facts and classifier, named on the
command line, with its choosers and acceptances.

READ-ONLY. Every log is opened ``mode=ro&immutable=1``, and nothing is written
under a ROOT: the runner refuses an ``--out`` at or under one. ``immutable=1``
reads the main database file alone, so a non-empty ``-wal`` or ``-journal`` beside
a log is reported (``unread_sidecars``), never read.

THE FILES. Every ``*.db`` under each ROOT, following symlinks (a symlink cycle is
cut), resolved and counted once per inode: a consumer's cell->home symlinks reach
each log twice. A file is skipped, and counted by why (``SkipWhy``), when it is
a broken symlink, unreadable, empty (a tombstone), at an address of a format this
runstate does not register, beside the same run's log in a newer registered
format (superseded when it is sealed, as ``runstate migrate`` leaves the log it
moves, and a conflict when not), not a SQLite database, too corrupt to read its
schema, another SQLite error (named), or one with no runstate ``log`` table. Its
format is read from its address: ``<root>/v<X>/<rid>.db`` is format X, and any
other address is a legacy one, format 0.2.0 (log-formats.md §7).

STREAMING. One run at a time: a run's rows, reads and results are dropped before
the next run is read, its line is written and flushed as soon as it is measured,
and only counters cross runs. A log file larger than ``--max-bytes``, and a run
with more than ``--max-rows`` rows, is counted and skipped before its rows are
read; discovery has opened it only to read its schema, which is the file's first
pages.

NEVER AN ABORT. Whatever fails on one file or one run is recorded and the corpus
run goes on: a directory, an entry or a file the OS or SQLite will not read is
a skip, with what refused it, and an exception while measuring a run, a step's,
a read's or the runner's own (``MemoryError`` included), is a crash on that
run's line, by its stage, its type and the function it was raised in. A body
that is not JSON is always a crash of the read, before any step reads it.
``summary.json`` is rewritten every ``_SUMMARY_EVERY`` runs and once more however
the runner stops, with ``complete`` false unless it finished.

EACH RUN, as ``runstate migrate``'s detection pass takes it, in memory:
1. Each step in turn finds its forks (``detect``) on its input. A projection that
   forks with no ``--resolve`` leaves the run ``undecided``, its forks counted by
   projection. Otherwise the step's transform runs (``transform_checked``), and
   where each divergence fires is recorded, with the ones no ``--accept`` names:
   ``runstate migrate`` would refuse those runs, the runner measures them. A
   step's refusal is recorded by its kind (``refusal_kind``, the one boundary that
   reads a step's messages). 0.3.0's ``is_live`` is recorded as
   ``live_by_0_3_0``, and refuses nothing here.
2. Every 0.4.0 row is validated against the 0.4.0 schemas, loaded from
   ``protocol/`` here (ruling R1), and "Also" is checked.
3. THE READS. The 0.3.0 reads, ``value_series``, ``peek_terminal`` and
   ``undischarged_stops``, over a ``MemoryChannel`` of the 0.3.0 rows; the 0.4.0
   reads, ``runstate.lineage``'s ``series`` per name at ``LatestClaimHead``, its
   ``verdict`` and ``undischarged_stops``, over one of the 0.4.0 rows. Each
   difference is attributed to the divergences that fired there (``Reads``), or
   ``unattributed``: an unattributed difference is a read-changing rule the step
   does not declare. A read that raises ``MalformedRecordError`` is a value: a
   0.3.0 verdict that raises is compared as one, and a 0.4.0 series that raises
   leaves the series uncompared, with the error.

4. P1's reads (``LineageCheck``): ``runstate.lineage``'s ``lineage``, ``count``,
   ``prefix_len`` and ``last_element`` over the 0.4.0 log, each of which must
   hold (not raise), the last element agreeing with the series' last.
5. THE CENSUS (``Census``), from the candidates the 0.3.0 step's ``detect`` built
   (the step keeps them for its transform): each commit's end-time fork, split by
   what is open, a tick or undated (dating) and the places an undated end may
   stand (place); the rewinds; and every resumed claim whose first tick is at the
   step of the save some candidate says it loaded, with its writer versions.

ISOLATION. Each run is measured in a child process (``os.fork``), sequentially:
its wall time and peak RSS (``wait4``'s ``ru_maxrss``) are the run's, and a child
the OS kills is that run's crash. A run's record crosses back pickled.

OUTPUT. ``DIR/runs.jsonl``: one ``RunRecord`` per run, as JSON, written as each
is measured. ``DIR/skipped.jsonl``: one ``{path, why, detail}`` per skipped
file, ``detail`` naming SQLite's error where there is one.
``DIR/summary.json``: the totals (``Totals``).
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import pickle
import re
import sqlite3
import sys
import time
import traceback
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, fields, is_dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, NamedTuple
from urllib.request import pathname2url

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]  # noqa: E402

from runstate import formats, observables  # noqa: E402
from runstate import lineage as reads  # noqa: E402
from runstate.channel import Envelope, MemoryChannel  # noqa: E402
from runstate.migrations import (  # noqa: E402
    Memo,
    MigrationError,
    Planned,
    Row,
    Step,
    transform_checked,
)
from runstate.migrations import v0_3_0_to_v0_4_0 as step04  # noqa: E402
from runstate.migrations import v0_3_0_to_v0_4_0_engine as engine  # noqa: E402
from runstate.migrations.ambiguity import (  # noqa: E402
    Choices,
    History,
    Limits,
    Migrated,
    parse_choices,
    resolutions,
)
from runstate.migrations.stores import sqlite_traces  # noqa: E402
from runstate.migrations.v0_2_0_to_v0_3_0 import V0_2_0_to_V0_3_0  # noqa: E402

_STEP_0_2_0 = V0_2_0_to_V0_3_0()
_STEP_0_3_0 = step04.V0_3_0_to_V0_4_0()
_STEPS: tuple[Step, ...] = (_STEP_0_2_0, _STEP_0_3_0)

_PROTOCOL = Path(__file__).resolve().parent.parent / "protocol"
_COLUMNS = {"seq", "topic", "name", "request_id", "body", "created_at"}
_SIDECARS = ("-wal", "-journal")  # what immutable=1 leaves unread
_VERSIONED = re.compile(r"^v\d+\.\d+\.\d+$")

_COMMIT = "lifecycle.commit"
_STOPPED = "lifecycle.stopped"
_STARTED = "lifecycle.started"
_HEARTBEAT = "lifecycle.heartbeat"
_DIED = "supervisor.died"
_REWIND = "lifecycle.rewind"
_VALUE = "value"


# ----- the classes, each next to the rule it names --------------------------------


class SkipWhy(StrEnum):
    """Why a file under a root is not measured."""

    BROKEN_LINK = "a broken symlink"
    EMPTY = "an empty file (a tombstone)"
    UNREGISTERED_FORMAT = "an address of a format this runstate does not register"
    SUPERSEDED = "superseded by the run's log in a newer format, and sealed"
    CONFLICT = "beside the run's log in a newer format, and not sealed"
    UNREADABLE = "unreadable: the OS refused a stat or a listing, named in detail"
    NOT_SQLITE = "not a sqlite database"
    CORRUPT = "a sqlite database too corrupt to read its schema"
    SQLITE_ERROR = "another sqlite error, named in detail"
    UNCLASSIFIED = "an error classifying it, named in detail"
    NO_LOG_TABLE = "no runstate log table"


class StepName(StrEnum):
    """The two steps a run is taken through, by the formats they join."""

    FROM_0_2_0 = "0.2.0 -> 0.3.0"
    FROM_0_3_0 = "0.3.0 -> 0.4.0"


class Status(StrEnum):
    MEASURED = "measured"
    SKIPPED_MAX_BYTES = "skipped_max_bytes"
    SKIPPED_MAX_ROWS = "skipped_max_rows"
    REFUSED = "refused"
    UNDECIDED = "undecided"  # a projection forks that no --resolve decides
    CRASHED = "crashed"


class Stage(StrEnum):
    """Where a crash happened: in a step, in a read the round measures, or in
    the runner's own measurement of a run, which is recorded on the run's line
    too and never aborts the corpus run."""

    READ = "read"
    FROM_0_2_0 = StepName.FROM_0_2_0.value
    FROM_0_3_0 = StepName.FROM_0_3_0.value
    OLD_READS = "the 0.3.0 reads"
    NEW_READS = "the 0.4.0 reads"
    RUNNER = "the runner's own measurement"


class Also(StrEnum):
    """§7's "Also", with Review Focus 1 and §2's one-exit rule."""

    LATER_VALUE = "a node names a value at or after it"
    PARENT_AT_OR_AFTER = "a parent at or after its child"
    CONSUMED_AT_OR_AFTER = "a commit at or before the record its consumed_seq names"
    RESUME_AT_OR_AFTER = "a resume_from at or after its started or rewind"
    CLAIM_AT_OR_AFTER = "a claim_seq at or after its record"
    WRONG_KIND = "a seq-valued field naming no record of the kind it names"
    TWO_EXITS = "a claim with a second exit"
    SCHEMA = "a row the 0.4.0 schemas reject"


# ----- the run record ---------------------------------------------------------------


@dataclass(frozen=True)
class Refusal:
    step: str
    kind: str
    message: str


@dataclass(frozen=True)
class Crash:
    stage: str
    kind: str
    message: str


@dataclass(frozen=True)
class InvalidRow:
    seq: int
    topic: str
    errors: list[str]


@dataclass(frozen=True)
class VerdictValue:
    """A verdict as the reads compare it: none, a set of outcomes, or a raise."""

    kind: str  # none, outcomes, raised
    outcomes: list[str]
    error: str | None

    def same(self, other: VerdictValue) -> bool:
        return (self.kind, self.outcomes) == (other.kind, other.outcomes)


@dataclass(frozen=True)
class Cell:
    """A differing series cell: the divergences that explain it, else
    ``unattributed``; the old read's value (its 0.3.0 seq) and the new read's
    elements there (theirs)."""

    name: str
    step: int
    divergences: list[str]
    old: int | None
    new: list[int]


@dataclass(frozen=True)
class SeriesResult:
    not_compared: str | None  # why not: the 0.4.0 read's error
    cells: int  # compared, or, when not, the old read's
    equal: int
    differing: int
    by_divergence: dict[str, int]
    unattributed: int
    differing_cells: list[Cell]


@dataclass(frozen=True)
class VerdictResult:
    old: VerdictValue
    new: VerdictValue
    equal: bool
    divergences: list[str]  # those explaining a difference, else unattributed


@dataclass(frozen=True)
class StopsResult:
    """The pending stops' ids, old and new."""

    old: list[str]
    new: list[str]
    equal: bool
    divergences: list[str]


@dataclass(frozen=True)
class Reads:
    series: SeriesResult
    verdict: VerdictResult
    stops: StopsResult


@dataclass(frozen=True)
class LineageCheck:
    """P1's reads over the 0.4.0 log: each that raised, by read and error, and each
    name whose ``last_element`` is not its series' last."""

    raised: list[str]
    last_disagrees: list[str]


@dataclass(frozen=True)
class Resumed:
    """A resumed claim whose first tick is at the step of a save some candidate
    loads: the claim's seq, the step, and the writer versions its classifier names."""

    claim: int
    step: int
    versions: list[str]


@dataclass(frozen=True)
class Census:
    """What the 0.3.0 step's candidates leave open, before any chooser: commits by how
    their end is open (``dating``: a tick or undated, or several ticks; ``undated``:
    no tick, so its time is a chooser's; ``places``: an undated end that may stand at
    more than one place), the rewinds, and the resumes at the loaded step."""

    commits: int
    dating: int
    undated: int
    places: int
    rewinds: int
    resumed: list[Resumed]


@dataclass(frozen=True)
class RunRecord:
    """One line of runs.jsonl."""

    path: str
    run_id: str
    address: str
    format: str
    rows: int | None  # None when its rows cannot be counted
    unread_sidecars: list[str]
    status: str
    # 0.3.0's is_live, which `runstate migrate` asks before it moves a run: an
    # unresolvable foreign handle reads live. None when the run never reached
    # 0.3.0, or is_live refused it (a claim with no handle).
    live_by_0_3_0: bool | None = None
    refusal: Refusal | None = None
    crash: Crash | None = None
    # each step's forks of each projection, by step and projection, the forks of
    # every step the run reached; ``undecided_on``: an undecided run's forks of the
    # projections no --resolve decides, empty for any other run
    forks: dict[str, dict[str, int]] = field(default_factory=dict)
    undecided_on: dict[str, dict[str, int]] = field(default_factory=dict)
    # where each divergence fired, its count of sites, and those no --accept names
    divergences: dict[str, int] = field(default_factory=dict)
    unaccepted: list[str] = field(default_factory=list)
    rows_0_4_0: int | None = None
    schema_invalid: list[InvalidRow] = field(default_factory=list)
    also: dict[str, int] = field(default_factory=dict)
    reads: Reads | None = None
    lineage_check: LineageCheck | None = None
    census: Census | None = None
    seconds: float | None = None  # the child's wall time
    peak_rss_kb: int | None = None  # the child's peak resident set (ru_maxrss)


# ----- refusals and crashes, mapped once ---------------------------------------------

# The steps' messages are their interface: each refusal a step raises maps to
# one kind here, and only here. "{topic}" takes the record's topic from the
# message's "the <topic> at seq N". An unknown message is its own kind.
_REFUSALS: dict[StepName, tuple[tuple[str, str], ...]] = {
    StepName.FROM_0_2_0: (
        (
            r"precedes every claim: format 0\.3\.0 requires",
            "a heartbeat before every claim",
        ),
        (r"does not renumber, but", "a broken seq-and-topic preservation"),
        (r"refused it:", "a chooser refused"),
        (r"answered in another shape", "a chooser answered in another shape"),
    ),
    StepName.FROM_0_3_0: (
        (r"has an empty topic", "envelope: an empty topic"),
        (r"has an empty name, which envelope", "envelope: an empty name"),
        (r"has an empty request_id, which envelope", "envelope: an empty request_id"),
        (r"has a body that is not an object", "envelope: a body that is not an object"),
        (r"is a user's topic under supervisor\.\*", "a user's topic under supervisor."),
        (r"is no topic format 0\.3\.0 defines under", "an undefined reserved topic"),
        (r"which format 0\.4\.0 pins null", "a field 0.4.0 pins null"),
        (r"does not read as", "an unreadable {topic}"),
        (
            r"samples the subscription .*: 0\.4\.0 cannot hold",
            "a subscription's sample",
        ),
        (r"is a subscription's record: 0\.4\.0 cannot hold", "a subscription's record"),
        (r"has no name, which value-v0\.3 requires", "a value with no name"),
        (r"holds a step atom", "a stop holding a step atom"),
        (r"an in-claim rewind", "an in-claim rewind"),
        (r"honors the empty id", "an empty honored id"),
        (r"has no request_id, which subscription-v0\.3", "a stop with no request id"),
        (
            r"names no launch: launcher-v0\.4 requires",
            "a launcher record naming no launch",
        ),
        (r"is a second for launch", "a second terminated of one launch"),
        (r"which is not a claim before it", "a record naming a claim not before it"),
        (r"which names no record before it", "a watermark naming no record before it"),
        (r"'s commit: a watermark is a read position", "a watermark naming a commit"),
        (r"records name: its death report needs", "a launch several launched name"),
        (
            r"which leaves the log: renumbering",
            "a name of a record that leaves the log",
        ),
        (
            r"only a consumer's classifier may read it",
            "a malformed stopped no classifier reads",
        ),
        (
            r"no history of the stated writers writes the log",
            "the facts admit no history",
        ),
        (r"closes a pass at the horizon, its loop free", "the horizon"),
        (r"nothing shows where its log was born", "a birth nothing dates"),
        (
            r"born in format 0\.2\.0, as .* shows, but",
            "a birth the evidence contradicts",
        ),
        (r"states summed over the layers", "past the budget"),
        (r"more than \d+ states", "past the cap"),
        (r"refused it:", "a chooser refused"),
        (r"answered in another shape", "a chooser answered in another shape"),
    ),
}


_TOPIC = re.compile(r"^the (\S+) at seq \d+")


def _normalized(message: str) -> str:
    """A message with its numbers and quoted names abstracted, so that one kind
    of unknown message is one kind, whatever run raised it."""
    return re.sub(r"'[^']*'", "'…'", re.sub(r"-?\d+", "N", message))


def refusal_kind(step: StepName, message: str) -> str:
    """THE ONE BOUNDARY that reads a refusal's message: its kind, by the first
    pattern of ``step``'s table it matches, else ``unknown: <message>`` with its
    numbers abstracted, never dropped."""
    for pattern, kind in _REFUSALS[step]:
        if re.search(pattern, message):
            topic = _TOPIC.match(message)
            return kind.format(topic=topic[1] if topic else "record")
    return f"unknown: {_normalized(message)}"


def _crash(stage: Stage, exc: BaseException) -> Crash:
    """A crash's kind: the exception's type, the function it was raised in, and
    its message with numbers abstracted; its message: the raw one, and where."""
    message = str(exc)
    frames = traceback.extract_tb(exc.__traceback__)
    where = frames[-1] if frames else None
    at = "?" if where is None else where.name
    kind = f"{type(exc).__name__} in {at}: {_normalized(message)}"[:300]
    if where is not None:
        message = f"{message} (at {Path(where.filename).name}:{where.lineno})"
    return Crash(stage.value, kind, message[:2000])


def _raised(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


# ----- the files ----------------------------------------------------------------------


class Found(NamedTuple):
    """A log to measure: its resolved path, its address and its format."""

    path: Path
    address: str
    format: str


class Skipped(NamedTuple):
    path: Path
    why: SkipWhy
    detail: str | None  # what refused it: an errno, SQLite's error name, an error


@dataclass
class FileCounts:
    paths: int = 0  # every *.db path reached, symlinks included
    unique: int = 0  # files, by inode
    duplicate_paths: int = 0
    # by why: each a *.db file, or a directory or entry discovery could not read
    skipped: Counter[str] = field(default_factory=Counter)


def _connect(path: Path) -> sqlite3.Connection:
    """Read-only, and immutable: nothing is locked, created or rolled back."""
    return sqlite3.connect(
        f"file:{pathname2url(str(path))}?mode=ro&immutable=1", uri=True
    )


def _unreadable(path: Path, exc: OSError) -> Skipped:
    """A path the OS would not let discovery stat or list, with its errno."""
    code = errno.errorcode.get(exc.errno, "?") if exc.errno is not None else "?"
    return Skipped(path, SkipWhy.UNREADABLE, f"{code}: {exc.strerror or exc}")


def _db_paths(root: Path) -> Iterator[Path | Skipped]:
    """Every ``*.db`` path under ``root``, depth first in name order, following
    symlinked directories but never into one of their own ancestors (a cycle).
    A directory it cannot list, or an entry it cannot stat (a symlink into a
    directory it may not search), is an unreadable path, never fatal."""
    try:
        if not root.is_dir():
            if root.name.endswith(".db"):
                yield root
            return
    except OSError as exc:
        yield _unreadable(root, exc)
        return
    stack: list[tuple[Path, frozenset[tuple[int, int]]]] = [(root, frozenset())]
    while stack:
        d, ancestors = stack.pop()
        try:
            st = d.stat()
            entries = sorted(os.scandir(d), key=lambda e: e.name)
        except OSError as exc:
            yield _unreadable(d, exc)
            continue
        here = (st.st_dev, st.st_ino)
        if here in ancestors:
            continue  # a symlink back to an ancestor
        below = ancestors | {here}
        dirs: list[Path] = []
        for e in entries:
            try:
                is_dir = e.is_dir(follow_symlinks=True)
            except OSError as exc:
                yield _unreadable(Path(e.path), exc)
                continue
            if is_dir:
                dirs.append(Path(e.path))
            elif e.name.endswith(".db"):
                yield Path(e.path)
        stack.extend((p, below) for p in reversed(dirs))


def _address(path: Path) -> tuple[str, str] | SkipWhy:
    """(address, format) by where the log sits: a registered layout's address is
    its format's, and any other a legacy one, format 0.2.0."""
    for version, layout in formats.FORMATS.items():
        if layout.sqlite_path(path.parent.parent, path.stem) == path:
            return path.parent.name, version
    if _VERSIONED.match(path.parent.name):
        return SkipWhy.UNREGISTERED_FORMAT
    return "legacy", formats.LEGACY_FORMAT


def _newer(path: Path, version: str, address: str) -> SkipWhy | None:
    """Whether the same run has a non-empty log at a newer registered format's
    address. ``runstate migrate`` seals the log it moves, clearing its write
    bits, so a sealed older log is superseded by the newer, which is the run. An
    unsealed one beside it is a conflict: two logs of one run, either of which
    may have been written last (a legacy log onboarding skipped because its
    destination existed, say). Neither is guessed at; the newer is measured."""
    if address == "legacy":
        root, newer = path.parent, list(formats.FORMATS)
    else:
        root = path.parent.parent
        newer = [
            v for v in formats.FORMATS if formats.parse(v) > formats.parse(version)
        ]
    for v in newer:
        p = formats.FORMATS[v].sqlite_path(root, path.stem)
        if p.is_file() and p.stat().st_size > 0:
            sealed = not path.stat().st_mode & 0o222
            return SkipWhy.SUPERSEDED if sealed else SkipWhy.CONFLICT
    return None


# Why a file whose schema SQLite cannot read is skipped, by SQLite's error name.
# Any other error is SQLITE_ERROR, its name kept, so a new one is seen.
_SQLITE_SKIPS = {
    "SQLITE_NOTADB": SkipWhy.NOT_SQLITE,
    "SQLITE_CORRUPT": SkipWhy.CORRUPT,
    "SQLITE_CANTOPEN": SkipWhy.UNREADABLE,
    "SQLITE_PERM": SkipWhy.UNREADABLE,
}


def _not_a_log(path: Path) -> Skipped | None:
    """Why the file is no runstate log to measure, or None."""
    try:
        conn = _connect(path)
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(log)")}
        finally:
            conn.close()
    except sqlite3.Error as exc:
        name = exc.sqlite_errorname
        return Skipped(path, _SQLITE_SKIPS.get(name, SkipWhy.SQLITE_ERROR), name)
    return None if cols == _COLUMNS else Skipped(path, SkipWhy.NO_LOG_TABLE, None)


def discover(roots: Sequence[Path], counts: FileCounts) -> Iterator[Found | Skipped]:
    """Each file under the roots once, by inode, as a log to measure or a skip.
    Holds only the inodes seen, never a run. Nothing about one path is fatal: a
    path the OS will not let it look at is skipped as unreadable, and any other
    error classifying a file as unclassified, each with what refused it."""
    seen: set[tuple[int, int]] = set()
    for root in roots:
        for p in _db_paths(root):
            if isinstance(p, Skipped):
                yield p
                continue
            counts.paths += 1
            try:
                item = _classify(p, seen, counts)
            except OSError as exc:
                item = _unreadable(p, exc)
            except Exception as exc:
                detail = f"{type(exc).__name__}: {exc}"
                item = Skipped(p, SkipWhy.UNCLASSIFIED, detail)
            if item is not None:
                yield item


def _classify(
    p: Path, seen: set[tuple[int, int]], counts: FileCounts
) -> Found | Skipped | None:
    """One path: a log, a skip, or None for a duplicate of a file seen."""
    if not p.exists():
        return Skipped(p, SkipWhy.BROKEN_LINK, None)
    real = p.resolve()
    st = real.stat()
    key = (st.st_dev, st.st_ino)
    if key in seen:
        counts.duplicate_paths += 1
        return None
    seen.add(key)
    counts.unique += 1
    if st.st_size == 0:
        return Skipped(real, SkipWhy.EMPTY, None)
    where = _address(real)
    if isinstance(where, SkipWhy):
        return Skipped(real, where, None)
    address, version = where
    newer = _newer(real, version, address)
    if newer is not None:
        return Skipped(real, newer, None)
    return _not_a_log(real) or Found(real, address, version)


def _count_rows(path: Path) -> int:
    conn = _connect(path)
    try:
        (n,) = conn.execute("SELECT count(*) FROM log").fetchone()
        return int(n)
    finally:
        conn.close()


def _read_rows(path: Path) -> list[Row]:
    """Every row, in seq order."""
    conn = _connect(path)
    try:
        return [
            Row(*r)
            for r in conn.execute(
                "SELECT seq, topic, name, request_id, body, created_at FROM log"
                " ORDER BY seq"
            )
        ]
    finally:
        conn.close()


def _unread_sidecars(path: Path) -> list[str]:
    out = []
    for suffix in _SIDECARS:
        p = Path(str(path) + suffix)
        if p.is_file() and p.stat().st_size > 0:
            out.append(suffix)
    return out


# ----- the 0.4.0 schemas (ruling R1) -----------------------------------------------------


class Stack(NamedTuple):
    """The validators of log format 0.4.0: the envelope, then each convention."""

    envelope: Any
    control: Any
    lifecycle: Any
    supervisor: Any
    value: Any


def load_stack() -> Stack:
    def load(name: str) -> Any:
        schema = json.loads((_PROTOCOL / f"{name}.schema.json").read_text())
        Draft202012Validator.check_schema(schema)
        return Draft202012Validator(schema)

    return Stack(
        envelope=load("envelope-v0.2"),
        control=load("subscription-v0.4"),
        lifecycle=load("lifecycle-v0.6"),
        supervisor=load("supervisor-v0.5"),
        value=load("value-v0.3"),
    )


def _schema_errors(stack: Stack, e: Envelope) -> list[str]:
    """The envelope's errors, and its convention's when 0.4.0 owns its topic: a
    user's topic answers to the envelope alone."""
    record = {
        "seq": e.seq,
        "topic": e.topic,
        "name": e.name,
        "request_id": e.request_id,
        "body": e.body,
    }
    validators = [stack.envelope]
    if e.topic.startswith("control."):
        validators.append(stack.control)
    elif e.topic.startswith("lifecycle."):
        validators.append(stack.lifecycle)
    elif e.topic.startswith("supervisor."):
        validators.append(stack.supervisor)
    elif e.topic == _VALUE:
        validators.append(stack.value)
    return [str(err.message) for v in validators for err in v.iter_errors(record)]


# ----- the reads, and what explains each difference -------------------------------------

# The divergences that change each read (the step's DIVERGENCES, §7).
_SERIES = (
    step04.REGISTER_VALUE,
    step04.UNHELD_VALUE,
    step04.RECOUNTED_VALUE,
    step04.OFF_HEAD_VALUE,
    step04.SEVERAL_VALUES,
)
_VERDICT = (
    step04.THIRD_PARTY_RELEASE,
    step04.THIRD_PARTY_NOTE,
    step04.THIRD_PARTY_REAP,
    step04.FENCED_EXIT,
    step04.ATTRIBUTED_DEATH,
    step04.UNATTRIBUTED_REAP,
)
_STOPS = (step04.EXIT_HONORED,)
UNATTRIBUTED = "unattributed"


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class Old(NamedTuple):
    """The old read's value at a cell: its 0.3.0 seq and value."""

    seq: int
    value: Any


class Element(NamedTuple):
    """A new read's element: the value's 0.3.0 seq and its value."""

    old: int
    value: Any


class OldReads(NamedTuple):
    """The 0.3.0 reads: the series, the verdict and the pending stops."""

    series: dict[str, dict[int, Any]]
    verdict: VerdictValue
    stops: list[Envelope]


def old_reads(ch03: MemoryChannel) -> OldReads:
    """A verdict that raises ``MalformedRecordError`` is a value."""
    try:
        result = observables.peek_terminal(ch03)
    except observables.MalformedRecordError as exc:
        verdict = VerdictValue("raised", [], _raised(exc))
    else:
        verdict = (
            VerdictValue("none", [], None)
            if result is None
            else VerdictValue("outcomes", [str(result.outcome)], None)
        )
    return OldReads(
        observables.value_series(ch03), verdict, observables.undischarged_stops(ch03)
    )


class NewVerdict(NamedTuple):
    verdict: VerdictValue
    stops: list[int]


def new_verdict(ch04: MemoryChannel) -> NewVerdict:
    """A verdict that raises ``MalformedRecordError`` is a value."""
    try:
        result = reads.verdict(ch04)
    except observables.MalformedRecordError as exc:
        verdict = VerdictValue("raised", [], _raised(exc))
    else:
        verdict = (
            VerdictValue("none", [], None)
            if result is None
            else VerdictValue("outcomes", sorted(str(o) for o in result.outcomes), None)
        )
    return NewVerdict(verdict, reads.undischarged_stops(ch04))


def _old_cells(
    env03: Sequence[Envelope], series: Mapping[str, Mapping[int, Any]]
) -> dict[tuple[str, int], Old]:
    """0.3.0's ``value_series``, with the record each cell's value is from: the
    last by seq of the values its fold reads (a name, a ``value`` key, an
    integer step). The values are checked against the read itself."""
    cells: dict[tuple[str, int], Old] = {}
    for e in env03:
        if (
            e.topic == _VALUE
            and e.name is not None
            and "value" in e.body
            and observables.is_step(e.body.get("step"))
        ):
            cells[(e.name, e.body["step"])] = Old(e.seq, e.body["value"])
    read = {(n, s): v for n, steps in series.items() for s, v in steps.items()}
    assert read.keys() == cells.keys(), "the old cells are not value_series's"
    for k, v in read.items():
        assert _canonical(v) == _canonical(cells[k].value), f"{k} is not value_series's"
    return cells


def _new_cells(
    ch04: MemoryChannel, env03: Sequence[Envelope], env04: Sequence[Envelope]
) -> dict[tuple[str, int], list[Element]]:
    """The 0.4.0 series at ``LatestClaimHead``, each element at its cell,
    ``(name, count(Commits(), node) - 1)``, by its 0.3.0 seq: the step writes
    every value where it stood, in order, so the k-th value of each log is one
    record."""
    head = reads.LatestClaimHead().resolve(ch04)
    nodes = reads.lineage(ch04, head=head)
    commits = {e.seq for e in env04 if e.topic == _COMMIT}
    count: dict[int, int] = {}
    running = 0
    for n in nodes:
        running += n in commits
        count[n] = running
    old_of = dict(
        zip(
            (e.seq for e in env04 if e.topic == _VALUE),
            (e.seq for e in env03 if e.topic == _VALUE),
            strict=True,
        )
    )
    names = {e.name for e in env04 if e.topic == _VALUE and e.name is not None}
    cells: dict[tuple[str, int], list[Element]] = {}
    for name in sorted(names):
        for el in reads.series(ch04, name, head=head):
            cells.setdefault((name, count[el.node] - 1), []).append(
                Element(old_of[el.seq], el.value)
            )
    return cells


def series_result(
    old: Mapping[tuple[str, int], Old],
    new: Mapping[tuple[str, int], list[Element]],
    fired: Mapping[str, tuple[int, ...]],
) -> SeriesResult:
    """Each cell either read: equal when the new read holds exactly one element
    there, equal in value to the old read's. A differing cell is explained by the
    series divergences whose sites hold the old value (it left the cell) or a new
    element that is not it (it came in), else it is unattributed."""
    sites = {d.name: set(fired[d.name]) for d in _SERIES}
    equal = differing = unattributed = 0
    by: Counter[str] = Counter()
    cells: list[Cell] = []
    for key in sorted(old.keys() | new.keys(), key=repr):
        o, n = old.get(key), new.get(key, [])
        if (
            o is not None
            and len(n) == 1
            and _canonical(n[0].value) == _canonical(o.value)
        ):
            equal += 1
            continue
        differing += 1
        why: set[str] = set()
        inside = {e.old for e in n}
        if o is not None and o.seq not in inside:
            why |= {d for d in (x.name for x in _SERIES) if o.seq in sites[d]}
        for e in n:
            if o is None or e.old != o.seq:
                why |= {d for d in (x.name for x in _SERIES) if e.old in sites[d]}
        if len(n) > 1:
            why |= {
                step04.SEVERAL_VALUES.name
                for e in n
                if e.old in sites[step04.SEVERAL_VALUES.name]
            }
        found = sorted(why) or [UNATTRIBUTED]
        by.update(found)
        unattributed += not why
        cells.append(
            Cell(key[0], key[1], found, None if o is None else o.seq, sorted(inside))
        )
    return SeriesResult(
        None,
        len(old.keys() | new.keys()),
        equal,
        differing,
        dict(by),
        unattributed,
        cells,
    )


def _explained(
    same: bool,
    fired: Mapping[str, tuple[int, ...]],
    which: Sequence[Any],
    *,
    since: int,
) -> list[str]:
    """The divergences of ``which`` that fired at a 0.3.0 seq at or after ``since``."""
    if same:
        return []
    return [d.name for d in which if any(s >= since for s in fired[d.name])] or [
        UNATTRIBUTED
    ]


def compare(
    old: OldReads,
    new: NewVerdict,
    series: SeriesResult,
    env03: Sequence[Envelope],
    env04: Sequence[Envelope],
    fired: Mapping[str, tuple[int, ...]],
) -> Reads:
    """The verdict's outcomes (0.3.0's one taken as a one-element set) and the
    pending stops' ids, each difference explained by the divergences that change
    that read and fired where it reads, else unattributed. The verdict reads the
    latest claim's episode, so only a site at or after its ``started`` explains it.
    The stop fold reads the whole control plane, and a stop's answer may sit in any
    claim, so any site in the run explains it."""
    same = old.verdict.same(new.verdict)
    verdict = VerdictResult(
        old.verdict,
        new.verdict,
        same,
        _explained(same, fired, _VERDICT, since=_latest_claim(env03)),
    )
    by_seq = {e.seq: e for e in env04}
    old_ids = sorted(e.request_id for e in old.stops if e.request_id is not None)
    new_ids = sorted(
        rid for s in new.stops if (rid := by_seq[s].request_id) is not None
    )
    equal = old_ids == new_ids
    stops = StopsResult(
        old_ids, new_ids, equal, _explained(equal, fired, _STOPS, since=0)
    )
    return Reads(series, verdict, stops)


def _latest_claim(env03: Sequence[Envelope]) -> int:
    """The 0.3.0 seq of the log's latest ``started``, 0 for none."""
    return max((e.seq for e in env03 if e.topic == "lifecycle.started"), default=0)


# ----- Also -----------------------------------------------------------------------------

_NODES = (_COMMIT, _STOPPED)


def also(env04: Sequence[Envelope]) -> Counter[Also]:
    """§7 "Also" and Review Focus 1, over the 0.4.0 rows: each seq-valued field
    names a record of its kind, below the record that names it."""
    topic = {e.seq: e.topic for e in env04}
    out: Counter[Also] = Counter()
    exits: set[int] = set()

    def names(e: Envelope, f: str, kinds: tuple[str, ...] | None, after: Also) -> None:
        """``kinds`` None: any record."""
        v = e.body.get(f)
        if v is None or (f == "consumed_seq" and v == 0):
            return
        if not isinstance(v, int) or (
            topic.get(v) is None if kinds is None else topic.get(v) not in kinds
        ):
            out[Also.WRONG_KIND] += 1
        elif v >= e.seq:
            out[after] += 1

    for e in env04:
        if e.topic in (_COMMIT, _HEARTBEAT, _STOPPED, _DIED, _REWIND):
            names(e, "claim_seq", (_STARTED,), Also.CLAIM_AT_OR_AFTER)
        if e.topic in (_STARTED, _REWIND):
            names(e, "resume_from", _NODES, Also.RESUME_AT_OR_AFTER)
        if e.topic in _NODES:
            names(e, "parent", _NODES, Also.PARENT_AT_OR_AFTER)
            for v in e.body.get("values") or []:
                if topic.get(v) != _VALUE:
                    out[Also.WRONG_KIND] += 1
                elif v >= e.seq:
                    out[Also.LATER_VALUE] += 1
        if e.topic in (_COMMIT, _HEARTBEAT):
            names(e, "consumed_seq", None, Also.CONSUMED_AT_OR_AFTER)
        if e.topic == _STOPPED:
            claim = e.body.get("claim_seq")
            if isinstance(claim, int):
                if claim in exits:
                    out[Also.TWO_EXITS] += 1
                exits.add(claim)
    return out


# ----- one run ------------------------------------------------------------------------------


def _envelopes(rows: Sequence[Row]) -> list[Envelope]:
    return [
        Envelope(x.seq, x.topic, x.name, x.request_id, json.loads(x.body)) for x in rows
    ]


def _refused(
    base: RunRecord, step: StepName, exc: MigrationError, **more: Any
) -> RunRecord:
    refusal = Refusal(step, refusal_kind(step, str(exc)), str(exc))
    return replace(base, status=Status.REFUSED, refusal=refusal, **more)


def _crashed(base: RunRecord, stage: Stage, exc: Exception) -> RunRecord:
    return replace(base, status=Status.CRASHED, crash=_crash(stage, exc))


def _base(found: Found) -> RunRecord:
    """A run's record before anything about it is read: built without I/O, so
    the record of a crash can always be written."""
    return RunRecord(
        path=str(found.path),
        run_id=found.path.stem,
        address=found.address,
        format=found.format,
        rows=None,
        unread_sidecars=[],
        status=Status.MEASURED,
    )


class Configuration(NamedTuple):
    """What a consumer gives the runner, as ``runstate migrate`` takes it: the
    choices, parsed once over both steps, and each step's plan built from them."""

    choices: Choices
    plans: Mapping[str, Planned]  # by the format a step leaves


def configuration(choices: Choices) -> Configuration:
    return Configuration(
        choices,
        {
            s.FROM: Planned(s, resolutions(s, choices), choices.accepted, Memo())
            for s in _STEPS
        },
    )


class _Undecided(Exception):
    """A step's projection forks with no ``--resolve``: the run's later steps wait.
    ``record`` is the run's record so far, with every fork of the step, and its
    census when the step is the 0.3.0 one."""

    def __init__(self, forks: dict[str, int], record: RunRecord) -> None:
        self.forks = forks
        self.record = record


_STAGE = {
    _STEP_0_2_0.FROM: (StepName.FROM_0_2_0, Stage.FROM_0_2_0),
    _STEP_0_3_0.FROM: (StepName.FROM_0_3_0, Stage.FROM_0_3_0),
}


def _run_step(
    plan: Planned, rows: list[Row], history: History, base: RunRecord
) -> tuple[Migrated, RunRecord]:
    """One step as the detection pass runs it: its forks, then, every forking
    projection resolved, its checked transform; where each divergence fires is
    recorded, never refused."""
    step = plan.step
    detected = step.detect(
        rows, assumptions=plan.resolutions.assumptions, history=history
    )
    label = f"{step.FROM} -> {step.TO}"
    if step is _STEP_0_3_0:
        base = replace(base, census=census(rows, plan))
    counts = {p: len(f) for p, f in detected.forks.items() if f}
    forks = {**base.forks, label: counts}
    open_ = {
        p.name: counts[p.name]
        for p in step.PROJECTIONS
        if p.name in counts and plan.resolutions.choosers[p.name].spelling is None
    }
    if open_:
        raise _Undecided(open_, replace(base, forks=forks))
    migrated = transform_checked(
        step, rows, resolutions=plan.resolutions, history=history
    )
    fired = {d.name: len(migrated.divergences[d.name]) for d in step.DIVERGENCES}
    unaccepted = [d.name for d in plan.diverged(migrated)]
    return migrated, replace(
        base,
        forks=forks,
        divergences={**base.divergences, **{k: v for k, v in fired.items() if v}},
        unaccepted=[*base.unaccepted, *unaccepted],
    )


def _first_ticks(rows: Sequence[Row]) -> dict[int, int]:
    """Each claim's first pass label, as the census reads it: the step of its first
    heartbeat with an integer step, or for a claim with none (a beatless claim), the
    first nonzero integer step of its values, a step-0 value being a loading status
    or a fresh claim's bootstrap. Between its ``started`` and the next one."""
    ticks: dict[int, int] = {}
    values: dict[int, int] = {}
    claim: int | None = None
    for r in rows:
        if r.topic == _STARTED:
            claim = r.seq
        elif claim is not None and r.topic in (_HEARTBEAT, _VALUE):
            step = json.loads(r.body).get("step")
            if not observables.is_step(step):
                continue
            if r.topic == _HEARTBEAT:
                ticks.setdefault(claim, step)
            elif step != 0:
                values.setdefault(claim, step)
    return values | ticks


def census(rows: list[Row], plan: Planned) -> Census:
    """The census of the candidates ``detect`` just built and the step keeps for its
    transform (``_last``): a measurement of the step's own graph, read, never changed.
    """
    # The census reads the step's private memo, by name: no public reading gives the
    # graph detect built without building it again. ``engine.candidates`` would build
    # a second graph beside the one the step holds for its transform, doubling the
    # run's peak (1.7 GB each for the store's largest).
    held = _STEP_0_3_0._last
    assert held is not None, "detect keeps the candidates it built"
    g = held.graph
    ends = engine.end_times(g)
    datings = ends.datings()
    places = sum(1 for c, d in datings.items() if d.undated and len(ends.places(c)) > 1)
    loaded = {
        (p.claim, p.step)
        for es in g.live
        for e in es
        for p in e.pieces
        if isinstance(p, engine.PStart) and p.step is not None
    }
    ticks = _first_ticks(rows)
    hits = sorted((claim, step) for claim, step in loaded if ticks.get(claim) == step)
    reader = step04.FACTS.of(plan.resolutions.assumptions).classifier.read(rows)
    return Census(
        commits=len(datings),
        dating=sum(1 for d in datings.values() if len(d.ticks) + d.undated > 1),
        undated=sum(1 for d in datings.values() if d.undated and not d.ticks),
        places=places,
        rewinds=len(engine.reloads(g)),
        resumed=[
            Resumed(c, k, sorted(repr(v) for v in reader.started(c))) for c, k in hits
        ],
    )


def lineage_check(ch04: MemoryChannel, env04: Sequence[Envelope]) -> LineageCheck:
    """P1's reads: each must hold over the migrated log."""
    raised: list[str] = []
    disagree: list[str] = []
    try:
        head = reads.LatestClaimHead().resolve(ch04)
        reads.lineage(ch04, head=head)
        reads.count(ch04, reads.Commits(), node=None)
    except Exception as exc:
        return LineageCheck([f"lineage: {_raised(exc)}"], [])
    names = sorted({e.name for e in env04 if e.topic == _VALUE and e.name is not None})
    for name in names:
        try:
            reads.prefix_len(ch04, name, head=head)
            series = reads.series(ch04, name, head=head)
            last = reads.last_element(ch04, name, head=head)
        except Exception as exc:
            raised.append(f"{name}: {_raised(exc)}"[:300])
            continue
        if (last is None) != (not series) or (last is not None and last != series[-1]):
            disagree.append(name)
    return LineageCheck(raised, disagree)


def measure(
    found: Found,
    *,
    max_rows: int,
    max_bytes: int,
    stack: Stack,
    config: Configuration,
) -> RunRecord:
    """One run, read, migrated, read again and measured; it holds this run only.
    A step's or a read's exception is recorded as a crash of its stage; ``main``
    records any other as the runner's."""
    base = _base(found)
    try:  # THE READ: its sidecars, its size, its rows, and every body parsed
        base = replace(base, unread_sidecars=_unread_sidecars(found.path))
        if found.path.stat().st_size > max_bytes:
            return replace(base, status=Status.SKIPPED_MAX_BYTES)
        base = replace(base, rows=_count_rows(found.path))
        if base.rows is not None and base.rows > max_rows:
            return replace(base, status=Status.SKIPPED_MAX_ROWS)
        rows = _read_rows(found.path)
        _envelopes(rows)
    except (OSError, sqlite3.Error, MemoryError, ValueError) as exc:
        # a file it may not read, a corrupt log, too big a one, a body not JSON
        return _crashed(base, Stage.READ, exc)
    # what `runstate migrate` reads beside the log: a versioned log's traces in its
    # root (a legacy log's chain starts at format 0.2.0, whose entry it writes)
    traces = (
        ()
        if found.address == "legacy"
        else sqlite_traces(found.path.parent.parent, found.path.stem)
    )
    history = History((), traces)
    fmt = found.format
    rows03 = rows
    migrated04: Migrated | None = None
    while fmt != _STEP_0_3_0.TO:
        plan = config.plans[fmt]
        name, stage = _STAGE[fmt]
        if fmt == _STEP_0_3_0.FROM:
            rows03 = rows
            try:
                base = replace(base, live_by_0_3_0=_STEP_0_3_0.is_live(rows))
            except MigrationError:
                pass  # a claim with no handle: is_live cannot tell
            except Exception as exc:
                return _crashed(base, Stage.OLD_READS, exc)
        try:
            migrated, base = _run_step(plan, rows, history, base)
        except _Undecided as u:
            label = f"{plan.step.FROM} -> {plan.step.TO}"
            return replace(
                u.record, status=Status.UNDECIDED, undecided_on={label: u.forks}
            )
        except MigrationError as exc:
            return _refused(base, name, exc)
        except Exception as exc:
            return _crashed(base, stage, exc)
        history = history.then(plan.entry(base.run_id, migrated))
        rows = migrated.rows
        migrated04 = migrated if fmt == _STEP_0_3_0.FROM else None
        fmt = plan.step.TO
    assert migrated04 is not None
    del rows
    env03 = _envelopes(rows03)
    env04 = _envelopes(migrated04.rows)
    ch03 = MemoryChannel(log=env03)
    ch04 = MemoryChannel(log=env04)
    try:
        old = old_reads(ch03)
        old_cells = _old_cells(env03, old.series)
    except Exception as exc:
        return _crashed(base, Stage.OLD_READS, exc)
    try:
        verdict = new_verdict(ch04)
        try:
            new_cells = _new_cells(ch04, env03, env04)
        except observables.MalformedRecordError as exc:
            series = SeriesResult(_raised(exc), len(old_cells), 0, 0, {}, 0, [])
        else:
            series = series_result(old_cells, new_cells, migrated04.divergences)
    except Exception as exc:
        return _crashed(base, Stage.NEW_READS, exc)
    invalid = [
        InvalidRow(e.seq, e.topic, errors)
        for e in env04
        if (errors := _schema_errors(stack, e))
    ]
    checks = also(env04)
    checks[Also.SCHEMA] += len(invalid)
    return replace(
        base,
        lineage_check=lineage_check(ch04, env04),
        rows_0_4_0=len(env04),
        schema_invalid=invalid,
        also={k: checks[k] for k in Also},
        reads=compare(old, verdict, series, env03, env04, migrated04.divergences),
    )


# ----- the totals ---------------------------------------------------------------------------

NOT_COMPARED = "the new reads raise"


@dataclass
class SeriesTotals:
    runs_compared: int = 0
    runs_not_compared: Counter[str] = field(default_factory=Counter)
    cells: int = 0
    equal: int = 0
    differing: int = 0
    by_divergence: Counter[str] = field(default_factory=Counter)
    unattributed: int = 0
    runs_with_unattributed: int = 0

    def add(self, x: SeriesResult) -> None:
        if x.not_compared is not None:
            self.runs_not_compared[NOT_COMPARED] += 1
            return
        self.runs_compared += 1
        self.cells += x.cells
        self.equal += x.equal
        self.differing += x.differing
        self.by_divergence.update(x.by_divergence)
        self.unattributed += x.unattributed
        self.runs_with_unattributed += x.unattributed > 0


@dataclass
class ReadPart:
    equal: int = 0
    differing: int = 0
    by_divergence: Counter[str] = field(default_factory=Counter)

    def add(self, equal: bool, divergences: list[str]) -> None:
        self.equal += equal
        self.differing += not equal
        self.by_divergence.update(divergences)


@dataclass
class ReadsTotals:
    runs_compared: int = 0
    series: SeriesTotals = field(default_factory=SeriesTotals)
    verdict: ReadPart = field(default_factory=ReadPart)
    stops: ReadPart = field(default_factory=ReadPart)

    def add(self, x: Reads) -> None:
        self.runs_compared += 1
        self.series.add(x.series)
        self.verdict.add(x.verdict.equal, x.verdict.divergences)
        self.stops.add(x.stops.equal, x.stops.divergences)


@dataclass
class RunTotals:
    total: int = 0
    status: Counter[str] = field(default_factory=Counter)
    format: Counter[str] = field(default_factory=Counter)
    address: Counter[str] = field(default_factory=Counter)
    unread_sidecars: int = 0
    live_by_0_3_0: int = 0  # runs `runstate migrate` would refuse as live
    not_live_by_0_3_0: int = 0
    liveness_unknown: int = 0  # never reached 0.3.0, or is_live could not tell


@dataclass
class CensusTotals:
    """The census summed over the runs that reached the 0.3.0 step's detect: each
    field ``Census``'s, the resumes counted by claim and by run."""

    commits: int = 0
    dating: int = 0
    undated: int = 0
    places: int = 0
    rewinds: int = 0
    resumed_at_loaded: int = 0
    runs_resumed_at_loaded: int = 0

    def add(self, c: Census) -> None:
        self.commits += c.commits
        self.dating += c.dating
        self.undated += c.undated
        self.places += c.places
        self.rewinds += c.rewinds
        self.resumed_at_loaded += len(c.resumed)
        self.runs_resumed_at_loaded += bool(c.resumed)


@dataclass
class SchemaTotals:
    invalid_rows: int = 0
    runs_with_invalid_rows: int = 0


@dataclass
class Args:
    roots: list[str]
    out: str
    max_rows: int
    max_bytes: int
    assume: list[str]
    resolve: list[str]
    accept: list[str]
    cap: int
    budget: int
    run_on: int


@dataclass
class Totals:
    """summary.json: counters only, what crosses runs. ``complete`` is false in
    the copies written while the runner runs, and in one an abort left."""

    args: Args
    complete: bool = False
    files: FileCounts = field(default_factory=FileCounts)
    runs: RunTotals = field(default_factory=RunTotals)
    refusals: dict[str, Counter[str]] = field(default_factory=dict)  # by step
    crashes: dict[str, Counter[str]] = field(default_factory=dict)  # by stage
    # undecided runs' open forks, by step and projection; forks of every run, likewise
    undecided: dict[str, Counter[str]] = field(default_factory=dict)
    forks: dict[str, Counter[str]] = field(default_factory=dict)
    # runs each divergence fired in, and the runs no --accept would let migrate
    divergences: Counter[str] = field(default_factory=Counter)
    runs_unaccepted: int = 0
    schema: SchemaTotals = field(default_factory=SchemaTotals)
    also: Counter[str] = field(default_factory=Counter)
    reads: ReadsTotals = field(default_factory=ReadsTotals)
    # P1's reads: runs where one raised, or a last element disagreed
    lineage_raised: int = 0
    lineage_disagrees: int = 0
    # the census, summed over the runs that reached the 0.3.0 step's detect
    census: CensusTotals = field(default_factory=CensusTotals)
    seconds: float = 0.0
    peak_rss_kb: int = 0

    def add(self, x: RunRecord) -> None:
        self.runs.total += 1
        self.runs.status[x.status] += 1
        self.runs.format[x.format] += 1
        self.runs.address[x.address] += 1
        self.runs.unread_sidecars += bool(x.unread_sidecars)
        self.runs.live_by_0_3_0 += x.live_by_0_3_0 is True
        self.runs.not_live_by_0_3_0 += x.live_by_0_3_0 is False
        self.runs.liveness_unknown += x.live_by_0_3_0 is None
        if x.refusal is not None:
            self.refusals.setdefault(x.refusal.step, Counter())[x.refusal.kind] += 1
        if x.crash is not None:
            self.crashes.setdefault(x.crash.stage, Counter())[x.crash.kind] += 1
        for label, counts in x.forks.items():
            self.forks.setdefault(label, Counter()).update(counts)
        for label, counts in x.undecided_on.items():
            self.undecided.setdefault(label, Counter()).update(counts)
        self.divergences.update(x.divergences.keys())
        self.runs_unaccepted += bool(x.unaccepted)
        self.schema.invalid_rows += len(x.schema_invalid)
        self.schema.runs_with_invalid_rows += bool(x.schema_invalid)
        self.also.update({k: v for k, v in x.also.items() if v})
        if x.reads is not None:
            self.reads.add(x.reads)
        if x.lineage_check is not None:
            self.lineage_raised += bool(x.lineage_check.raised)
            self.lineage_disagrees += bool(x.lineage_check.last_disagrees)
        if x.census is not None:
            self.census.add(x.census)
        self.seconds += x.seconds or 0.0
        self.peak_rss_kb = max(self.peak_rss_kb, x.peak_rss_kb or 0)


# ----- the edge -----------------------------------------------------------------------------


def jsonable(x: object) -> Any:
    """A record or the totals as JSON values, by their declared fields. Not
    ``dataclasses.asdict``, which rebuilds a ``Counter`` from its items and so
    counts the pairs."""
    if is_dataclass(x) and not isinstance(x, type):
        return {f.name: jsonable(getattr(x, f.name)) for f in fields(x)}
    if isinstance(x, dict):
        return {k: jsonable(v) for k, v in x.items()}
    if isinstance(x, list | tuple):
        return [jsonable(v) for v in x]
    return x


# How often summary.json is rewritten while the runner runs, in runs measured,
# so an abort loses no totals. It changes no result.
_SUMMARY_EVERY = 100


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Measure the 0.3.0 -> 0.4.0 migration over real logs, read-only, "
        "under a consumer's facts, choosers and acceptances "
        "(docs/specs/commits-and-lineage.md §7; log-formats.md §6)."
    )
    p.add_argument("roots", nargs="+", type=Path, metavar="ROOT")
    p.add_argument("--out", required=True, type=Path, help="the output directory")
    p.add_argument(
        "--max-rows",
        required=True,
        type=int,
        help="skip, and count, a run with more rows than this, without reading it",
    )
    p.add_argument(
        "--max-bytes",
        required=True,
        type=int,
        help="skip, and count, a log file larger than this, without reading its rows",
    )
    for flag, metavar, what in (
        ("--assume", "MODULE:ATTR", "facts a step's writers satisfy"),
        ("--resolve", "PROJECTION=CHOOSER", "a chooser for one projection"),
        ("--accept", "DIVERGENCE", "a divergence the consumer accepts"),
    ):
        p.add_argument(flag, action="append", default=[], metavar=metavar, help=what)
    for flag, what in (
        ("--cap", "the states a step may hold at once on one run"),
        ("--budget", "the states a step may hold on one run summed over its layers"),
        ("--run-on", "labels past the log's steps an unpinned run is followed"),
    ):
        p.add_argument(flag, required=True, type=int, help=what)
    return p


def _write_summary(out: Path, totals: Totals) -> None:
    """Atomically: a reader never sees half a summary."""
    tmp = out / ".summary.json.tmp"
    tmp.write_text(json.dumps(jsonable(totals), indent=2) + "\n")
    os.replace(tmp, out / "summary.json")


def _runner_crashed(found: Found, exc: Exception) -> RunRecord:
    """A run the runner's own measurement failed on: recorded on its line, and
    the corpus run goes on."""
    base = _base(found)
    return replace(base, status=Status.CRASHED, crash=_crash(Stage.RUNNER, exc))


# The exit statuses of a child that did not send its record, on each of which the
# runner aborts: one interrupted, and one that failed sending it.
_INTERRUPTED = 130
_UNSENT = 1


def isolated(found: Found, measure_one: Callable[[], RunRecord]) -> RunRecord:
    """``measure_one()`` in a child process: the run's record, with the child's wall
    time and peak RSS. A child the OS kills is the run's crash, by its signal; one
    interrupted aborts the runner. The child never returns into the runner's frames:
    whatever it raises, it leaves by ``os._exit``, and only one that sent its whole
    record exits 0 (one that could not send it, say a ``MemoryError`` pickling it,
    is the runner's own failure, which aborts it).

    The peak RSS is the child's, which starts as a copy of the parent: it includes
    what the parent held at the fork (the interpreter, the modules and the schemas,
    about 31 MB), so it is a process's figure, not the run's increment."""
    r, w = os.pipe()
    t0 = time.perf_counter()
    pid = os.fork()
    if pid == 0:  # the child: measure, send the record, and leave at once
        os.close(r)
        code = _UNSENT
        try:
            try:
                sent = measure_one()
            except Exception as exc:
                sent = _runner_crashed(found, exc)
            with os.fdopen(w, "wb") as out:
                out.write(pickle.dumps(sent))
            code = 0
        except KeyboardInterrupt:
            code = _INTERRUPTED
        finally:  # whatever was raised, the child ends here
            os._exit(code)
    os.close(w)
    with os.fdopen(r, "rb") as inp:
        data = inp.read()
    _, status, usage = os.wait4(pid, 0)
    seconds = time.perf_counter() - t0
    record: RunRecord
    if os.WIFSIGNALED(status):
        why = f"killed by signal {os.WTERMSIG(status)}"
        record = replace(
            _base(found),
            status=Status.CRASHED,
            crash=Crash(Stage.RUNNER.value, f"the child {why}", why),
        )
    elif os.WEXITSTATUS(status) == _INTERRUPTED:
        raise KeyboardInterrupt
    elif os.WEXITSTATUS(status) == _UNSENT:
        raise RuntimeError(
            f"the child measuring {found.path} could not send its record"
        )
    else:
        record = pickle.loads(data)
    return replace(record, seconds=round(seconds, 4), peak_rss_kb=usage.ru_maxrss)


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    roots: list[Path] = [r.resolve() for r in args.roots]
    out: Path = args.out.resolve()
    for root in roots:
        if not root.exists():
            parser.error(f"ROOT {root} does not exist")
    for root in roots:
        if out == root or root in out.parents:
            parser.error(f"--out {out} is under ROOT {root}, where nothing is written")
    try:
        config = configuration(
            parse_choices(
                _STEPS,
                assume=args.assume,
                resolve=args.resolve,
                accept=args.accept,
                limits=Limits(cap=args.cap, budget=args.budget, run_on=args.run_on),
            )
        )
    except MigrationError as exc:
        parser.error(str(exc))
    out.mkdir(parents=True, exist_ok=True)
    stack = load_stack()
    totals = Totals(
        Args(
            [str(r) for r in roots],
            str(out),
            args.max_rows,
            args.max_bytes,
            args.assume,
            args.resolve,
            args.accept,
            args.cap,
            args.budget,
            args.run_on,
        )
    )
    try:
        with (
            open(out / "runs.jsonl", "w") as runs,
            open(out / "skipped.jsonl", "w") as skipped,
        ):
            for item in discover(roots, totals.files):
                if isinstance(item, Skipped):
                    totals.files.skipped[item.why] += 1
                    line = {"path": str(item.path), "why": item.why}
                    skipped.write(json.dumps(line | {"detail": item.detail}) + "\n")
                    skipped.flush()
                    continue
                found = item
                record = isolated(
                    found,
                    lambda: measure(
                        found,
                        max_rows=args.max_rows,
                        max_bytes=args.max_bytes,
                        stack=stack,
                        config=config,
                    ),
                )
                runs.write(json.dumps(jsonable(record)) + "\n")
                runs.flush()
                totals.add(record)
                del record
                if totals.runs.total % _SUMMARY_EVERY == 0:
                    _write_summary(out, totals)
        totals.complete = True
    finally:
        _write_summary(out, totals)
    return 0


if __name__ == "__main__":
    sys.exit(main())
