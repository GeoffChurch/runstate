#!/usr/bin/env python3
"""The migration round's corpus runner (docs/plans/2026-10-23-migration-round.md,
Task 11): real logs through the shipped 0.2.0 -> 0.3.0 step and the new
0.3.0 -> 0.4.0 step, with P1-P4 and "Also" of docs/specs/commits-and-lineage.md
§7 measured on each, every difference attributed to a named rule.

    scripts/migration_round.py ROOT... --out DIR --max-rows N --max-bytes B

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
that is not JSON is always a crash of the read, before any step reads it. ``summary.json`` is rewritten every
``_SUMMARY_EVERY`` runs and once more however the runner stops, with
``complete`` false unless it finished.

EACH RUN:
1. Format 0.2.0 is brought to 0.3.0 by the shipped step, in memory, through
   ``transform_checked``; a refusal is recorded. 0.3.0's ``is_live``, which
   ``runstate migrate`` asks before it moves a run, is recorded as
   ``live_by_0_3_0``, and refuses nothing here.
2. The 0.3.0 -> 0.4.0 step runs once (``run_passes``), and its records are
   written as ``transform`` writes them. A refusal is recorded by its kind
   (``refusal_kind``, the one boundary that reads a step's messages), with the
   census of the 0.3.0 rows; an exception that is not a refusal is recorded as a
   crash of the step. The rest runs only on a run the step accepts.
3. Every 0.4.0 row is validated against the 0.4.0 schemas, loaded from
   ``protocol/`` here (ruling R1): the envelope's, and its convention's when 0.4.0
   owns its topic.
4. The 0.3.0 reads, ``value_series``, ``peek_terminal`` and
   ``undischarged_stops``, run over a ``MemoryChannel`` of the 0.3.0 rows; the
   0.4.0 reads, ``runstate.lineage``'s head at ``LatestClaimHead``, ``lineage``,
   ``series`` per name, ``verdict`` and ``undischarged_stops``, over one of the
   0.4.0 rows. A read that raises ``MalformedRecordError`` is a value, not a
   crash: a 0.3.0 verdict that raises is compared as one (P4), and a 0.4.0 head,
   lineage or series that raises leaves P3 uncompared, with the error and the
   old read's cell count. Any other exception of a step or a read is recorded as
   a crash of its stage, and so is a failure of the runner's own checks.
5. P1-P4 and "Also" are checked, and the census hooks counted.

THE PROPERTIES (§7, widened by the ledger's rulings, progress.md):
- **P1** (``P1Result``): every 0.3.0 heartbeat with an integer step whose
  commit's chain links back to old step 0 counts ``s + 1`` commits. A chain whose
  root is a parentless resume is counted by its class, not checked; every other
  real commit is checked, and fails as a trailing gap (``P1Failure``, the
  step's known limit), an unsound chain, or no chain at all.
- **P2** (``P2Result``): every value with a step is committed by its claim's
  commit carrying that step, by the 0.4.0 rows, except by the kinds of
  ``P2Exception``; anything else is a ``P2Failure``.
- **P3** (``P3Result``): the 0.3.0 ``value_series`` against the 0.4.0 ``series``
  of every name, cell by cell, the 0.4.0 cell of an element being its name and
  ``count(Commits(), node) - 1`` at the node that commits it. A cell is equal
  when the new read holds exactly one element there, equal in value to the old
  read's; else it differs, and each of its parts is explained by a named rule
  (``P3Class``): the old value's fate, which is why it is not the cell's only
  element, and each other element's, which is why it is there. A cell is
  reported with every class that explains a part, and ``unattributed`` when a
  part has none.
- **P4** (``P4Result``): the verdict's outcomes, 0.3.0's one outcome taken as a
  one-element set, and the stop fold's ids, each difference attributed to the
  ``P4Class`` rules that change it, or ``unattributed``. The stop fold's ids are
  compared as sets (both folds keep an id's newest send); no rule changes them,
  so a difference there is always unattributed.
- **Also** (``Also``): no node names a value at or after it, no parent at or
  after its child, no commit before the record its ``consumed_seq`` names, no
  resume point at or after its claim, every seq-valued field names a record of
  the kind it names (Review Focus 1), one exit per claim, and every row valid.

OUTPUT. ``DIR/runs.jsonl``: one ``RunRecord`` per run, as JSON, written as each
is measured. ``DIR/skipped.jsonl``: one ``{path, why, detail}`` per skipped
file, ``detail`` naming SQLite's error where there is one.
``DIR/summary.json``: the totals (``Totals``), every class counted once per
cell, verdict or record it names, and each combination of classes a cell or a
verdict carries counted under ``by_class_set``, the classes joined with ``+``.
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import re
import sqlite3
import sys
import traceback
from bisect import bisect_right
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field, fields, is_dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, NamedTuple, assert_never
from urllib.request import pathname2url

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]  # noqa: E402

from runstate import formats, observables  # noqa: E402
from runstate import lineage as reads  # noqa: E402
from runstate.channel import Envelope, MemoryChannel  # noqa: E402
from runstate.migrations import MigrationError, Row, transform_checked  # noqa: E402
from runstate.migrations import v0_3_0_to_v0_4_0 as step04  # noqa: E402
from runstate.migrations.ambiguity import parse_choices, resolutions  # noqa: E402
from runstate.migrations.v0_2_0_to_v0_3_0 import V0_2_0_to_V0_3_0  # noqa: E402
from runstate.migrations.v0_3_0_to_v0_4_0 import (  # noqa: E402
    Commit,
    CommitRef,
    Dropped,
    End,
    Input,
    Parentless,
    Passes,
    Place,
    Real,
    Reap,
    ResumeFrom,
    Synthetic,
    Uncommitted,
    _Exit,
    _Release,
    _REPORTED_LATER,
)

# The 0.2.0 -> 0.3.0 step under the readings it encoded before its projections were
# declared. The runner measures what the step writes, so no divergence is a refusal
# here: transform_checked reports where each fires and refuses none (Task 22 makes the
# choosers the runner's configuration).
_STEP_0_2_0 = V0_2_0_to_V0_3_0()
_POSITIONAL = resolutions(
    _STEP_0_2_0,
    parse_choices(
        [_STEP_0_2_0],
        assume=[],
        resolve=[
            "record-claim=positional",
            "stop-honored=positional",
            "lease-bound=first-claim",
        ],
        accept=[],
        cap=1,
    ),
)

_PROTOCOL = Path(__file__).resolve().parent.parent / "protocol"
_COLUMNS = {"seq", "topic", "name", "request_id", "body", "created_at"}
_SIDECARS = ("-wal", "-journal")  # what immutable=1 leaves unread
_VERSIONED = re.compile(r"^v\d+\.\d+\.\d+$")

_COMMIT = "lifecycle.commit"
_STOPPED = "lifecycle.stopped"
_STARTED = "lifecycle.started"
_HEARTBEAT = "lifecycle.heartbeat"
_DIED = "supervisor.died"
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


class P3Class(StrEnum):
    """The named rules a differing P3 cell traces to (§7's P3 list, widened by
    the ledger). Each is decided by a predicate below, on the run and the cell's
    part it explains:

    - ``BEYOND``, ``REVISITED`` (§7 P3): the old value is committed off the head's
      lineage, at a step past every one the lineage reached, or at one it reached
      on another branch (``_off_the_lineage``);
    - ``SEVERAL`` (§7 P3, "nodes holding several values"): the cell holds
      several elements, all one commit's (``p3``);
    - ``LEADING`` (§7 M5's last sentence; P2's by-design exception): a value
      below its claim's first real beat, committed by the claim's first commit,
      so at that commit's step (``_shift``);
    - ``PARENTLESS`` (§7 P3, M1) and ``PARENTLESS_BY_CANDIDACY`` (M1's candidacy
      rule, PROVISIONAL ruling of Task 5): the cell's chain is rooted at a
      parentless resume, so it counts too few, and with candidacy when an
      earlier claim holds the step below but not as a candidate (``_root``);
    - ``TRAILING_GAP`` (the step's known limit; ledger ruling on Task 5's
      concern 3): M5 synthesized a trailing step above a gap of steps with no
      values, so every count above it is short (``_chain``);
    - ``LOADING`` (§7 P3 "loading cells"; PROVISIONAL ruling of Task 4), ``M4``,
      ``ABOVE`` (§7 P3, "a displaced worker's values above its successor's exit";
      the ledger adds a crashed iteration's), ``ERROR_EXIT`` (§7 P3, "error
      exits") and ``UNREACHED_UNENDED`` (§7 M5: "a trailing step that no such
      claim needs stays uncommitted"; the ledger's P2 list names it for claims a
      third party released or nothing ended): the old value is left
      uncommitted, for that reason (``_left_p3``). A value left before every claim
      has no class: the ledger rules that Task 14 names it if the corpus shows
      any (Task 3), so it reads unattributed."""

    BEYOND = "beyond the head's lineage"
    REVISITED = "a step the lineage revisited"
    SEVERAL = "a node holding several values"
    LEADING = "a leading value"
    PARENTLESS = "a parentless resume"
    PARENTLESS_BY_CANDIDACY = "a parentless resume by M1's candidacy rule"
    TRAILING_GAP = "a trailing gap"
    LOADING = "loading: a claim that beat no step"
    M4 = "after its claim's end (M4)"
    ABOVE = "above its exit's last step"
    ERROR_EXIT = "an error exit's unreached trailing step"
    UNREACHED_UNENDED = (
        "an unreached trailing step of a claim a third party released or nothing ended"
    )
    UNATTRIBUTED = "unattributed"


class P4Class(StrEnum):
    """The named rules that change a verdict (§7's P4 list and the ledger's
    rulings), each decided by a predicate below (``_P4_RULES``)."""

    RELEASE = "a third party's release"
    LATER_STOP = "a third party's later stop"
    BACKSTOP_TERMINATED = "a backstop that ends no claim, now a terminated"
    CLAIMLESS = "a stopped naming no claim"
    ATTRIBUTED_DEATH = "an attributed death report"
    WRAPPER = "a wrapper's terminated"
    UNATTRIBUTED = "unattributed"


class P1Failure(StrEnum):
    TRAILING_GAP = "a trailing gap (a known limit)"
    UNSOUND_CHAIN = (
        "a chain no rule explains: its old steps do not rise by one, "
        "or its root is no parentless resume"
    )
    NO_CHAIN = "a parent at or after its child"


_PARENTLESS = frozenset({P3Class.PARENTLESS, P3Class.PARENTLESS_BY_CANDIDACY})


class P2Exception(StrEnum):
    """P2's exceptions (§7 P2, widened by the ledger: Task 4's write-up items and
    the controller's list for this task). Each is a predicate on the value: its
    commit, or why the passes left it (``_left_p2``)."""

    LEADING = "a leading value"
    ABOVE_THE_LAST_STEP = "above its exit's last step"
    ERROR_EXIT = "an error exit's unreached trailing step"
    UNREACHED_UNENDED = (
        "an unreached trailing step of a claim a third party released or nothing ended"
    )
    AFTER_ITS_END = "after its claim's end (M4)"
    BEFORE_EVERY_CLAIM = "before every claim"
    LOADING = "loading: a claim that beat no step"


class P2Failure(StrEnum):
    COMMITTED_TWICE = "committed by two nodes"
    BY_AN_EXIT = "committed by an exit"
    ANOTHER_CLAIMS = "committed by another claim's commit"
    ANOTHER_STEP = "committed at another step, not as a leading value"
    UNCOMMITTED_WITHOUT_A_REASON = "uncommitted, and the passes give no reason"


class Also(StrEnum):
    """§7's "Also", with Review Focus 1 and §2's one-exit rule."""

    LATER_VALUE = "a node names a value at or after it"
    PARENT_AT_OR_AFTER = "a parent at or after its child"
    CONSUMED_AT_OR_AFTER = "a commit at or before the record its consumed_seq names"
    RESUME_AT_OR_AFTER = "a resume_from at or after its started"
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
class LoneStop:
    """A ``stopped`` that is the only one naming its claim, with its body's
    signature (the plan's Task 13): the census reads who wrote it."""

    seq: int
    claim_seq: int
    malformed: bool  # M10's {reason, honored, claim_seq}
    error: str | None
    error_kind: str  # none, backstop or other
    completed: bool | None
    final_step_null: bool | None
    honored_empty: bool
    t_minus_created_at: float | None
    beats_after: int  # heartbeats naming its claim after it
    values_after: int  # values pass 1 gives its claim after it
    latest_claim: bool
    read_as: str | None  # how the step classified it: exit or release

    def signature(self) -> str:
        """Its body's shape, with its ``t`` bucketed against its ``created_at``
        by the step's own tolerance, ``_REPORTED_LATER`` seconds: a writer
        stamps ``t`` just before the row lands, while the reclaim tool stamps
        SLURM's end time, well before it, which the rule for lone stops reads as
        a stop written after the time it reports. The exact difference is in the
        record; the summary echoes the tolerance."""
        if self.t_minus_created_at is None:
            t = "none"
        elif self.t_minus_created_at < -_REPORTED_LATER:
            t = "before"
        elif self.t_minus_created_at > _REPORTED_LATER:
            t = "after"
        else:
            t = "near"
        final = "-" if self.final_step_null is None else self.final_step_null
        return (
            f"malformed={self.malformed} error={self.error_kind} "
            f"completed={self.completed} final_step_null={final} "
            f"honored_empty={self.honored_empty} t={t} read_as={self.read_as}"
        )


@dataclass(frozen=True)
class Census:
    """The census hooks for Task 13 and the write-up. The last three need the
    new step's passes, and are None when it refused."""

    lone_stops: list[LoneStop]
    whole_number_floats: dict[str, int]
    naks_without_request_id: int
    null_name_values: int
    beats_after_end: dict[str, int] | None
    withdrawals_of_unborne_ids: int | None
    parentless_by_candidacy: int | None


@dataclass(frozen=True)
class InvalidRow:
    seq: int
    topic: str
    errors: list[str]


@dataclass(frozen=True)
class P1Failing:
    seq: int  # the 0.3.0 heartbeat's
    step: int
    count: int | None
    kind: str


@dataclass(frozen=True)
class P1Result:
    checked: int  # real commits whose chain links back to old step 0
    passed: int
    failures: dict[str, int]
    parentless: dict[str, int]  # real commits on a parentless chain, by its class
    failing: list[P1Failing]


@dataclass(frozen=True)
class P2Failing:
    seq: int  # the 0.3.0 value's
    step: int
    kind: str


@dataclass(frozen=True)
class P2Result:
    checked: int
    at_its_step: int
    exceptions: dict[str, int]
    failures: dict[str, int]
    failing: list[P2Failing]


@dataclass(frozen=True)
class Unexplained:
    seq: int  # the 0.3.0 value's
    fate: str


@dataclass(frozen=True)
class Cell:
    """A differing cell: its classes, the old read's value (its 0.3.0 seq), and
    the new read's elements there (theirs), in lineage order."""

    name: str
    step: int
    classes: list[str]
    old: int | None
    new: list[int]
    unexplained: list[Unexplained]


@dataclass(frozen=True)
class P3Result:
    not_compared: str | None  # why not: the 0.4.0 read's error
    cells: int  # compared, or, when not, the old read's
    equal: int
    equal_other_record: int  # equal in value, from another record
    differing: int
    by_class: dict[str, int]
    by_class_set: dict[str, int]
    unattributed: int
    differing_cells: list[Cell]


@dataclass(frozen=True)
class VerdictValue:
    """A verdict as P4 compares it: none, a set of outcomes, or a raise."""

    kind: str  # none, outcomes, raised
    outcomes: list[str]
    error: str | None

    def same(self, other: VerdictValue) -> bool:
        return (self.kind, self.outcomes) == (other.kind, other.outcomes)


@dataclass(frozen=True)
class P4Verdict:
    old: VerdictValue
    new: VerdictValue
    equal: bool
    classes: list[str]


@dataclass(frozen=True)
class P4Stops:
    """Each pending stop's id, and its 0.4.0 seq (an old stop's mapped)."""

    old: dict[str, int]
    new: dict[str, int]
    equal: bool
    classes: list[str]


@dataclass(frozen=True)
class P4Result:
    verdict: P4Verdict
    stops: P4Stops


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
    census: Census | None = None
    rows_0_4_0: int | None = None
    schema_invalid: list[InvalidRow] = field(default_factory=list)
    also: dict[str, int] = field(default_factory=dict)
    p1: P1Result | None = None
    p2: P2Result | None = None
    p3: P3Result | None = None
    p4: P4Result | None = None


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
        (r"samples the subscription .*: M9 refuses it", "M9: a subscription's sample"),
        (r"is a subscription's record: M9 refuses it", "M9: a subscription's record"),
        (r"has no name, which value-v0\.3 requires", "M9: a value with no name"),
        (r"holds a step atom", "M9: a stop holding a step atom"),
        (r"an in-claim rewind \(M9\)", "M9: an in-claim rewind"),
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
        (r"without exactly one there is no handle", "a launch not one launched names"),
        (
            r"a third party's that becomes only its withdrawals",
            "a watermark on a dropped stop",
        ),
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
    return "legacy", formats._BASE


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


# ----- the passes' facts ------------------------------------------------------------------


class EndKind(StrEnum):
    """What ended a claim, as the step read it."""

    EXIT = "the worker's exit"
    ERROR_EXIT = "the worker's error exit"
    RELEASE = "a third party's release"
    ATTRIBUTED_DEATH = "an attributed death report"
    NOTHING = "nothing"


@dataclass(frozen=True)
class Facts:
    """What the passes decided for one run, by 0.3.0 seq unless named otherwise.
    The measurements read the 0.4.0 rows; these say why."""

    numbering: Mapping[int, int]  # each input record the output keeps -> its new seq
    original: Mapping[int, int]  # the inverse
    commit_at: Mapping[int, CommitRef]  # a commit's new seq -> its pass-3 identity
    commits: Mapping[CommitRef, Commit]
    committed: Mapping[int, CommitRef]  # a value -> the commit holding it
    left: Mapping[int, Uncommitted]  # a value -> why no commit holds it
    first: Mapping[int, int]  # a claim -> its first commit's old step
    beats: Mapping[CommitRef, int]  # a commit -> the beat that decides candidacy
    order: Mapping[int, int]  # a claim -> its index in claim order
    resume: Mapping[int, ResumeFrom]
    ends: Mapping[int, EndKind]

    def earlier_at(self, claim: int, step: int) -> list[int]:
        """The deciding beats of the commits at ``step`` of the claims before
        ``claim``. A commit is a candidate for ``claim`` exactly when its
        deciding beat precedes the claim's ``started`` (M1's candidacy rule): its
        own heartbeat when real, else the real heartbeat before it in its claim
        (``Synthetic.previous``), as the step's ``_beaten`` reads it. Asked only
        of a claim M1 called parentless (``_parentless``)."""
        i = self.order[claim]
        return [
            beat
            for ref, beat in self.beats.items()
            if ref.step == step and self.order[ref.claim] < i
        ]


def facts_of(passes: Passes) -> Facts:
    numbering = {k.seq: n for k, n in passes.numbering.items() if isinstance(k, Input)}
    commit_at: dict[int, CommitRef] = {}
    commits: dict[CommitRef, Commit] = {}
    committed: dict[int, CommitRef] = {}
    first: dict[int, int] = {}
    beats: dict[CommitRef, int] = {}
    for c in passes.synthesis.claims:
        claim = c.claim.started.seq
        if c.commits:
            first[claim] = c.commits[0].step
        for k in c.commits:
            ref = CommitRef(claim, k.step)
            commits[ref] = k
            match k.origin:
                case Real(beat=beat) | Synthetic(previous=beat):
                    beats[ref] = beat.seq
                case _:
                    assert_never(k.origin)
            commit_at[passes.numbering[passes.placement.commits[ref]]] = ref
            for v in k.values:
                committed[v] = ref
    ends: dict[int, EndKind] = {}
    for claim, end in passes.classification.ends.items():
        match end:
            case _Exit(stop):
                ends[claim] = EndKind.EXIT if stop.error is None else EndKind.ERROR_EXIT
            case _Release():
                ends[claim] = EndKind.RELEASE
            case _:
                assert_never(end)
    for d in passes.reports.deaths.values():
        ends[d.death.claim] = EndKind.ATTRIBUTED_DEATH
    return Facts(
        numbering=numbering,
        original={n: s for s, n in numbering.items()},
        commit_at=commit_at,
        commits=commits,
        committed=committed,
        left={x.value.seq: x.why for x in passes.synthesis.uncommitted},
        first=first,
        beats=beats,
        order={c.started.seq: i for i, c in enumerate(passes.attribution.claims)},
        resume=passes.linkage.resume_from,
        ends=ends,
    )


def _rows_0_4_0(passes: Passes) -> list[Row]:
    """The 0.4.0 rows, as ``transform`` writes the passes' records."""
    return [step04.write(o) for o in passes.outs]


# ----- chains: the count, and what shifts it ----------------------------------------------


class Chain(NamedTuple):
    """A node's chain to its root: ``commits``, its ``Commits()``; ``step``, its old
    step (an exit's is its parent's); ``root``, its root commit's old step;
    ``classes``, the named rules that put its count below its old step + 1; and
    ``sound``, whether its old steps rise by one but at trailing gaps."""

    commits: int
    step: int
    root: int
    classes: frozenset[P3Class]
    sound: bool


_TRAILING = (Place.BEFORE_A_CLEAN_EXIT, Place.RESUMED_FROM)

# The empty lineage: no commit, so a count of 0, at step -1, the step before a
# fresh chain's first. An exit with no parent (a claim that committed nothing
# and resumed from nothing) is its only node.
_EMPTY = Chain(commits=0, step=-1, root=0, classes=frozenset(), sound=True)


def _parentless(ref: CommitRef, facts: Facts) -> P3Class | None:
    """THE PARENTLESS RESUME (§7 M1; P3's "a parentless resume"): a claim's first
    commit above old step 0 with no parent counts too few. Tested here, not
    taken from M1's verdict:

    - PARENTLESS when no earlier claim holds a commit at the step below;
    - PARENTLESS_BY_CANDIDACY (M1's candidacy rule, PROVISIONAL ruling of Task
      5) when earlier claims hold one, but every one is decided by a beat at or
      after the claim's ``started``, so none is a candidate: a displaced
      worker's checkpoint (the step's known limit);
    - None when a candidate exists, or M1 did not call the claim parentless: no
      rule explains the root, and what depends on it reads unattributed."""
    if not isinstance(facts.resume[ref.claim], Parentless):
        return None
    beats = facts.earlier_at(ref.claim, ref.step - 1)
    if not beats:
        return P3Class.PARENTLESS
    if all(beat >= ref.claim for beat in beats):  # the claim's seq is its started's
        return P3Class.PARENTLESS_BY_CANDIDACY
    return None


def _root(ref: CommitRef, facts: Facts) -> frozenset[P3Class] | None:
    """A chain's root commit: at old step 0, a fresh start, which shifts no
    count; above it, a parentless resume (``_parentless``), or None when no rule
    explains it."""
    if ref.step == 0:
        return frozenset()
    cls = _parentless(ref, facts)
    return None if cls is None else frozenset({cls})


def _chain(up: Chain, ref: CommitRef, facts: Facts) -> Chain:
    """THE TRAILING GAP (the step's known limit: "M5 synthesizes a trailing step
    only where values say it was reached"): a commit more than one old step
    above its parent is sound only as a trailing step M5 synthesized."""
    gap = ref.step - up.step
    origin = facts.commits[ref].origin
    classes, sound = up.classes, up.sound
    if gap > 1 and isinstance(origin, Synthetic) and origin.place in _TRAILING:
        classes = classes | {P3Class.TRAILING_GAP}
    elif gap != 1:
        sound = False
    return Chain(up.commits + 1, ref.step, up.root, classes, sound)


def chains_of(env04: Sequence[Envelope], facts: Facts) -> dict[int, Chain]:
    """Each node's chain, by its new seq, in one forward pass: a parent precedes
    its child (§7 "Also"), and a node whose parent does not has none. A node with
    no parent starts one: a commit its own, an exit the empty chain."""
    out: dict[int, Chain] = {}
    for e in env04:
        if e.topic not in (_COMMIT, _STOPPED):
            continue
        parent = e.body.get("parent")
        up = _EMPTY if parent is None else out.get(parent)
        if e.topic == _STOPPED:
            if up is not None:
                out[e.seq] = up  # an exit adds no commit
            continue
        ref = facts.commit_at[e.seq]
        if parent is None:
            root = _root(ref, facts)
            sound = root is not None
            out[e.seq] = Chain(1, ref.step, ref.step, root or frozenset(), sound)
        elif up is not None:
            out[e.seq] = _chain(up, ref, facts)
    return out


# ----- P1 -------------------------------------------------------------------------------


def p1(
    env04: Sequence[Envelope], facts: Facts, chains: Mapping[int, Chain]
) -> P1Result:
    """§7 P1: each real commit linked back to old step 0 counts its step + 1.
    Every real commit is in one bucket: parentless (a sound chain rooted above
    old step 0, counted by its class), or checked, which is passed and failed."""
    checked = passed = 0
    failures: Counter[str] = Counter()
    parentless: Counter[str] = Counter()
    failing: list[P1Failing] = []
    for e in env04:
        if e.topic != _COMMIT:
            continue
        ref = facts.commit_at[e.seq]
        origin = facts.commits[ref].origin
        if isinstance(origin, Synthetic):
            continue
        chain = chains.get(e.seq)
        if chain is not None and chain.root > 0 and chain.sound:
            for c in chain.classes & _PARENTLESS:
                parentless[c] += 1
            continue
        checked += 1
        if chain is not None and chain.sound and chain.commits == ref.step + 1:
            passed += 1
            continue
        if chain is None:
            kind = P1Failure.NO_CHAIN
        elif not chain.sound:
            kind = P1Failure.UNSOUND_CHAIN
        else:  # a sound chain from old step 0 miscounts only across a trailing gap
            assert P3Class.TRAILING_GAP in chain.classes, f"{ref} miscounts"
            kind = P1Failure.TRAILING_GAP
        failures[kind] += 1
        count = None if chain is None else chain.commits
        failing.append(P1Failing(origin.beat.seq, ref.step, count, kind))
    return P1Result(checked, passed, dict(failures), dict(parentless), failing)


# ----- P2 -------------------------------------------------------------------------------


def _positional_claims(env03: Sequence[Envelope]) -> dict[int, int]:
    """Each value's claim by pass 1's positional rule: the latest ``started``
    before it. A value before every claim has none."""
    out: dict[int, int] = {}
    claim: int | None = None
    for e in env03:
        if e.topic == _STARTED:
            claim = e.seq
        elif e.topic == _VALUE and claim is not None:
            out[e.seq] = claim
    return out


def _holders(env04: Sequence[Envelope]) -> dict[int, list[int]]:
    """Each committed value's new seq -> the nodes whose ``values`` name it."""
    out: dict[int, list[int]] = {}
    for e in env04:
        if e.topic in (_COMMIT, _STOPPED) and isinstance(e.body.get("values"), list):
            for v in e.body["values"]:
                out.setdefault(v, []).append(e.seq)
    return out


def _unreached(end: EndKind) -> tuple[P2Exception, P3Class] | None:
    """§7 M5: "a trailing step that no such claim needs stays uncommitted", by
    what ended its claim. A clean exit vouches for its trailing steps, so none of
    its is unreached."""
    match end:
        case EndKind.ERROR_EXIT:
            return P2Exception.ERROR_EXIT, P3Class.ERROR_EXIT
        case EndKind.RELEASE | EndKind.ATTRIBUTED_DEATH | EndKind.NOTHING:
            return P2Exception.UNREACHED_UNENDED, P3Class.UNREACHED_UNENDED
        case EndKind.EXIT:
            return None
        case _:
            assert_never(end)


def _left_p2(why: Uncommitted, end: EndKind) -> P2Exception | None:
    """P2's exceptions for a value the passes left uncommitted: §7 P2's two
    (above the exit's last step; an error exit's unreached trailing step), and the
    ledger's (M4; before every claim; loading; an unreached trailing step of a
    claim a third party released or nothing ended)."""
    match why:
        case Uncommitted.ABOVE_THE_LAST_STEP:
            return P2Exception.ABOVE_THE_LAST_STEP
        case Uncommitted.AFTER_ITS_END:
            return P2Exception.AFTER_ITS_END
        case Uncommitted.BEFORE_EVERY_CLAIM:
            return P2Exception.BEFORE_EVERY_CLAIM
        case Uncommitted.LOADING:
            return P2Exception.LOADING
        case Uncommitted.UNREACHED:
            unreached = _unreached(end)
            return None if unreached is None else unreached[0]
        case Uncommitted.STEPLESS:
            return None  # a value with a step is never stepless
        case _:
            assert_never(why)


def _left_p3(why: Uncommitted, end: EndKind) -> P3Class | None:
    """P3's classes for an old value the passes left uncommitted (``P3Class``). A
    value before every claim has none yet (the ledger, Task 3)."""
    match why:
        case Uncommitted.ABOVE_THE_LAST_STEP:
            return P3Class.ABOVE
        case Uncommitted.AFTER_ITS_END:
            return P3Class.M4
        case Uncommitted.LOADING:
            return P3Class.LOADING
        case Uncommitted.UNREACHED:
            unreached = _unreached(end)
            return None if unreached is None else unreached[1]
        case Uncommitted.BEFORE_EVERY_CLAIM | Uncommitted.STEPLESS:
            return None
        case _:
            assert_never(why)


def p2(
    env03: Sequence[Envelope],
    facts: Facts,
    holders: Mapping[int, list[int]],
) -> P2Result:
    """§7 P2, read from the 0.4.0 rows: the commit naming each value with a step,
    against its positional claim and its old step."""
    claims = _positional_claims(env03)
    checked = at_step = 0
    exceptions: Counter[str] = Counter()
    failures: Counter[str] = Counter()
    failing: list[P2Failing] = []
    for e in env03:
        if e.topic != _VALUE or not observables.is_step(e.body.get("step")):
            continue
        s: int = e.body["step"]
        checked += 1
        claim = claims.get(e.seq)
        nodes = holders.get(facts.numbering[e.seq], [])
        exception: P2Exception | None = None
        failure: P2Failure | None = None
        if len(nodes) > 1:
            failure = P2Failure.COMMITTED_TWICE
        elif nodes:
            ref = facts.commit_at.get(nodes[0])
            if ref is None:
                failure = P2Failure.BY_AN_EXIT
            elif ref.claim != claim:
                failure = P2Failure.ANOTHER_CLAIMS
            elif ref.step == s:
                at_step += 1
                continue
            elif s < ref.step == facts.first[ref.claim]:
                exception = P2Exception.LEADING
            else:
                failure = P2Failure.ANOTHER_STEP
        else:
            why = facts.left.get(e.seq)
            end = (
                EndKind.NOTHING
                if claim is None
                else facts.ends.get(claim, EndKind.NOTHING)
            )
            exception = None if why is None else _left_p2(why, end)
            if exception is None:
                failure = P2Failure.UNCOMMITTED_WITHOUT_A_REASON
        if exception is not None:
            exceptions[exception] += 1
        else:
            assert failure is not None
            failures[failure] += 1
            failing.append(P2Failing(e.seq, s, failure))
    return P2Result(checked, at_step, dict(exceptions), dict(failures), failing)


# ----- P3 -------------------------------------------------------------------------------


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class Old(NamedTuple):
    """The old read's value at a cell: its 0.3.0 seq and value."""

    seq: int
    value: Any


class Element(NamedTuple):
    """A new read's element: the value's 0.3.0 seq, its 0.4.0 seq, the node that
    commits it, and its value."""

    old: int
    new: int
    node: int
    value: Any


class Part(NamedTuple):
    """What explains one part of a differing cell: the classes, and whether
    every component of the part is explained."""

    classes: frozenset[P3Class]
    explained: bool
    fate: str


class Lineage(NamedTuple):
    """The head's lineage, for the values off it: its commits' old steps, root
    first; the old steps inside its trailing gaps; its root's class; and whether
    its chain is sound."""

    nodes: frozenset[int]
    steps: tuple[int, ...]
    gaps: frozenset[int]
    root: frozenset[P3Class]
    sound: bool


class Run(NamedTuple):
    """One run's facts for P3."""

    facts: Facts
    chains: Mapping[int, Chain]
    holders: Mapping[int, list[int]]
    steps: Mapping[int, int]  # a value's 0.3.0 seq -> its old step
    claims: Mapping[int, int]  # a value's 0.3.0 seq -> its positional claim
    lineage: Lineage
    on: Mapping[int, int]  # a value on the lineage, by its new seq -> its node


def _lineage_of(
    nodes: Sequence[int], facts: Facts, chains: Mapping[int, Chain]
) -> Lineage:
    commits = [n for n in nodes if n in facts.commit_at]
    steps = tuple(facts.commit_at[n].step for n in commits)
    gaps: set[int] = set()
    for a, b in zip(steps, steps[1:]):
        gaps.update(range(a + 1, b))
    root = chains.get(commits[0]) if commits else None
    head = chains.get(commits[-1]) if commits else None
    return Lineage(
        nodes=frozenset(nodes),
        steps=steps,
        gaps=frozenset(gaps),
        root=frozenset() if root is None else root.classes,
        sound=head is None or head.sound,
    )


def _shift(run: Run, old: int, step: int, node: int) -> Part:
    """Why a value on the lineage sits at a count other than its old step:
    LEADING when its commit is its own claim's first, above it (§7 M5's last
    sentence), and the chain's classes when the count is below the commit's old
    step (``_root``, ``_chain``)."""
    ref = run.facts.commit_at[node]
    chain = run.chains[node]
    classes: set[P3Class] = set()
    explained = True
    if ref.step != step:
        own = run.claims.get(old) == ref.claim  # pass 1 gave the value this claim
        if own and step < ref.step == run.facts.first[ref.claim]:
            classes.add(P3Class.LEADING)
        else:
            explained = False
    if chain.commits - 1 != ref.step:
        if chain.sound and chain.classes:
            classes |= chain.classes
        else:
            explained = False
    fate = f"on the lineage at {chain.commits - 1}, committed by {tuple(ref)}"
    return Part(frozenset(classes), explained and bool(classes), fate)


def _off_the_lineage(run: Run, step: int) -> frozenset[P3Class]:
    """§7 P3, "cells beyond the head's lineage" and "steps the lineage
    revisited": a value committed off the head's lineage at an old step past
    every one the lineage reached is BEYOND it, and one at a step it reached on
    its own branch was REVISITED. Below a parentless root the lineage holds no
    step (``_root``), and in a trailing gap none (``_chain``)."""
    lin = run.lineage
    if not lin.sound:
        return frozenset()
    if not lin.steps or step > lin.steps[-1]:
        return frozenset({P3Class.BEYOND})
    if step in lin.steps:
        return frozenset({P3Class.REVISITED})
    if step < lin.steps[0]:
        return lin.root
    if step in lin.gaps:
        return frozenset({P3Class.TRAILING_GAP})
    return frozenset()


def _agrees(run: Run, old: int, node: int) -> bool:
    """The rows' committing node is a commit, the one the passes gave the
    value."""
    ref = run.facts.commit_at.get(node)
    return ref is not None and run.facts.committed.get(old) == ref


def _old_part(run: Run, o: Old, step: int) -> Part:
    """Why the old read's value is not the new cell's only element: where it went,
    by the 0.4.0 rows and the head's lineage, and the rule that put it there."""
    new = run.facts.numbering[o.seq]
    node = run.on.get(new)
    if node is not None:
        if not _agrees(run, o.seq, node):
            return Part(frozenset(), False, "on the lineage, against the passes")
        if run.chains[node].commits - 1 == step:
            # §7 P3, "nodes holding several values": the cell differs, so others
            # are there, and they are this commit's (``p3``)
            return Part(frozenset({P3Class.SEVERAL}), True, "at its step, with others")
        return _shift(run, o.seq, step, node)
    nodes = run.holders.get(new, [])
    if len(nodes) == 1:
        if not _agrees(run, o.seq, nodes[0]):
            return Part(frozenset(), False, "off the lineage, against the passes")
        classes = _off_the_lineage(run, step)
        ref = run.facts.commit_at[nodes[0]]
        return Part(
            classes, bool(classes), f"off the lineage, committed by {tuple(ref)}"
        )
    if nodes:
        return Part(frozenset(), False, f"committed by {len(nodes)} nodes")
    why = run.facts.left.get(o.seq)
    if why is None:
        return Part(
            frozenset(), False, "uncommitted by the rows, committed by the passes"
        )
    claim = run.claims.get(o.seq)
    end = (
        EndKind.NOTHING if claim is None else run.facts.ends.get(claim, EndKind.NOTHING)
    )
    cls = _left_p3(why, end)
    fate = f"uncommitted: {why.value} (its claim's end: {end.value})"
    return Part(frozenset() if cls is None else frozenset({cls}), cls is not None, fate)


def _new_part(run: Run, e: Element, step: int, o: Old | None, n: int) -> Part:
    """Why a new element other than the old value is at the cell: the lineage's
    own value at its old step, when the old read has a value there whose part
    says why it is not alone; else moved here (``_shift``); and, beside other
    elements, SEVERAL."""
    if not _agrees(run, e.old, e.node):
        return Part(frozenset(), False, "on the lineage, against the passes")
    classes: set[P3Class] = set()
    if run.steps[e.old] == step:
        explained, fate = o is not None, "at its own step"
    else:
        moved = _shift(run, e.old, run.steps[e.old], e.node)
        classes |= moved.classes
        explained, fate = moved.explained, moved.fate
    if n > 1:
        classes.add(P3Class.SEVERAL)  # §7 P3, "nodes holding several values"
    return Part(frozenset(classes), explained, fate)


def p3(
    run: Run,
    old_cells: Mapping[tuple[str, int], Old],
    new_cells: Mapping[tuple[str, int], list[Element]],
) -> P3Result:
    """§7 P3, cell by cell, by attribution (the module docstring).

    ONE CELL, ONE COMMIT. A lineage's commits each add one to its count, so the
    elements at one cell that pass ``_agrees`` are one commit's: an exit shares
    its parent's count, but an element an exit holds fails ``_agrees``. So a cell
    holding several elements is a node holding several values (§7 P3)."""
    equal = other_record = 0
    by_class: Counter[str] = Counter()
    by_set: Counter[str] = Counter()
    unattributed = 0
    differing: list[Cell] = []
    keys = sorted(old_cells.keys() | new_cells.keys())
    for name, step in keys:
        o = old_cells.get((name, step))
        elements = new_cells.get((name, step), [])
        if (
            o is not None
            and len(elements) == 1
            and _canonical(elements[0].value) == _canonical(o.value)
        ):
            equal += 1
            other_record += elements[0].old != o.seq
            continue
        parts: list[tuple[int, Part]] = []
        if o is not None:
            parts.append((o.seq, _old_part(run, o, step)))
        for e in elements:
            if o is None or e.old != o.seq:
                parts.append((e.old, _new_part(run, e, step, o, len(elements))))
        classes: set[str] = set()
        for _, p in parts:
            classes |= p.classes
        unexplained = [Unexplained(s, p.fate) for s, p in parts if not p.explained]
        if unexplained or not classes:
            classes.add(P3Class.UNATTRIBUTED)
            unattributed += 1
        for c in classes:
            by_class[c] += 1
        by_set["+".join(sorted(classes))] += 1
        differing.append(
            Cell(
                name,
                step,
                sorted(classes),
                None if o is None else o.seq,
                [e.old for e in elements],
                unexplained,
            )
        )
    return P3Result(
        not_compared=None,
        cells=len(keys),
        equal=equal,
        equal_other_record=other_record,
        differing=len(differing),
        by_class=dict(by_class),
        by_class_set=dict(by_set),
        unattributed=unattributed,
        differing_cells=differing,
    )


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


# ----- P4 -------------------------------------------------------------------------------


@dataclass(frozen=True)
class VerdictContext:
    """What P4's rules read: the latest claim (0.3.0 seq), the record 0.3.0's
    verdict rested on, and the passes."""

    latest: int | None
    record: Envelope | None
    passes: Passes

    def becomes(self) -> object | None:
        """What the record 0.3.0's verdict rested on became, when it is a third
        party's stop."""
        if self.record is None or self.record.topic != _STOPPED:
            return None
        report = self.passes.reports.stops.get(self.record.seq)
        return None if report is None else report.becomes


def _release(x: VerdictContext) -> bool:
    """§7, third-party stops: the first ``stopped`` naming the claim is a third
    party's release (M10's body, the runner's backstop, or what the rule for
    lone stops attributes), and becomes a ``supervisor.died``."""
    if x.latest is None:
        return False
    return isinstance(x.passes.classification.ends.get(x.latest), _Release)


def _later_stop(x: VerdictContext) -> bool:
    """§7, the later-stop rule (Review Focus 4): a ``stopped`` after the first
    naming the same claim is a third party's, a death only when its ``error`` is
    set, otherwise only its withdrawals. 0.3.0's verdict read the newest. A
    backstop's later stop is ruling c′'s, not this."""
    later = {s.stop.seq for s in x.passes.classification.later}
    return (
        x.record is not None
        and x.record.seq in later
        and not isinstance(x.becomes(), Reap)
    )


def _backstop_terminated(x: VerdictContext) -> bool:
    """Owner ruling c′: a backstop that ends no claim, later or claimless,
    becomes a ``supervisor.terminated``. A claimed run's verdict no longer rests
    on it; a never-claimed run's rests on the terminated, so a signal reads
    killed where 0.3.0 read errored."""
    if isinstance(x.becomes(), Reap):
        return True
    return x.latest is None and any(
        isinstance(r.becomes, Reap) for r in x.passes.reports.stops.values()
    )


def _claimless(x: VerdictContext) -> bool:
    """§7: a ``stopped`` naming no claim becomes only its withdrawals; a run
    whose only terminal was one loses its verdict."""
    claimless = {s.seq for s in x.passes.classification.claimless}
    return (
        x.latest is None
        and x.record is not None
        and x.record.seq in claimless
        and isinstance(x.becomes(), Dropped)
    )


def _attributed_death(x: VerdictContext) -> bool:
    """§7, attributed death reports: the claim 0.3.0 saw ended only by its
    launch's ``terminated`` gets a ``died`` from it; an exit 0 reads lost where
    0.3.0 read completed."""
    return x.latest is not None and any(
        d.death.claim == x.latest for d in x.passes.reports.deaths.values()
    )


def _wrapper(x: VerdictContext) -> bool:
    """§7: a launch whose handle differs from its claim's, compared whole, gets
    no death report (a wrapper's, or a pid on another host: the ledger), so the
    run's verdict changes from the wrapper's exit to none."""
    r = x.record
    if x.latest is None or r is None or r.topic != "launcher.terminated":
        return False
    claim = next(c for c in x.passes.log.claims if c.seq == x.latest)
    launched = [ln for ln in x.passes.log.launched if ln.launch_id == r.request_id]
    return len(launched) == 1 and launched[0].handle != claim.handle


_P4_RULES = (
    (P4Class.RELEASE, _release),
    (P4Class.LATER_STOP, _later_stop),
    (P4Class.BACKSTOP_TERMINATED, _backstop_terminated),
    (P4Class.CLAIMLESS, _claimless),
    (P4Class.ATTRIBUTED_DEATH, _attributed_death),
    (P4Class.WRAPPER, _wrapper),
)


class OldReads(NamedTuple):
    """The 0.3.0 reads: the series, the verdict and the record it rests on (the
    one ``peek_terminal`` reads, through the Watcher's own hook,
    ``_verdict_record``), the pending stops, and the latest claim."""

    series: dict[str, dict[int, Any]]
    verdict: VerdictValue
    record: Envelope | None
    stops: list[Envelope]
    latest: Envelope | None


def old_reads(ch03: MemoryChannel) -> OldReads:
    """A verdict that raises ``MalformedRecordError`` is a value (P4)."""
    try:
        record = observables._verdict_record(ch03)
    except observables.MalformedRecordError:
        record = None
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
        observables.value_series(ch03),
        verdict,
        record,
        observables.undischarged_stops(ch03),
        observables.latest_episode(ch03),
    )


