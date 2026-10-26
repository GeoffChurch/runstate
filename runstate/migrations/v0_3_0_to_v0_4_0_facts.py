"""The writer facts of the 0.3.0 -> 0.4.0 step: what a consumer states about its writers
(commits-and-lineage.md §7, "The writer facts").

Part of the step, under its import rule: it takes only ``Row`` from the package. The
candidate engine (``v0_3_0_to_v0_4_0_engine.py``) reads these types; a consumer's
configuration is written in them.

**The form of a fact.** A fact classifies every element of one kind, a claim or one kind
of act of its writer, into one class of a closed domain. A statement is a subset of the
domain: every such element of the writer's claims falls in it. An element falls in every
class its behaviour is consistent with: one, except where classes coincide on it, as a
fresh claim's first values read 0 under ``loaded`` and ``zero`` alike. A statement is
true when each element has a class in it. The unstated fact is the whole domain
(``every``), and the empty set says the element never occurs. Statements compose by
conjunction.

**Facts belong to a writer version**, never to a root: one ``WorkerFacts`` per worker
version and one ``ThirdPartyFacts`` per third-party writer version, keyed as the
consumer's classifier names them, since ``lifecycle.started`` names no writer.

**Every field is required.** An unstated fact is spelled ``every(...)``, never a
default.

**Names an earlier step filled.** In a log born in format 0.2.0 the 0.2.0 -> 0.3.0 step
filled every ``stopped``'s ``honored`` and every heartbeat's and ``stopped``'s
``claim_seq``, by the readings its consumer chose for ``stop-honored`` and
``record-claim`` (reference-by-name.md §6). They are that step's choices, which this
step receives (``Born020``); no fact states them.
"""

from __future__ import annotations

from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import NamedTuple, Protocol, TypeAlias, TypeVar

from . import Row

# ----- the worker facts: one statement per worker version -----------------------------


class Stride(Enum):
    """Element: each pass. Its label against the step the worker continues from."""

    # the step after the state it continues from: 0 fresh, loaded + 1, last + 1,
    # rewound + 1
    NEXT = "next"
    SKIP = "skip"  # above it


class Loading(Enum):
    """Element: each claim whose clock before its first pass labels a value it writes
    then, or fills a ``counter`` ``final_step``. What that clock reads."""

    LOADED = "loaded"  # the loaded state's step (0 when fresh)
    ZERO = "zero"  # 0, whatever it loaded


class Phase(Enum):
    """Element: each value the worker writes with a step. Where in its program it writes
    it."""

    LOADING = "loading"  # before its first pass, or in a loop that ran none
    PASS = "pass"  # inside a pass
    BETWEEN = "between"  # after a pass's close, before its next pass or its own stopped
    TEARDOWN = "teardown"  # after its own stopped


class BeatPos(Enum):
    """Element: each heartbeat with a step. Where in its pass the worker ticks."""

    OPENING = "opening"  # as its pass's first act (a tick-first loop)
    INSIDE = "inside"  # after some act of its pass, before the pass closes
    AFTER_CLOSE = "after-close"  # after the pass closed (the steps() driver)


class Covered(Enum):
    """Element: each pass the worker's loop continued past. Whether the worker ticked
    it."""

    BEATEN = "beaten"
    UNBEATEN = "unbeaten"


class Marker(Enum):
    """Element: each pass that closed neither at once after a value of it (``closing``)
    nor after a tick of its own (``beat-pos``). Whether the worker wrote a value of it
    before its close."""

    RECORDED = "recorded"
    SILENT = "silent"  # a run-on the log cannot show


class Closing(Enum):
    """Element: each pass that closed. Whether it closed at once after a value of it."""

    AT_VALUE = "at-value"
    OTHER = "other"


class LoopEnd(Enum):
    """Element: each own ``stopped`` without an error. Where the worker left its loop
    for it."""

    IN_PASS = "in-pass"  # inside an open pass
    AFTER_CLOSE = "after-close"  # after a pass's close, or before any pass


class Discards(Enum):
    """Element: each own ``stopped`` without an error, inside an open pass. Whether the
    open pass's state stays resumable."""

    KEEPS = "keeps"
    DISCARDS = "discards"  # 0.4.0 records it as an exit with ``values: null``


class FinalStep(Enum):
    """Element: each own ``stopped`` (``final-step``: without an error;
    ``error-final-step``: with one). What its ``final_step`` reports."""

    YIELDED = "yielded"  # the last pass it began
    FINISHED = "finished"  # the last pass it finished
    TICKED = "ticked"  # the last pass it ticked
    # its own counter: the last pass it finished, else its clock before its first pass
    COUNTER = "counter"


class CutSave(Enum):
    """Element: each pass cut (its worker's death, its fence, or its own ``stopped``)
    after the worker began a save of its completed state, and before its close. Whether
    that pass finished."""

    LANDED = "landed"  # once the save landed
    CLOSED = "closed"  # only at its close
    SAVE_START = "save-start"  # once the save started, landed or not