class NewVerdict(NamedTuple):
    verdict: VerdictValue
    stops: list[int]


def new_verdict(ch04: MemoryChannel) -> NewVerdict:
    """A verdict that raises ``MalformedRecordError`` is a value (P4)."""
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


class NewSeries(NamedTuple):
    """The 0.4.0 reads at ``LatestClaimHead``, the head resolved once: its
    lineage, root first, and each name's series."""

    head: reads.Head
    nodes: list[int]
    series: dict[str, list[reads.Element]]


def new_series(ch04: MemoryChannel, names: set[str]) -> NewSeries:
    head = reads.LatestClaimHead().resolve(ch04)
    return NewSeries(
        head,
        reads.lineage(ch04, head=head),
        {name: reads.series(ch04, name, head=head) for name in sorted(names)},
    )


def p4(
    old: OldReads,
    new: NewVerdict,
    env04: Sequence[Envelope],
    passes: Passes,
    facts: Facts,
) -> P4Result:
    """§7 P4: the verdict's outcomes and the stop fold's ids. A differing verdict
    takes every rule of ``_P4_RULES`` that holds, or ``unattributed``; no rule
    changes the stop fold's ids. The ids are compared as sets; each is shown
    with its 0.4.0 seq, since both folds keep the newest send of an id."""
    same = old.verdict.same(new.verdict)
    classes: list[str] = []
    if not same:
        latest = None if old.latest is None else old.latest.seq
        x = VerdictContext(latest, old.record, passes)
        classes = [c for c, rule in _P4_RULES if rule(x)] or [P4Class.UNATTRIBUTED]
    verdict = P4Verdict(old.verdict, new.verdict, same, classes)
    by_seq = {e.seq: e for e in env04}
    old_stops = {
        e.request_id: facts.numbering[e.seq]
        for e in old.stops
        if e.request_id is not None
    }
    new_stops: dict[str, int] = {}
    for seq in new.stops:
        rid = by_seq[seq].request_id
        assert rid is not None, f"the pending stop at {seq} has no id"
        new_stops[rid] = seq
    equal = old_stops.keys() == new_stops.keys()
    stops = P4Stops(
        old_stops, new_stops, equal, [] if equal else [P4Class.UNATTRIBUTED]
    )
    return P4Result(verdict, stops)


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
        if e.topic in (_COMMIT, _HEARTBEAT, _STOPPED, _DIED):
            names(e, "claim_seq", (_STARTED,), Also.CLAIM_AT_OR_AFTER)
        if e.topic == _STARTED:
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


# ----- the census -----------------------------------------------------------------------

# The fields 0.3.0 pins as integers, where a whole-number float (3.0) is valid
# JSON Schema but refused by the step's strict read (progress.md, Task 8).
_INTEGER_FIELDS = {
    _HEARTBEAT: ("step", "consumed_seq", "claim_seq"),
    _STOPPED: ("final_step", "claim_seq"),
    _VALUE: ("step",),
    "launcher.terminated": ("exit_code", "signal"),
}


def _read_as(end: End) -> str:
    """How the step read a claim's end: the worker's exit, or a third party's
    release."""
    match end:
        case _Exit():
            return "exit"
        case _Release():
            return "release"
        case _:
            assert_never(end)


def _after(seqs: Sequence[int], seq: int) -> int:
    """How many of the ascending ``seqs`` are above ``seq``."""
    return len(seqs) - bisect_right(seqs, seq)


def census(
    rows03: Sequence[Row],
    env03: Sequence[Envelope],
    passes: Passes | None,
    facts: Facts | None,
) -> Census:
    """The census hooks (the controller's notes for this task): from the 0.3.0
    rows, lone stops with their signature, whole-number floats, naks without a
    request id and values without a name; from the passes, when the step ran,
    the known-limit shapes: a beat after its claim's end, a withdrawal of an id
    no stop bears, a parentless resume by candidacy."""
    floats: Counter[str] = Counter()
    naks = nameless = 0
    stops: dict[int, list[int]] = {}  # a claim -> the stoppeds naming it
    beats: dict[int, list[int]] = {}  # a claim -> its heartbeats, ascending
    values: dict[int, list[int]] = {}  # a claim -> its values (pass 1), ascending
    latest: int | None = None
    for e in env03:
        body: dict[str, Any] = e.body if isinstance(e.body, dict) else {}
        for f in _INTEGER_FIELDS.get(e.topic, ()):
            v = body.get(f)
            if isinstance(v, float) and v.is_integer():
                floats[f"{e.topic}.{f}"] += 1
        claim = body.get("claim_seq")
        named = (
            claim if isinstance(claim, int) and not isinstance(claim, bool) else None
        )
        if e.topic == "lifecycle.nak" and e.request_id is None:
            naks += 1
        elif e.topic == _VALUE:
            nameless += e.name is None
            if latest is not None:
                values.setdefault(latest, []).append(e.seq)
        elif e.topic == _STARTED:
            latest = e.seq
        elif e.topic == _STOPPED and named is not None:
            stops.setdefault(named, []).append(e.seq)
        elif e.topic == _HEARTBEAT and named is not None:
            beats.setdefault(named, []).append(e.seq)
    created = {x.seq: x.created_at for x in rows03}
    by_seq = {e.seq: e for e in env03}
    ends = {} if passes is None else passes.classification.ends
    lone: list[LoneStop] = []
    for claim, seqs in sorted(stops.items()):
        if len(seqs) != 1:
            continue
        (seq,) = seqs
        body = by_seq[seq].body
        malformed = "reason" in body and "completed" not in body
        error = body.get("error") if isinstance(body.get("error"), str) else None
        t = body.get("t")
        lone.append(
            LoneStop(
                seq=seq,
                claim_seq=claim,
                malformed=malformed,
                error=error,
                error_kind=(
                    "none"
                    if error is None
                    else "backstop" if step04._BACKSTOP.fullmatch(error) else "other"
                ),
                completed=None if malformed else body.get("completed"),
                final_step_null=None if malformed else body.get("final_step") is None,
                honored_empty=not body.get("honored"),
                t_minus_created_at=(
                    t - created[seq]
                    if isinstance(t, int | float) and not isinstance(t, bool)
                    else None
                ),
                beats_after=_after(beats.get(claim, []), seq),
                values_after=_after(values.get(claim, []), seq),
                latest_claim=claim == latest,
                # a lone stop is the first naming its claim, so its end
                read_as=None if passes is None else _read_as(ends[claim]),
            )
        )
    if passes is None or facts is None:
        return Census(lone, dict(floats), naks, nameless, None, None, None)
    end_seq = {c: end.stop.seq for c, end in passes.classification.ends.items()}
    for terminated, d in passes.reports.deaths.items():
        end_seq[d.death.claim] = terminated
    after: Counter[str] = Counter()
    for b in passes.log.beats:
        if b.seq > end_seq.get(b.claim_seq, b.seq):
            after["commit" if b.step is not None else "beat"] += 1
    borne = {r.request_id for r in passes.log.records if r.topic == "control.stop"}
    unborne = sum(
        1
        for r in passes.reports.stops.values()
        for i in r.stop.honored
        if i not in borne
    )
    by_candidacy = 0
    for claim, first in facts.first.items():
        if first > 0:
            cls = _parentless(CommitRef(claim, first), facts)
            by_candidacy += cls is P3Class.PARENTLESS_BY_CANDIDACY
    return Census(
        lone, dict(floats), naks, nameless, dict(after), unborne, by_candidacy
    )