class SaveAt(Enum):
    """Element: each save the worker makes. Where in its program."""

    # before its first pass, or in a loop that ran none: the state it loaded
    BEFORE_LOOP = "before-loop"
    IN_PASS = "in-pass"  # inside a pass, of its completed state
    AT_CLOSE = "at-close"  # as a pass's last act
    BETWEEN = "between"  # after a pass's close, before its next pass or its own stopped
    PARTIAL = "partial"  # inside a pass, of its partial state
    AFTER_EXIT = "after-exit"  # after its own stopped


class Saved(Enum):
    """Element: ``save-cov``: each pass the loop continued past; ``leave-save``: the
    last closed pass at a clean exit after a close. Whether the worker saved that pass's
    completed state before its next pass (or its exit).
    """

    SAVED = "saved"
    UNSAVED = "unsaved"


class Load(Enum):
    """Element: each claim. What its worker loaded when it claimed."""

    FRESH = "fresh"  # nothing, and no claim of the run had saved
    RESTART = "restart"  # nothing, though a save existed
    NEWEST = "newest"  # the newest save the run's claims had made
    OLDER = "older"  # an older save of the run's claims
    OUTSIDE = "outside"  # a checkpoint no claim of the run saved


class Rewinds(Enum):
    """Element: each rewind: between passes, the worker reloads a state and drops what
    it wrote since its last position. Which state."""

    ROOT = "root"  # the root: a fresh state
    NEWEST = "newest"  # the newest save the run had made
    OLDER = "older"  # an older save


class Takeover(Enum):
    """Element: each later claim whose gate judged a held claim of this version dead
    while its process lived."""

    DEAD = "dead"  # its worker had halted
    # its worker still lived: the takeover's surviving worker, whom the later claim's
    # report fences
    LIVE = "live"


class Stray(Enum):
    """Element: each value no claim's worker wrote, standing while a claim of this
    version is the latest."""

    STEPPED = "stepped"
    STEPLESS = "stepless"


class Fold(Enum):
    """An encoding. Element: each claim. When its worker applies an answer written after
    its claim."""

    AT_CLAIM = "at-claim"
    AT_DRAIN = "at-drain"


class Honored(Enum):
    """An encoding. Element: each own ``stopped`` in a log born in 0.3.0. Which stops
    its ``honored`` lists."""

    EXIT = "exit"  # every stop it took in, its exit draining too
    TICKS = "ticks"  # only those its ticks drained


class Watermark(Enum):
    """An encoding. Element: each heartbeat. What its ``consumed_seq`` holds."""

    CONTROL = "control"  # the newest control record before it
    CLAIMED = "claimed"  # the newest control record since its claim, else 0
    TAIL = "tail"  # the newest record of any topic before it


@dataclass(frozen=True)
class WorkerFacts:
    """What a consumer states about one worker version. Every field is required."""

    stride: frozenset[Stride]
    loading: frozenset[Loading]
    values: frozenset[Phase]
    beat_pos: frozenset[BeatPos]
    beat_cov: frozenset[Covered]
    marker: frozenset[Marker]
    closing: frozenset[Closing]
    loop_end: frozenset[LoopEnd]
    discards: frozenset[Discards]
    final_step: frozenset[FinalStep]
    error_final_step: frozenset[FinalStep]
    cut_save: frozenset[CutSave]
    save_at: frozenset[SaveAt]
    save_cov: frozenset[Saved]
    leave_save: frozenset[Saved]
    load: frozenset[Load]
    rewinds: frozenset[Rewinds]
    takeover: frozenset[Takeover]
    strays: frozenset[Stray]
    fold: frozenset[Fold]
    honored: frozenset[Honored]
    watermark: frozenset[Watermark]


# ----- the third-party facts: one statement per third-party writer version ------------


class ClaimState(Enum):
    """The state of the claim a third party's ``stopped`` acted on, when it wrote it."""

    UNNAMED = "unnamed"  # it names none
    LIVE = "live"  # held, its worker alive
    DEAD = "dead"  # held, its worker dead
    EXITED = "exited"  # ended first by its worker's own stopped
    ENDED = "ended"  # ended first otherwise: a release, or a later claim


class Act(Enum):
    """What a third party's ``stopped`` records."""

    RELEASE = "release"  # a death report
    NOTE = "note"  # nothing ended: a note, or the clearing of the stops it honours
    REAP = "reap"  # a process's end


class Acts(NamedTuple):
    """Element: each ``stopped`` it writes: the state of the claim it acted on, and what
    it records."""

    state: ClaimState
    act: Act


class Via(Enum):
    """Element: each release it writes of a claim whose worker had exited. What it says
    there."""

    IF_HELD = "if-held"  # a death only while the claim is held, so nothing here
    ALWAYS = "always"  # the death with its cause, whatever the claim's state


class Outcome(Enum):
    """Element: each release it writes. supervisor-v0.5's outcomes."""

    PREEMPTED = "preempted"
    ERRORED = "errored"
    KILLED = "killed"
    LOST = "lost"


class ThirdFinal(Enum):
    """An encoding. Element: each ``stopped`` it writes. How it fills ``final_step``."""

    NULL = "null"
    PROGRESS = "progress"  # what runstate's ``progress()`` returned when it wrote