# ----- one run ------------------------------------------------------------------------------


def _envelopes(rows: Sequence[Row]) -> list[Envelope]:
    return [
        Envelope(x.seq, x.topic, x.name, x.request_id, json.loads(x.body)) for x in rows
    ]


def _new_cells(
    new: NewSeries, ch04: MemoryChannel, facts: Facts, chains: Mapping[int, Chain]
) -> tuple[dict[tuple[str, int], list[Element]], dict[int, int]]:
    """Each element at its cell, ``(name, count(Commits(), node) - 1)``, and each
    value on the lineage, by its new seq, with its node. The count is the
    chains' (one forward pass, never a ``count`` per cell), checked against the
    lineage's running count and against ``count`` once, at the head."""
    running = 0
    for node in new.nodes:
        running += node in facts.commit_at
        assert (
            chains[node].commits == running
        ), f"the count at {node} is not the lineage's"
    assert reads.count(ch04, reads.Commits(), node=new.head.node) == running
    cells: dict[tuple[str, int], list[Element]] = {}
    on: dict[int, int] = {}
    for name, elements in new.series.items():
        for el in elements:
            at = (name, chains[el.node].commits - 1)
            cells.setdefault(at, []).append(
                Element(facts.original[el.seq], el.seq, el.node, el.value)
            )
            on[el.seq] = el.node
    return cells, on


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


def measure(found: Found, *, max_rows: int, max_bytes: int, stack: Stack) -> RunRecord:
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
        env = _envelopes(rows)
    except (OSError, sqlite3.Error, MemoryError, ValueError) as exc:
        # a file it may not read, a corrupt log, too big a one, a body not JSON
        return _crashed(base, Stage.READ, exc)
    rows03, env03 = rows, env
    if found.format == V0_2_0_to_V0_3_0.FROM:
        try:
            rows03 = transform_checked(
                _STEP_0_2_0, rows, resolutions=_POSITIONAL, history=()
            ).rows
        except MigrationError as exc:
            return _refused(base, StepName.FROM_0_2_0, exc)
        except Exception as exc:
            return _crashed(base, Stage.FROM_0_2_0, exc)
        env03 = _envelopes(rows03)  # the step's own bodies, each JSON
    del rows, env
    try:
        base = replace(base, live_by_0_3_0=step04.V0_3_0_to_V0_4_0().is_live(rows03))
    except MigrationError:
        pass  # a claim with no handle: is_live cannot tell
    except Exception as exc:
        return _crashed(base, Stage.OLD_READS, exc)
    try:
        passes = step04.run_passes(rows03)
        rows04 = _rows_0_4_0(passes)
    except MigrationError as exc:
        census03 = census(rows03, env03, None, None)
        return _refused(base, StepName.FROM_0_3_0, exc, census=census03)
    except Exception as exc:
        return _crashed(base, Stage.FROM_0_3_0, exc)
    env04 = _envelopes(rows04)
    del rows04
    ch03 = MemoryChannel(log=env03)
    ch04 = MemoryChannel(log=env04)
    names = {e.name for e in env04 if e.topic == _VALUE and e.name is not None}
    try:
        old = old_reads(ch03)
    except Exception as exc:
        return _crashed(base, Stage.OLD_READS, exc)
    try:
        verdict = new_verdict(ch04)
        series: NewSeries | str
        try:
            series = new_series(ch04, names)
        except observables.MalformedRecordError as exc:
            series = _raised(exc)
    except Exception as exc:
        return _crashed(base, Stage.NEW_READS, exc)

    invalid = [
        InvalidRow(e.seq, e.topic, errors)
        for e in env04
        if (errors := _schema_errors(stack, e))
    ]
    checks = also(env04)
    checks[Also.SCHEMA] += len(invalid)
    facts = facts_of(passes)
    chains = chains_of(env04, facts)
    holders = _holders(env04)
    old_cells = _old_cells(env03, old.series)
    if isinstance(series, str):
        result3 = P3Result(series, len(old_cells), 0, 0, 0, {}, {}, 0, [])
    else:
        new_cells, on = _new_cells(series, ch04, facts, chains)
        run = Run(
            facts=facts,
            chains=chains,
            holders=holders,
            steps={
                e.seq: e.body["step"]
                for e in env03
                if e.topic == _VALUE and observables.is_step(e.body.get("step"))
            },
            claims=_positional_claims(env03),
            lineage=_lineage_of(series.nodes, facts, chains),
            on=on,
        )
        result3 = p3(run, old_cells, new_cells)
    return replace(
        base,
        census=census(rows03, env03, passes, facts),
        rows_0_4_0=len(env04),
        schema_invalid=invalid,
        also={k: checks[k] for k in Also},
        p1=p1(env04, facts, chains),
        p2=p2(env03, facts, holders),
        p3=result3,
        p4=p4(old, verdict, env04, passes, facts),
    )