class ThirdHonored(Enum):
    """Element: each ``stopped`` it writes, in a log born in 0.3.0. How it fills
    ``honored``, which E2 reads."""

    NONE = "none"
    UNDISCHARGED = "undischarged"  # every stop no stopped has named yet


@dataclass(frozen=True)
class ThirdPartyFacts:
    """What a consumer states about one third-party writer version. Every field is
    required."""

    acts: frozenset[Acts]
    via: frozenset[Via]
    outcome: frozenset[Outcome]
    final_step: frozenset[ThirdFinal]
    honored: frozenset[ThirdHonored]


E = TypeVar("E", bound=Enum)


def every(domain: type[E]) -> frozenset[E]:
    """The whole domain: the fact unstated."""
    return frozenset(domain)


ALL_ACTS = frozenset(
    Acts(s, a)
    for s in ClaimState
    for a in Act
    if not (s is ClaimState.UNNAMED and a is Act.RELEASE)
)
"""Every (state, act) a conforming third party can write: a release names the claim it
reports dead."""

# ----- the classifier: consumer code that reads a record's content --------------------


class Exited(NamedTuple):
    """A process that exited with ``code``."""

    code: int


class Killed(NamedTuple):
    """A process a signal killed."""

    signal: int


class Release(NamedTuple):
    """A death report, with its outcome."""

    outcome: Outcome


@dataclass(frozen=True)
class Note:
    """Nothing ended: a note, or the clearing of the stops it honours. A dataclass, not
    a NamedTuple: a field-less NamedTuple equals every other one, ``OwnStop()``
    included."""


class Reap(NamedTuple):
    """A process's end."""

    how: Exited | Killed


RecordAct: TypeAlias = Release | Note | Reap


@dataclass(frozen=True)
class OwnStop:
    """The ``stopped`` is its claim's worker's own. A dataclass, for ``Note``'s
    reason."""


class ThirdStop(NamedTuple):
    """The ``stopped`` is third party ``writer``'s, recording ``act``."""

    writer: Hashable
    act: RecordAct


StopAnswer: TypeAlias = OwnStop | ThirdStop


class Role(Enum):
    """Where a value's writer wrote it: a worker's phase, or a writer holding no
    claim."""

    LOADING = "loading"
    PASS = "pass"
    CLOSING = "closing"  # inside a pass, which closed at once after it
    BETWEEN = "between"
    TEARDOWN = "teardown"
    STRAY = "stray"  # a writer holding no claim; always a register


class ValueRole(NamedTuple):
    role: Role
    # no node holds it: stepless, a stray, or kept outside every lineage by its writer
    register: bool


class LogReader(Protocol):
    """One log's classifier. Its state is that log's alone. One method per kind of
    record whose writer the log leaves open; each returns every answer the record
    allows: one where the content decides, the whole domain where it says nothing, never
    a default."""

    def started(self, seq: int) -> frozenset[Hashable]:
        """The versions the worker of the claim whose ``lifecycle.started`` is at
        ``seq`` may be. Several: the claim satisfies one version's whole statement,
        never a mix."""
        ...

    def stopped(self, seq: int) -> frozenset[StopAnswer]:
        """Who wrote the ``lifecycle.stopped`` at ``seq``, and what a third party's
        records."""
        ...

    def value(self, seq: int) -> frozenset[ValueRole]:
        """What the ``value`` at ``seq`` may be."""
        ...


class Classifier(Protocol):
    """Consumer code, a Strategy resolved once where the configuration is read. The
    engine hands it each log once; it keeps no state across logs."""

    def read(self, log: Sequence[Row]) -> LogReader: ...


class Configuration(NamedTuple):
    """A consumer's whole statement for one root: its facts per version, keyed as its
    classifier names them."""

    workers: Mapping[Hashable, WorkerFacts]
    third_parties: Mapping[Hashable, ThirdPartyFacts]
    classifier: Classifier


# ----- the earlier step's readings ----------------------------------------------------


class RecordClaim(Enum):
    """The 0.2.0 -> 0.3.0 step's reading of ``record-claim`` (reference-by-name.md
    §6)."""

    POSITIONAL = "positional"  # the latest claim before the record


class StopHonored(Enum):
    """The 0.2.0 -> 0.3.0 step's reading of ``stop-honored`` (reference-by-name.md
    §6)."""

    POSITIONAL = "positional"  # every stop since the previous stopped
    DRAINED = "drained"  # those the newest heartbeat of its claim had consumed
    NONE = "none"


@dataclass(frozen=True)
class Born030:
    """A log born in format 0.3.0: every name is its writer's. A dataclass: a field-less
    NamedTuple would equal ``()``."""


class Born020(NamedTuple):
    """A log born in format 0.2.0: the 0.2.0 -> 0.3.0 step filled every heartbeat's and
    ``stopped``'s ``claim_seq`` and every ``stopped``'s ``honored``, by these readings,
    which the root's manifest carries (log-formats.md §6). Such a name is that reading's
    inference, never its writer's word.
    """

    record_claim: RecordClaim
    stop_honored: StopHonored


Born: TypeAlias = Born030 | Born020