# ----- the totals ---------------------------------------------------------------------------

NOT_COMPARED = "the new reads raise"


@dataclass
class P1Totals:
    checked: int = 0
    passed: int = 0
    failures: Counter[str] = field(default_factory=Counter)
    parentless: Counter[str] = field(default_factory=Counter)

    def add(self, x: P1Result) -> None:
        self.checked += x.checked
        self.passed += x.passed
        self.failures.update(x.failures)
        self.parentless.update(x.parentless)


@dataclass
class P2Totals:
    checked: int = 0
    at_its_step: int = 0
    exceptions: Counter[str] = field(default_factory=Counter)
    failures: Counter[str] = field(default_factory=Counter)

    def add(self, x: P2Result) -> None:
        self.checked += x.checked
        self.at_its_step += x.at_its_step
        self.exceptions.update(x.exceptions)
        self.failures.update(x.failures)


@dataclass
class P3Totals:
    runs_compared: int = 0
    runs_not_compared: Counter[str] = field(default_factory=Counter)
    cells_not_compared: int = 0  # the old read's cells in those runs
    cells: int = 0
    equal: int = 0
    equal_other_record: int = 0
    differing: int = 0
    by_class: Counter[str] = field(default_factory=Counter)
    by_class_set: Counter[str] = field(default_factory=Counter)
    unattributed: int = 0
    runs_with_unattributed: int = 0

    def add(self, x: P3Result) -> None:
        if x.not_compared is not None:
            self.runs_not_compared[NOT_COMPARED] += 1
            self.cells_not_compared += x.cells
            return
        self.runs_compared += 1
        self.cells += x.cells
        self.equal += x.equal
        self.equal_other_record += x.equal_other_record
        self.differing += x.differing
        self.by_class.update(x.by_class)
        self.by_class_set.update(x.by_class_set)
        self.unattributed += x.unattributed
        self.runs_with_unattributed += x.unattributed > 0


@dataclass
class P4Part:
    equal: int = 0
    differing: int = 0
    by_class: Counter[str] = field(default_factory=Counter)
    by_class_set: Counter[str] = field(default_factory=Counter)

    def add(self, equal: bool, classes: list[str]) -> None:
        self.equal += equal
        self.differing += not equal
        self.by_class.update(classes)
        if classes:
            self.by_class_set["+".join(sorted(classes))] += 1


@dataclass
class P4Totals:
    runs_compared: int = 0
    verdict: P4Part = field(default_factory=P4Part)
    stops: P4Part = field(default_factory=P4Part)

    def add(self, x: P4Result) -> None:
        self.runs_compared += 1
        self.verdict.add(x.verdict.equal, x.verdict.classes)
        self.stops.add(x.stops.equal, x.stops.classes)


@dataclass
class CensusTotals:
    t_vs_created_at_tolerance_s: float = _REPORTED_LATER
    lone_stops: Counter[str] = field(default_factory=Counter)  # by signature
    whole_number_floats: Counter[str] = field(default_factory=Counter)
    naks_without_request_id: int = 0
    null_name_values: int = 0
    beats_after_end: Counter[str] = field(default_factory=Counter)
    withdrawals_of_unborne_ids: int = 0
    parentless_by_candidacy: int = 0

    def add(self, x: Census) -> None:
        self.lone_stops.update(s.signature() for s in x.lone_stops)
        self.whole_number_floats.update(x.whole_number_floats)
        self.naks_without_request_id += x.naks_without_request_id
        self.null_name_values += x.null_name_values
        self.beats_after_end.update(x.beats_after_end or {})
        self.withdrawals_of_unborne_ids += x.withdrawals_of_unborne_ids or 0
        self.parentless_by_candidacy += x.parentless_by_candidacy or 0


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
class SchemaTotals:
    invalid_rows: int = 0
    runs_with_invalid_rows: int = 0


@dataclass
class Args:
    roots: list[str]
    out: str
    max_rows: int
    max_bytes: int


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
    schema: SchemaTotals = field(default_factory=SchemaTotals)
    also: Counter[str] = field(default_factory=Counter)
    p1: P1Totals = field(default_factory=P1Totals)
    p2: P2Totals = field(default_factory=P2Totals)
    p3: P3Totals = field(default_factory=P3Totals)
    p4: P4Totals = field(default_factory=P4Totals)
    census: CensusTotals = field(default_factory=CensusTotals)

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
        self.schema.invalid_rows += len(x.schema_invalid)
        self.schema.runs_with_invalid_rows += bool(x.schema_invalid)
        self.also.update({k: v for k, v in x.also.items() if v})
        if x.census is not None:
            self.census.add(x.census)
        if x.p1 is not None:
            self.p1.add(x.p1)
        if x.p2 is not None:
            self.p2.add(x.p2)
        if x.p3 is not None:
            self.p3.add(x.p3)
        if x.p4 is not None:
            self.p4.add(x.p4)


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
        description="Measure P1-P4 of the 0.3.0 -> 0.4.0 migration over real logs, "
        "read-only (docs/specs/commits-and-lineage.md §7)."
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
    out.mkdir(parents=True, exist_ok=True)
    stack = load_stack()
    totals = Totals(
        Args([str(r) for r in roots], str(out), args.max_rows, args.max_bytes)
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
                try:
                    record = measure(
                        item,
                        max_rows=args.max_rows,
                        max_bytes=args.max_bytes,
                        stack=stack,
                    )
                except Exception as exc:
                    record = _runner_crashed(item, exc)
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
