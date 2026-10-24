"""Format 0.3.0 -> 0.4.0: commits and lineage (docs/specs/commits-and-lineage.md §7).

Format 0.3.0 numbered a run's iterations by the ``step`` of its heartbeats and
values; 0.4.0 records them as nodes (lifecycle-v0.6, supervisor-v0.5,
value-v0.3, subscription-v0.4). This step writes the nodes, and **every
inference copies what the old positional reading implied**, the standard of
the 0.2.0 -> 0.3.0 step. An inference is labelled as one.

It is the only step that renumbers, and the last that may (log-formats.md §6):
it moves commits and inserts records, so the runner does not hold it to the
seq-and-topic preservation every other step keeps. From 0.4.0 on, checkpoints
hold seqs.

It reads a run in six passes (§7), each a function from the parsed log and the
earlier passes' results to a frozen result, then writes the rows.
``run_passes`` runs them, in this order, and nothing else does:

1. **Claim attribution** (``attribute``): which claim each heartbeat and each
   value belongs to.
2. **Classification** (``classify``): who wrote each ``lifecycle.stopped``, and
   what it ends; then what each third party's becomes, and which claim a
   launch's ``terminated`` reports dead (``report``).
3. **Synthesis** (``synthesize``): M2, M4 and M5 assign values to commits or
   leave them, synthesizing the commits M5 names, with M8's fields.
4. **M1** (``link``): parents and ``resume_from``.
5. **M6** (``place``, then ``renumber``): the moves and the withdrawals, then
   renumbering.
6. **M7** (``rename``): the renames.

``write`` then writes each record's 0.4.0 row.

The rules:

- **Attribution.** A heartbeat belongs to the claim its ``claim_seq`` names, as
  every 0.3.0 read of it did. A value names none, so it belongs to the latest
  claim before it: the positional rule the 0.2.0 -> 0.3.0 step stated for
  episodes, an inference. A value before every claim belongs to none.
- **Classification.** The first ``stopped`` naming a claim is the claim's
  **end**, one of two:
  - **a third party's release,** when it is the reclaim tool's malformed body
    (M10), or the runner's hard-crash backstop, identified by its error text,
    "subprocess exited N with no terminal record": the backstop is a third
    party's wherever it falls;
  - otherwise the rule for lone stops decides, from the log alone: **the
    worker's exit**, as the old read implied, unless the log contradicts
    that, which makes it **a third party's release**.

  Every later ``stopped`` naming the same claim is **a third party's later
  stop**: the claim has already ended. A ``stopped`` naming no claim is
  **claimless**, whatever its shape, the backstop's included: a death report
  must name a claim.
- **Third parties' stops** (``report``; review §12, item 14; owner ruling c′).
  A release becomes a ``supervisor.died``: ``errored`` when its ``error`` is
  set and ``preempted`` otherwise, a completed one included, since a death is
  never a completion, with the ``error`` as the evidence, else
  "migrated: third-party stopped"; M10's body is ``preempted``, its
  ``reason`` the evidence. A later stop becomes a death only when its
  ``error`` is set, and otherwise only its withdrawals, so the repair
  script's copy of a release and the "clear staged stop" default body write no
  death. A claimless stop becomes only its withdrawals. **The backstop that
  ends no claim**, later or claimless, becomes a ``supervisor.terminated``:
  the runner's report of its subprocess's end, ``exited`` with the N its text
  names, or ``killed`` by signal -N when N is negative, under a launch id
  minted ``backstop@<seq>``, fresh against every request id in the run.
  Whatever a third party's stop becomes, each id it honored becomes a
  ``control.withdraw`` just after it, so the stops stay spent; a stop that
  becomes nothing else leaves the log, its withdrawals taking its place.
  Every record a stop becomes carries its ``t``, or, for M10's body, which
  has none, its ``created_at``.
- **Attributed death reports** (``report``; review §12, items 13 and 14). A
  claim that 0.3.0 saw ended only by its launch's ``launcher.terminated`` gets
  a ``supervisor.died`` from it, placed just after it, when the launch's
  handle is the claim's: the same ``local://host/pid``, compared whole, since
  two hosts can share a pid. The launch is the one the claim names, by the
  launch id its ``started`` re-emitted, and its handle is its
  ``launcher.launched``'s, found by that id anywhere in the log, since a child
  can claim before its parent writes ``launched``. The report mirrors the live
  reap (§3): a signal is ``killed``, a nonzero exit code ``errored`` and an exit
  0 ``lost``, with ``signal s`` or ``exit n`` as the evidence and the
  ``terminated``'s ``t``.

  Only a claim no ``stopped`` names can get one, and only the newest claim
  naming its launch, since §3's live launcher reports only for that claim. An
  older claim of the same launch stays as 0.3.0 left it, with no report; only
  the latest claim enters the verdict. Only a ``terminated`` after the claim
  ends it, since 0.3.0 read only the records after a claim. A launch whose
  handle differs, a wrapper's or another host's, gets none. The
  ``terminated`` a ``died`` is attributed from is its claim's end, as a
  ``stopped`` naming it would be, since a death report ends its claim (§2):
  a value after it is after its claim's end (M4).
- **Lone stops** (``_lone_stop_rule``; the implementation round's census of
  who wrote them). The first ``stopped`` naming a claim, well-formed and not
  the backstop, is the worker's exit, which is what 0.3.0 defined the topic to
  mean, unless the log contradicts that. It is a third party's release when
  either:
  - **its claim beats after it:** a heartbeat naming the same claim has a
    higher seq. The worker outlived the stop, so the stop is not its exit;
  - **it was written after the time it reports:** its ``t`` precedes its
    record's ``created_at`` by more than ``_REPORTED_LATER`` (60 s). A worker
    reporting its own exit stamps both from one clock at one moment:
    SqliteChannel stamps ``created_at`` with ``time.time()`` at insert, behind
    a 5 s ``busy_timeout``. A later reporter dates the stop to an event it
    learned of afterwards, as mycooc's reclaim tool does with SLURM's end
    time.

  So every lone stop is attributed. Where the log does not contradict a stop,
  nothing in it tells a third party's from the worker's own, and reading it as
  the worker's is what the topic means (a known limit below).
- **Synthesis.** Each heartbeat with an integer step is a commit. A value with
  one goes to a commit of its own claim, never another's:
  - **M2:** the commit carrying its step, even when the value follows that
    beat;
  - **leading values,** below the claim's first real beat, go to its first
    commit;
  - **a claim that beat no integer step reaches no step,** the reading the
    migration takes: its integer-step values are left as loading, whether it
    wrote them while it loaded or in a step it never beat, even at or below a
    clean exit's last step, and nothing is synthesized in it. A trailing
    value is one above a claim's last real beat. The reading replaces one
    that inferred a beatless claim's position from the claims before it,
    which had two self-consistent answers wherever the claim's values sat
    above the inferred step: the third place could synthesize in it or in
    the claim it was inferred from. M1's ``resume_from`` for such a claim
    (pass 4) reads the commits after synthesis, as §7's pass order says;
  - **M5** synthesizes a commit in exactly three places (``Place``): a tick
    skipped between two real beats, even one with no values; a trailing step,
    above the claim's last real beat, at or below a non-error exit's last
    step; and a trailing step s, whatever ended its claim, of the newest
    claim before a later claim whose first real beat is s+1, in claim order,
    to reach step s, by a commit or by trailing values (``_newest_reaching``,
    which M1 reuses among its candidates). It is the claim M1 will choose
    wherever its commit at s is a candidate, as it is unless a displaced
    worker beat after the later claim began (a known limit).

  "The exit's last step" is the 0.3.0 ``final_step``; a null one reached no
  step. Only an exit node has one: a claim a third party released, or that
  nothing ended, has no last step to be above. Every other value is left
  uncommitted, with the first reason that holds (``Uncommitted``): stepless,
  a register (M3); before every claim; after its claim's end (M4), whatever
  ended it, the end being the first ``stopped`` naming the claim or the
  ``terminated`` a ``died`` is attributed from; a claim's that beat no step
  (loading); above its claim's exit's last step; or a trailing step no exit
  vouches for, which M5's third place does not reach.
- **The exit node.** The worker's exit holds ``[]``, or null when it errored:
  it discarded its open iteration. A third party's release becomes a death,
  not an exit node.
- **M8.** A synthetic commit takes ``t`` from the last value it commits that
  carries one, else from the real heartbeat before it in its claim; and
  ``consumed_seq`` from that heartbeat. M5 synthesizes only above a claim's
  first real beat, so that heartbeat always exists, and M8's further
  fallbacks never apply. It is worked out once and carried on the commit
  (``Synthetic.previous``), the one fact M8, candidacy and placement read.
- **M1** (``link``) reads the commits after synthesis, in claim order, never
  by step nearness, and chooses a claim's resume point among its
  **candidates**: the commits placed below its ``started``, which only a
  beat can decide (``_beaten``): a real commit's own heartbeat, a synthetic
  one's real heartbeat before it in its claim. A claim's later
  commits take its previous commit as ``parent``. Its first commit, at old
  step s, takes the candidate at s-1 of the newest claim before it to hold
  one; at step 0 it is a fresh start, and with no candidate at s-1 its parent
  is null, a parentless resume. ``started.resume_from`` is the first commit's
  parent, and a claim that beat no integer step takes, as an inference, the
  newest candidate of the newest claim before it to hold one. Each rule is a
  ``ResumeFrom`` variant, so a report can name it. A commit is the parent
  even where an extendable exit follows it. The exit node's ``parent`` is the
  claim's position: its newest commit, else its resume point.
- **M6** (``place``), the reading the migration takes: a commit goes just
  after the latest of the records it must follow: its last committed value,
  its parent, the record its ``consumed_seq`` names and, for a real commit,
  its own heartbeat. §7 lists the first three; the heartbeat is added, so a
  commit only moves forward. A commit asserts that its values are complete,
  so it moves past a value it commits that follows its heartbeat (M2's
  post-loop values), and past a parent that moved so; a node is never placed
  before the event that recorded it. With the heartbeat added, every commit
  has a place: a real commit's heartbeat follows its claim's ``started``, and
  a synthetic commit's parent is a commit of its own claim. A commit is then
  placed below a later claim's ``started`` exactly when M1 counts it a
  candidate. A real commit is its heartbeat's record, moved; a synthetic one
  is new. The commits are placed in claim order, each claim's in its order,
  so a parent is placed before its child, and M6 places at most one record
  after any record. Then every record is renumbered once (``renumber``): a
  pass that adds records places them through the same ``After``, just after
  a record, and the one renumbering resolves them all.
- **The records** (``write``): ``started`` gains ``resume_from`` and
  ``sources: []``; a commit carries ``fired: []`` and ``expired: []``; a
  stepless heartbeat drops ``step``; the exit node drops ``final_step`` and
  carries ``parent``, ``values``, ``fired: []`` and ``expired: []``; a value
  drops ``step``; ``launcher.*`` records move to ``supervisor.*``. A record
  whose body 0.4.0 keeps is written with its body's stored text.
- **M7** (``rename``): every seq-valued field the 0.4.0 schemas define is
  renumbered to name the record it named: ``claim_seq`` (on a heartbeat, a
  commit, an exit and a ``died``), ``consumed_seq``, ``parent``, ``values``
  and ``resume_from``. A ``consumed_seq`` is a drain watermark, a read
  position: 0 names nothing and stays 0, and otherwise it names a record
  before its heartbeat that M6 never moves, so renaming it keeps the records
  it covers. Request ids are strings, so a ``stop@N`` stays as it is. Each
  0.3.0 record keeps its ``created_at``; a record the step adds takes that of
  the record it is placed after.

**Refused**, each naming the seq. ``parse`` reads the records in seq order
and refuses the first it cannot carry; a refusal that needs the whole log
(the attributed death report's, and a watermark naming a stop that leaves it)
names the lowest seq its pass refuses. Since the step writes a record whose
body 0.4.0 keeps with its stored text, it reads every record at the boundary
as 0.3.0 pins it, so an input it accepts never yields a row 0.4.0 rejects. It
reads a body strictly: an integer only as a JSON integer, so it refuses a
whole-number float such as 3.0, which 0.3.0's schemas accept. It refuses:

- a record envelope-v0.2 rejects: an empty topic, name or request id, or a
  body that is not an object;
- **a ``stopped`` whose ``honored`` holds the empty id,** valid 0.3.0, whose
  honored items are bare strings. The empty id names no stop, since
  envelope-v0.2 forbids an empty request id, and 0.4.0 cannot carry it: a
  third party's stop would become a withdrawal under it, which envelope-v0.2
  rejects, and a worker's exit would keep it in ``honored``, whose items
  lifecycle-v0.6 holds non-empty. ``parse`` refuses it before it can tell an
  exit from a third party's, and it honors nothing either way;
- a topic under a prefix 0.3.0 reserves (``lifecycle.``, ``control.``,
  ``launcher.``) that 0.3.0 does not define, as a ``control.withdraw``, a
  ``lifecycle.commit`` or a ``lifecycle.rewind`` would be;
- **a user's topic under ``supervisor.``,** valid 0.3.0, which left the
  prefix to users: 0.4.0 reserves it, and supervisor-v0.5 rejects any topic
  there but ``launched``, ``terminated`` and ``died``;
- what M9 refuses:
  - **a subscription's every record:** a ``control.subscribe``, a
    ``control.unsubscribe``, a ``lifecycle.bound``, which binds a lease to a
    claim, and a sample, a value bearing a ``request_id``. Refusing the
    ``bound`` too keeps every lease out of the passes, whether or not its
    subscribe is in the log;
  - **a ``control.stop`` whose ``from`` holds a step atom,** at any depth,
    which subscription-v0.4 removed;
  - **an in-claim rewind:** a heartbeat at or below an integer step its claim
    already beat, as two heartbeats of one step or a step below an earlier
    one. A 0.4.0 writer records a rewind, and nothing in 0.3.0 says where one
    resumed;
  - **a value with a null ``name``,** which value-v0.3 cannot hold; an empty
    one the envelope refuses;
- a ``lifecycle.started``, ``lifecycle.heartbeat``, ``lifecycle.nak`` or
  ``value`` whose body is not format 0.3.0's (lifecycle-v0.5, value-v0.2):
  the step writes 0.4.0 from the fields 0.3.0 pins, and such a body may lack
  them;
- a ``control.stop`` with no request id, or whose body is not
  subscription-v0.3's StopTrigger;
- a ``launcher.*`` record with no launch id, or whose body is not
  launcher-v0.4's, its ``exited`` with an exit code and no signal, its
  ``killed`` with a signal and no exit code;
- **a second ``terminated`` of any launch,** whatever claims there are. A
  process ends once, and no reference launcher writes two: duplicates are a
  writer's bug, and they may disagree, and a run nothing claimed takes its
  launch's ``terminated`` as its verdict (§5), which two make ambiguous;
- a ``lifecycle.stopped`` whose body is neither 0.3.0's nor M10's
  ``{reason, honored, claim_seq}``: what it ends, and who wrote it, cannot be
  told;
- a heartbeat or ``stopped`` whose ``claim_seq`` names no claim before it: no
  0.3.0 writer wrote one, and renumbering would point it at whatever record
  took that seq;
- a heartbeat whose nonzero ``consumed_seq`` names no record before it: no
  0.3.0 writer wrote one, and renumbering cannot carry it;
- a heartbeat whose ``consumed_seq`` names a heartbeat with an integer step:
  lifecycle-v0.5 defines the watermark as a read position in the control
  order, and the reference Worker sets it only to a control record's seq.
  Such a heartbeat is a commit M6 may move, and a watermark renamed to a moved
  record would cover records it never covered, as a stop it had not drained,
  or precede the record it names. A watermark naming a value or any other
  record M6 never moves is read as it is;
- an envelope field 0.4.0 pins null that 0.3.0 left open and that holds a
  value: a lifecycle record's or a stop's ``name``, a heartbeat's or a
  ``stopped``'s ``request_id``. No 0.3.0 reference writer set one, and the
  step cannot carry it;
- a heartbeat whose ``consumed_seq`` names a third party's stop that becomes
  only its withdrawals: it leaves the log, so renaming cannot carry the
  watermark;
- **a launch that not exactly one ``launched`` names,** where an attributed
  death report reads it: its ``terminated`` follows the newest claim naming
  it, which no ``stopped`` ended. Without one there is no handle to attribute
  the death by, and writing no ``died`` would leave the claim open forever in
  0.4.0. Elsewhere nothing needs the handle, and the ``terminated`` is
  carried.

**Known limits:**

- A backstop's ``terminated`` names a launch no ``launched`` record names: the
  runner wrote no launch record, and its id is minted.
- Each id a third party's stop honored is withdrawn, even one another record
  already spent: a second withdrawal of a spent request changes nothing.
- A third party's stop that honors an id no stop bears becomes a withdrawal of
  a request that never existed. It is harmless: a withdrawal spends a request,
  and there is none to spend.
- A displaced worker's values are attributed to its successor's claim, because
  the positional rule did so, and the log does not record which worker wrote
  them. Its heartbeats name its own claim and keep it. Above its successor's
  exit's last step such a value stays uncommitted, but a successor with no exit
  node has no last step, so the value counts as that claim reaching its step.
- M5 synthesizes a trailing step only where values say it was reached. A
  trailing step above a gap of steps with no values leaves the gap without
  commits, so a claim that resumes from above it counts too few (P1). A
  0.3.0 worker's trailing values sit one step above its last beat.
- A value before every claim is no claim's, so no lineage holds it, where
  0.3.0's ``value_series`` read it.
- A third party's lone stop that the log does not contradict reads as its
  claim's exit: a default body, a ``t`` within ``_REPORTED_LATER`` of its
  ``created_at``, and no beat of its claim after it. It then vouches for its
  claim's iteration, committing the claim's pending values up to its
  ``final_step``. The writers that can produce one are the runner's
  backstop's ``error: null`` variants, mycooc's ``resume_fanout`` on an
  unstopped claim, and the reclaim tool before ``ffe05f6e``, which dated its
  stop when it wrote it. The local corpus holds none of them.
- A beat after its claim's end, whoever ended it, still commits. M4 leaves a
  value after the end uncommitted, but a heartbeat with an integer step is a
  commit wherever it falls: after a third party's release, or the
  ``terminated`` a death is attributed from, it is a commit placed after the
  ``died``. A released stop's claim beating after it is one such case, the
  beat being what makes the stop a release. Whether such a beat should commit
  nothing, as such a value does, is the owner's decision.
- A claim that beat no integer step reaches no step, even one that finished
  a whole step and checkpointed it before it crashed: nothing is synthesized
  in it, so the next claim's first commit may be a parentless resume, which
  P3 names. One whose exit is clean gets no commit either: its step values at
  or below its exit's last step, which the exit vouches for, are left as
  loading, not synthesized.
- A claim that beat no integer step resumes, by inference, from the newest
  candidate before it: the log does not say which checkpoint it loaded.
- A claim that loaded a checkpoint a displaced worker saved after the claim
  began has no candidate at the step below its first: its first commit is a
  parentless resume, which P3 names. A 0.3.0 worker claimed and then loaded,
  where a 0.4.0 worker loads first, and ``resume_from`` must name a node below
  its ``started``.
- M5's third place and M1 can choose different claims. The third place asks
  which claim before a resuming one reached step s at all; M1 asks only among
  the candidates. They differ only where a displaced worker beat after the
  resuming claim began. M1 then takes an older claim's candidate at s, or
  none, a parentless resume. The third place stays unfiltered: it decides
  only whether a trailing step finished, and moving the synthesis to an older
  claim would credit it with a checkpoint the displaced worker may have
  saved. It errs, as M4 does, towards committing too little. The third place
  synthesizes a commit M1 then passes over only when values pass 1 gives its
  claim precede the resuming claim while that claim's own beat below them
  follows it, which takes a second displaced worker's values. M1 passes it
  over only for that resuming claim: a later claim begun after the beat
  before it, whose first real beat is s+1, can still resume from it. Either
  way M1 errs towards a parentless resume, which P3 names, never towards a
  resume point placed after its claim.
"""

from __future__ import annotations

import json
import re
from bisect import bisect_right
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar, NamedTuple, TypeGuard, assert_never

from ..vocabulary.handle import resolve
from . import MigrationError, Row

# ----- format 0.3.0's record semantics, frozen here -----------------------------
#
# The step is retained forever, so it must read 0.3.0 and write 0.4.0 as they
# were defined when it was written, whatever later code does (log-formats.md
# §6: a step carries what it needs of its old format). It imports no record
# semantics from the package: not Envelope, not Topic, not the payloads, not
# runstate.lineage. From outside it takes only Row and MigrationError, and
# resolve(), an OS probe of a handle rather than a format rule.

_Body = dict[str, Any]


@dataclass(frozen=True)
class _Record:
    """One record with its body parsed: a 0.3.0 envelope (envelope-v0.2), its
    body's stored text, which a record whose body 0.4.0 keeps is written with
    byte for byte, and the time it was written."""

    seq: int
    topic: str
    name: str | None
    request_id: str | None
    body: _Body
    text: str
    created_at: float


class _Topic(StrEnum):
    """The topics format 0.3.0 defines, as it spelled them (lifecycle-v0.5,
    subscription-v0.3, launcher-v0.4, value-v0.2)."""

    STARTED = "lifecycle.started"
    HEARTBEAT = "lifecycle.heartbeat"
    STOPPED = "lifecycle.stopped"
    NAK = "lifecycle.nak"
    BOUND = "lifecycle.bound"
    STOP = "control.stop"
    SUBSCRIBE = "control.subscribe"
    UNSUBSCRIBE = "control.unsubscribe"
    LAUNCHED = "launcher.launched"
    TERMINATED = "launcher.terminated"
    VALUE = "value"


@dataclass(frozen=True)
class _Started:
    """A claim (lifecycle-v0.5 ``started``), named by its seq. ``launch_id`` is
    its envelope ``request_id``: the id of the launch it answers, which the
    worker re-emits (launcher-record-identity.md), or None when no launcher
    minted one."""

    seq: int
    handle: str
    t: float
    launch_id: str | None


@dataclass(frozen=True)
class _Beat:
    """A 0.3.0 heartbeat (lifecycle-v0.5): a beat when ``step`` is null, and
    otherwise the end of iteration ``step``."""

    seq: int
    step: int | None
    consumed_seq: int
    claim_seq: int
    t: float


@dataclass(frozen=True)
class _Value:
    """A value (value-v0.2): its envelope ``name``, which M9 holds to a name
    value-v0.3 can hold, and its body."""

    seq: int
    name: str
    value: Any
    step: int | None
    t: float | None


@dataclass(frozen=True)
class _Stopped:
    """A ``stopped`` as lifecycle-v0.5 pins it, and its record's ``created_at``,
    which the rule for lone stops compares with the ``t`` it reports."""

    seq: int
    completed: bool
    error: str | None
    final_step: int | None
    claim_seq: int | None
    honored: tuple[str, ...]
    t: float
    created_at: float


@dataclass(frozen=True)
class _Malformed:
    """A ``stopped`` in the one malformed shape the step reads (M10): mycooc's
    reclaim tool wrote ``{reason}``, a third party's release, and the 0.2.0 ->
    0.3.0 step named what it ended and honored. It carries no ``t``, so its
    record's ``created_at`` is the only time it has."""

    seq: int
    reason: str
    honored: tuple[str, ...]
    claim_seq: int | None
    created_at: float


_Stop = _Stopped | _Malformed


@dataclass(frozen=True)
class Exited:
    """A process that exited with ``code``."""

    code: int


@dataclass(frozen=True)
class Killed:
    """A process a signal killed."""

    signal: int


@dataclass(frozen=True)
class _Launched:
    """A ``launcher.launched`` (launcher-v0.4): the launch its envelope
    ``request_id`` names, and the handle of the process it spawned."""

    seq: int
    launch_id: str
    handle: str


@dataclass(frozen=True)
class _Terminated:
    """A ``launcher.terminated`` (launcher-v0.4): the launch its envelope
    ``request_id`` names, how its process ended, and ``t``, its reaper's
    clock."""

    seq: int
    launch_id: str
    how: Exited | Killed
    t: float


# Each body the step reads, as its schema pins it: every key, and the test its
# value passes. The schemas set additionalProperties: false, so a body holds
# exactly these keys.

_Test = Callable[[object], bool]


def _is_int(v: object) -> TypeGuard[int]:
    """A JSON integer: an int, bool excluded (JSON ``true`` is not 1)."""
    return isinstance(v, int) and not isinstance(v, bool)


def _is_seq(v: object) -> bool:
    return _is_int(v) and v >= 1


def _is_count(v: object) -> bool:  # a step, a consumed_seq
    return _is_int(v) and v >= 0


def _is_time(v: object) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool) and v >= 0


def _is_str(v: object) -> bool:
    return isinstance(v, str)


def _is_bool(v: object) -> bool:
    return isinstance(v, bool)


def _is_ids(v: object) -> bool:
    """Unique request ids."""
    return (
        isinstance(v, list)
        and all(isinstance(i, str) for i in v)
        and len(set(v)) == len(v)
    )


def _is_json(v: object) -> bool:
    return True


def _nullable(test: _Test) -> _Test:
    return lambda v: v is None or test(v)


_STARTED: dict[str, _Test] = {"handle": _is_str, "t": _is_time}
_HEARTBEAT: dict[str, _Test] = {
    "step": _nullable(_is_count),
    "consumed_seq": _is_count,
    "claim_seq": _is_seq,
    "t": _is_time,
}
_VALUE: dict[str, _Test] = {
    "value": _is_json,
    "step": _nullable(_is_count),
    "t": _nullable(_is_time),
}
_STOPPED: dict[str, _Test] = {
    "completed": _is_bool,
    "error": _nullable(_is_str),
    "final_step": _nullable(_is_count),
    "claim_seq": _nullable(_is_seq),
    "honored": _is_ids,
    "t": _is_time,
}
_MALFORMED: dict[str, _Test] = {
    "reason": _is_str,
    "honored": _is_ids,
    "claim_seq": _nullable(_is_seq),
}
_LAUNCHED: dict[str, _Test] = {
    "handle": _is_str,
    "status": lambda v: v == "running",
    "t": _is_time,
}
_TERMINATED: dict[str, _Test] = {  # with reason-coupled exit_code and signal
    "reason": lambda v: v in ("exited", "killed"),
    "exit_code": _nullable(_is_count),
    "signal": _nullable(_is_seq),
    "t": _is_time,
}
_NAK: dict[str, _Test] = {
    "reason": lambda v: v in ("malformed", "unsatisfiable", "unsupported"),
    "message": _is_str,
}


def _fits(body: _Body, shape: dict[str, _Test]) -> bool:
    return body.keys() == shape.keys() and all(
        test(body[k]) for k, test in shape.items()
    )


def _unreadable(r: _Record, shape: str) -> MigrationError:
    """The step reads a body strictly: exactly the keys its schema pins, each
    value of its JSON type. So an integer written as a whole-number float, such
    as 3.0, which the schemas accept, is refused too."""
    return MigrationError(
        f"the {r.topic} at seq {r.seq} does not read as {shape}, which this step "
        "reads strictly, an integer only as a JSON integer and never as a float "
        "such as 3.0, so it cannot read it"
    )


def _started(r: _Record) -> _Started:
    if not _fits(r.body, _STARTED):
        raise _unreadable(r, "format 0.3.0's (lifecycle-v0.5)")
    return _Started(
        r.seq, handle=r.body["handle"], t=r.body["t"], launch_id=r.request_id
    )


def _beat(r: _Record) -> _Beat:
    if not _fits(r.body, _HEARTBEAT):
        raise _unreadable(r, "format 0.3.0's (lifecycle-v0.5)")
    b = r.body
    return _Beat(
        r.seq,
        step=b["step"],
        consumed_seq=b["consumed_seq"],
        claim_seq=b["claim_seq"],
        t=b["t"],
    )


def _refused(r: _Record, what: str) -> MigrationError:
    """M9's refusal of a record: what 0.4.0 cannot hold, or holds otherwise."""
    return MigrationError(f"the {r.topic} at seq {r.seq} {what}: M9 refuses it")


def _value(r: _Record) -> _Value:
    if not _fits(r.body, _VALUE):
        raise _unreadable(r, "format 0.3.0's (value-v0.2)")
    if r.request_id is not None:
        raise _refused(r, f"samples the subscription {r.request_id!r}")
    if r.name is None:  # an empty one the envelope refuses
        raise _refused(r, "has no name, which value-v0.3 requires")
    b = r.body
    return _Value(r.seq, r.name, value=b["value"], step=b["step"], t=b["t"])


def _spends(r: _Record, honored: list[str]) -> tuple[str, ...]:
    """A ``stopped``'s ``honored``: the ids of the stops it spends. The empty id
    names no stop, since envelope-v0.2 forbids an empty ``request_id``, so it
    honors nothing, and 0.4.0 rejects it on what either writer's stop becomes:
    a third party's withdrawal under it, a worker's exit node keeping it in
    ``honored``. Which wrote the stop is not known yet."""
    if "" in honored:
        raise MigrationError(
            f"the {r.topic} at seq {r.seq} honors the empty id, which names no "
            "stop: envelope-v0.2 forbids an empty request_id, so no stop bears it"
        )
    return tuple(honored)


def _stop(r: _Record) -> _Stop:
    b = r.body
    if _fits(b, _STOPPED) and not (b["completed"] and b["error"] is not None):
        return _Stopped(
            r.seq,
            completed=b["completed"],
            error=b["error"],
            final_step=b["final_step"],
            claim_seq=b["claim_seq"],
            honored=_spends(r, b["honored"]),
            t=b["t"],
            created_at=r.created_at,
        )
    if _fits(b, _MALFORMED):
        return _Malformed(
            r.seq,
            reason=b["reason"],
            honored=_spends(r, b["honored"]),
            claim_seq=b["claim_seq"],
            created_at=r.created_at,
        )
    raise _unreadable(
        r,
        "format 0.3.0's (lifecycle-v0.5), or the reclaim tool's malformed "
        "{reason, honored, claim_seq} (M10)",
    )


def _atoms(condition: object) -> list[str] | None:
    """The atoms of a subscription-v0.3 Condition, ``step`` and
    ``time_seconds``, at any depth of its ``any`` and ``all``; None when it is
    not one. Each branch of the grammar is an object with exactly one key."""
    if not isinstance(condition, dict) or len(condition) != 1:
        return None
    ((key, v),) = condition.items()
    match key:
        case "step" if _is_count(v):
            return ["step"]
        case "time_seconds" if _is_time(v):
            return ["time_seconds"]
        case "any" | "all" if isinstance(v, list) and v:
            atoms: list[str] = []
            for c in v:
                inner = _atoms(c)
                if inner is None:
                    return None
                atoms += inner
            return atoms
        case _:
            return None


def _trigger(r: _Record) -> None:
    """A stop as subscription-v0.3 pins it, a request id and at most a ``from``
    Condition, which 0.4.0 keeps byte for byte. A step atom in it is M9's:
    subscription-v0.4 removed it."""
    if r.request_id is None:
        raise MigrationError(
            f"the {r.topic} at seq {r.seq} has no request_id, which "
            "subscription-v0.3 requires of every control verb"
        )
    atoms = _atoms(r.body["from"]) if "from" in r.body else []
    if atoms is None or r.body.keys() - {"from"}:
        raise _unreadable(r, "subscription-v0.3's StopTrigger")
    if "step" in atoms:
        raise _refused(r, "holds a step atom, which subscription-v0.4 removed")


def _launch_id(r: _Record) -> str:
    if r.request_id is None:
        raise MigrationError(
            f"the {r.topic} at seq {r.seq} names no launch: launcher-v0.4 requires "
            "the launch's id as the request_id of every launcher record"
        )
    return r.request_id


def _launched(r: _Record) -> _Launched:
    launch = _launch_id(r)
    if not _fits(r.body, _LAUNCHED):
        raise _unreadable(r, "launcher-v0.4's")
    return _Launched(r.seq, launch, handle=r.body["handle"])


def _terminated(r: _Record) -> _Terminated:
    """``exited`` carries an exit code and no signal, ``killed`` a signal and no
    exit code."""
    launch = _launch_id(r)
    b = r.body
    if _fits(b, _TERMINATED):
        match b["reason"], b["exit_code"], b["signal"]:
            case "exited", int() as code, None:
                return _Terminated(r.seq, launch, Exited(code), b["t"])
            case "killed", None, int() as signal:
                return _Terminated(r.seq, launch, Killed(signal), b["t"])
    raise _unreadable(r, "launcher-v0.4's")


def _a_second_reap(t: _Terminated, reaped: Mapping[str, int]) -> None:
    """``reaped`` maps each launch's ``terminated`` before ``t`` to its seq. A
    process ends once, and no reference launcher writes two. Duplicates may
    disagree, and a run nothing claimed takes its launch's ``terminated`` as its
    verdict (§5), so two leave it ambiguous."""
    first = reaped.get(t.launch_id)
    if first is not None:
        raise MigrationError(
            f"the {_Topic.TERMINATED} at seq {t.seq} is a second for launch "
            f"{t.launch_id!r}, after the one at seq {first}: a process ends once, "
            "and no reference launcher writes two"
        )


@dataclass(frozen=True)
class _Log:
    """One run's 0.3.0 records, read once at the boundary: every record, and
    the typed bodies of those the passes interpret, each in seq order. Every
    record is 0.3.0's, as its schemas pin it, M10's bodies excepted. Every
    ``claim_seq`` on ``beats`` and ``stops`` names a claim before its record,
    every nonzero ``consumed_seq`` names a record before its heartbeat, each
    claim's integer steps rise, and each launch has at most one
    ``terminated``. No record is a subscription's, and no stop holds a step
    atom (M9)."""

    records: tuple[_Record, ...]
    claims: tuple[_Started, ...]
    beats: tuple[_Beat, ...]
    values: tuple[_Value, ...]
    stops: tuple[_Stop, ...]
    launched: tuple[_Launched, ...]
    terminated: tuple[_Terminated, ...]


# The envelope fields format 0.4.0 pins null on what these records become, which
# 0.3.0 left open: every lifecycle record's name and a stop's (lifecycle-v0.6,
# subscription-v0.4), and the request_id of a heartbeat, which becomes a commit
# or a heartbeat, and of a stopped, which becomes an exit node or a third party's
# records. No 0.3.0 reference writer set one.
_NAMELESS = frozenset(
    {
        _Topic.STARTED,
        _Topic.HEARTBEAT,
        _Topic.STOPPED,
        _Topic.NAK,
        _Topic.STOP,
    }
)
_UNREQUESTED = frozenset({_Topic.HEARTBEAT, _Topic.STOPPED})


def _enveloped(x: Row) -> _Body:
    """A row's body, once its envelope is envelope-v0.2's: a topic, and a name
    and a request id each null or non-empty, and a body that is an object."""
    if x.topic == "":
        raise MigrationError(
            f"the record at seq {x.seq} has an empty topic, which envelope-v0.2 "
            "forbids"
        )
    for field, value in (("name", x.name), ("request_id", x.request_id)):
        if value == "":
            raise MigrationError(
                f"the {x.topic} at seq {x.seq} has an empty {field}, which "
                "envelope-v0.2 forbids"
            )
    body = json.loads(x.body)
    if not isinstance(body, dict):
        raise MigrationError(
            f"the {x.topic} at seq {x.seq} has a body that is not an object, "
            "which envelope-v0.2 requires"
        )
    return body


_DEFINED = frozenset(_Topic)
_RESERVED = ("lifecycle.", "control.", "launcher.")  # the prefixes 0.3.0 owns


def _defined(r: _Record) -> None:
    """Refuses a topic under a prefix 0.3.0 reserves that 0.3.0 does not
    define, and a user's topic under ``supervisor.``, which 0.3.0 left to
    users and 0.4.0 reserves: supervisor-v0.5 defines only ``launched``,
    ``terminated`` and ``died`` there, so the step cannot carry one."""
    if r.topic.startswith("supervisor."):
        raise MigrationError(
            f"the {r.topic} at seq {r.seq} is a user's topic under supervisor.*, "
            "which format 0.3.0 left to users and 0.4.0 reserves: supervisor-v0.5 "
            "defines only launched, terminated and died there, so this step "
            "cannot carry it"
        )
    if r.topic.startswith(_RESERVED) and r.topic not in _DEFINED:
        raise MigrationError(
            f"the {r.topic} at seq {r.seq} is no topic format 0.3.0 defines under "
            "the prefix it reserves (lifecycle-v0.5, subscription-v0.3, "
            "launcher-v0.4)"
        )


def _carriable(r: _Record) -> None:
    """Refuses an envelope field 0.4.0 pins null that holds a value: the step
    cannot carry it, and dropping it would lose it silently."""
    for field, value, pinned in (
        ("name", r.name, _NAMELESS),
        ("request_id", r.request_id, _UNREQUESTED),
    ):
        if r.topic in pinned and value is not None:
            raise MigrationError(
                f"the {r.topic} at seq {r.seq} carries {field} {value!r}, which "
                "format 0.4.0 pins null on what it becomes: no 0.3.0 reference "
                "writer set one, and this step cannot carry it"
            )


def _names_a_claim_before_it(
    seq: int, topic: _Topic, claim_seq: int | None, claims: set[int]
) -> None:
    """``claims`` holds the claims before the record at ``seq``."""
    if claim_seq is not None and claim_seq not in claims:
        raise MigrationError(
            f"the {topic} at seq {seq} names claim {claim_seq}, which is not a "
            "claim before it"
        )


def _a_record_before_it(b: _Beat, seen: set[int]) -> None:
    """``seen`` holds the seqs before the heartbeat. Its ``consumed_seq`` is a
    drain watermark: 0 for none, else the seq of a record before it. That
    record is never an integer-step heartbeat, which ``_names_no_commit``
    refuses, nor a stop that leaves the log, which ``report`` refuses
    (``_drained_from_nothing``)."""
    if b.consumed_seq != 0 and b.consumed_seq not in seen:
        raise MigrationError(
            f"the {_Topic.HEARTBEAT} at seq {b.seq} has consumed_seq "
            f"{b.consumed_seq}, which names no record before it: no 0.3.0 writer "
            "wrote one, and renumbering cannot carry it"
        )


def _names_no_commit(b: _Beat, commits: Mapping[int, int]) -> None:
    """``commits`` maps each heartbeat with an integer step before ``b`` to its
    step. lifecycle-v0.5 defines the watermark as a read position in the
    control order, and the reference Worker sets it only to a control record's
    seq. Such a heartbeat is a commit M6 may move, and a watermark renamed to a
    moved record would cover records it never covered."""
    step = commits.get(b.consumed_seq)
    if step is not None:
        raise MigrationError(
            f"the {_Topic.HEARTBEAT} at seq {b.seq} has consumed_seq "
            f"{b.consumed_seq}, which names the {_Topic.HEARTBEAT} at seq "
            f"{b.consumed_seq}, step {step}'s commit: a watermark is a read "
            "position in the control order, and renaming cannot carry one that "
            "names a record renumbering moves"
        )


class _Stepped(NamedTuple):
    """A claim's latest heartbeat with an integer step so far."""

    step: int
    seq: int


def _not_a_rewind(seq: int, step: int, latest: _Stepped | None) -> None:
    """M9: a claim's integer steps rise. A heartbeat at or below its claim's
    latest integer step is an in-claim rewind, which 0.4.0 records with a
    ``rewind`` and 0.3.0 did not record at all."""
    if latest is not None and step <= latest.step:
        raise MigrationError(
            f"the {_Topic.HEARTBEAT} at seq {seq} is at step {step}, at or below "
            f"step {latest.step} of its claim's heartbeat at seq {latest.seq}: "
            "an in-claim rewind (M9), which this step refuses"
        )


def parse(rows: list[Row]) -> _Log:
    """One run's rows, in seq order, read as format 0.3.0 defines them. A
    refusal names the first record, by seq, that the step cannot read or that
    M9 refuses."""
    records: list[_Record] = []
    claims: list[_Started] = []
    beats: list[_Beat] = []
    values: list[_Value] = []
    stops: list[_Stop] = []
    launched: list[_Launched] = []
    terminated: list[_Terminated] = []
    before: set[int] = set()  # the claims so far
    seen: set[int] = set()  # the seqs so far
    commits: dict[int, int] = {}  # each integer-step heartbeat so far, its step
    stepped: dict[int, _Stepped] = {}  # each claim's latest integer step so far
    reaped: dict[str, int] = {}  # each launch's terminated so far, its seq
    for x in rows:
        r = _Record(
            x.seq, x.topic, x.name, x.request_id, _enveloped(x), x.body, x.created_at
        )
        _defined(r)
        _carriable(r)
        records.append(r)
        match r.topic:
            case _Topic.STARTED:
                claims.append(_started(r))
                before.add(r.seq)
            case _Topic.HEARTBEAT:
                b = _beat(r)
                _names_a_claim_before_it(r.seq, _Topic.HEARTBEAT, b.claim_seq, before)
                _a_record_before_it(b, seen)
                _names_no_commit(b, commits)
                if b.step is not None:
                    _not_a_rewind(b.seq, b.step, stepped.get(b.claim_seq))
                    stepped[b.claim_seq] = _Stepped(b.step, b.seq)
                    commits[b.seq] = b.step
                beats.append(b)
            case _Topic.VALUE:
                values.append(_value(r))
            case _Topic.STOPPED:
                stop = _stop(r)
                _names_a_claim_before_it(r.seq, _Topic.STOPPED, stop.claim_seq, before)
                stops.append(stop)
            case _Topic.SUBSCRIBE | _Topic.UNSUBSCRIBE | _Topic.BOUND:
                raise _refused(r, "is a subscription's record")
            case _Topic.STOP:
                _trigger(r)
            case _Topic.NAK if not _fits(r.body, _NAK):
                raise _unreadable(r, "format 0.3.0's (lifecycle-v0.5)")
            case _Topic.LAUNCHED:
                launched.append(_launched(r))
            case _Topic.TERMINATED:
                reap = _terminated(r)
                _a_second_reap(reap, reaped)
                reaped[reap.launch_id] = reap.seq
                terminated.append(reap)
        seen.add(r.seq)
    return _Log(
        tuple(records),
        tuple(claims),
        tuple(beats),
        tuple(values),
        tuple(stops),
        tuple(launched),
        tuple(terminated),
    )


# ----- pass 1: claim attribution --------------------------------------------------


@dataclass(frozen=True)
class Claim:
    """A claim, and the heartbeats and values pass 1 attributes to it, each in
    seq order."""

    started: _Started
    beats: tuple[_Beat, ...]
    values: tuple[_Value, ...]


@dataclass(frozen=True)
class Attribution:
    """Pass 1: every claim, in claim order, and the values before every claim."""

    claims: tuple[Claim, ...]
    unclaimed: tuple[_Value, ...]


def attribute(log: _Log) -> Attribution:
    """A heartbeat is the claim's its ``claim_seq`` names. A value is the latest
    claim's before it (an inference: the positional rule), or no claim's."""
    order = [c.seq for c in log.claims]
    beats: dict[int, list[_Beat]] = {c: [] for c in order}
    values: dict[int, list[_Value]] = {c: [] for c in order}
    unclaimed: list[_Value] = []
    for b in log.beats:
        beats[b.claim_seq].append(b)
    for v in log.values:
        before = bisect_right(order, v.seq)
        if before:
            values[order[before - 1]].append(v)
        else:
            unclaimed.append(v)
    return Attribution(
        claims=tuple(
            Claim(c, tuple(beats[c.seq]), tuple(values[c.seq])) for c in log.claims
        ),
        unclaimed=tuple(unclaimed),
    )


# ----- pass 2: classification -----------------------------------------------------

# mycooc's runner writes it when its worker exits nonzero with no terminal record
# (run_experiment.py, _SyncHandle); N is the exit code, negative for a signal.
_BACKSTOP = re.compile(r"subprocess exited (-?\d+) with no terminal record")


def _is_backstop(stop: _Stopped) -> bool:
    return stop.error is not None and _BACKSTOP.fullmatch(stop.error) is not None


@dataclass(frozen=True)
class _Exit:
    """The worker's own exit: it becomes the exit node."""

    stop: _Stopped


@dataclass(frozen=True)
class _Release:
    """A third party's stop that ends its claim: it becomes a ``died``."""

    stop: _Stop


End = _Exit | _Release
"""How a claim ended: the first ``stopped`` naming it, by who wrote it. A later
pass matches on the variant, ending in ``assert_never``. A well-formed one that
is not the backstop is the worker's exit unless the log contradicts that, in
one of two ways (``_lone_stop_rule``): its claim beats after it, or it was
written after the time it reports."""


class _Later(NamedTuple):
    """A third party's ``stopped`` after the first naming the same claim."""

    claim: int
    stop: _Stop


# A worker reporting its own exit stamps the stop's t and its record's created_at
# from one clock at one moment: SqliteChannel stamps created_at with time.time()
# at insert, behind a 5 s busy_timeout. A stop whose t precedes its created_at by
# more than this many seconds was written after the time it reports, by a writer
# that dated it to an event it learned of afterwards, as mycooc's reclaim tool
# dates one with SLURM's end time. Part of the step's frozen semantics.
_REPORTED_LATER = 60.0


def _lone_stop_rule(stop: _Stopped, *, beaten_after: bool) -> End:
    """THE RULE FOR LONE STOPS (§7, "lone stops"; the census of their writers):
    how the first ``stopped`` naming a claim ended it, when it is well-formed
    and not the backstop. It is the worker's exit, which is what 0.3.0 defined
    the topic to mean, unless the log contradicts that. It is a third party's
    release when either:

    - **its claim beats after it** (``beaten_after``): a heartbeat naming the
      same claim has a higher seq, so the worker outlived the stop;
    - **it was written after the time it reports:** its ``t`` precedes its
      record's ``created_at`` by more than ``_REPORTED_LATER`` seconds."""
    if beaten_after or stop.created_at - stop.t > _REPORTED_LATER:
        return _Release(stop)
    return _Exit(stop)


@dataclass(frozen=True)
class Classification:
    """Pass 2: every ``stopped``, by who wrote it and what it ends. Each is in
    exactly one field. A claim's end is the first ``stopped`` naming it, or,
    when none does, the ``terminated`` a ``died`` is attributed from, which
    ``report`` decides (``Reports.deaths``). ``ends`` maps each claim a
    ``stopped`` ended to that end, by who wrote it. ``later`` and
    ``claimless`` are in seq order."""

    ends: Mapping[int, End]
    later: tuple[_Later, ...]
    claimless: tuple[_Stop, ...]  # names no claim, so ends none


def classify(log: _Log) -> Classification:
    """The first ``stopped`` naming a claim ends it. It is a third party's
    release when it is malformed (M10) or the backstop, which is a third
    party's wherever it falls. Otherwise the rule for lone stops decides: the
    worker's exit, unless the log contradicts that, which makes it a release:
    a heartbeat naming its claim follows it, or its ``t`` precedes its
    ``created_at`` by more than ``_REPORTED_LATER``. Every later one naming
    that claim is a third party's."""
    ends: dict[int, End] = {}
    later: list[_Later] = []
    claimless: list[_Stop] = []
    last_beat = {b.claim_seq: b.seq for b in log.beats}  # in seq order: the last
    for s in log.stops:
        claim = s.claim_seq
        if claim is None:
            claimless.append(s)
        elif claim in ends:
            later.append(_Later(claim, s))
        elif isinstance(s, _Malformed) or _is_backstop(s):
            ends[claim] = _Release(s)
        else:
            beat = last_beat.get(claim)
            beaten_after = beat is not None and beat > s.seq
            ends[claim] = _lone_stop_rule(s, beaten_after=beaten_after)
    return Classification(ends, tuple(later), tuple(claimless))


# ----- pass 2, continued: what a third party's stop becomes -----------------------

_THIRD_PARTY = "migrated: third-party stopped"  # evidence when nothing says more


class Outcome(StrEnum):
    """How a claim died (supervisor-v0.5's ``died.outcome``). A death is never a
    completion."""

    PREEMPTED = "preempted"
    ERRORED = "errored"
    KILLED = "killed"
    LOST = "lost"


@dataclass(frozen=True)
class Death:
    """A ``supervisor.died``: the claim it names, by its 0.3.0 seq, how it
    ended, and the evidence. A third party's stop becomes one in its place,
    and §7 attributes one from a launch's ``terminated``, just after it."""

    claim: int
    outcome: Outcome
    evidence: str


@dataclass(frozen=True)
class Reap:
    """A ``supervisor.terminated`` in a backstop's place, when the backstop ends
    no claim (owner ruling c′): the runner's report of how its subprocess
    ended, under ``launch_id``, a launch id minted for it."""

    launch_id: str
    how: Exited | Killed


@dataclass(frozen=True)
class Dropped:
    """Nothing in its stop's place: the stop becomes only its withdrawals,
    which take its place."""


Becomes = Death | Reap | Dropped


@dataclass(frozen=True)
class Report:
    """A third party's ``stopped``: what it becomes in its place, and ``t``, the
    time every record it becomes carries: its own, or, for M10's body, which
    has none, its ``created_at``. Whatever it becomes, each id it honored
    becomes a ``control.withdraw`` placed after it (pass 5), so the stops stay
    spent."""

    stop: _Stop
    becomes: Becomes
    t: float


@dataclass(frozen=True)
class AttributedDeath:
    """§7's attributed death report: the death a launch's ``terminated``
    reports of its claim, and ``t``, the terminated's."""

    death: Death
    t: float


@dataclass(frozen=True)
class Reports:
    """Pass 2's third parties: every third party's ``stopped``, by its seq, in
    seq order, and every attributed death report, by the seq of the
    ``terminated`` it follows. A worker's exit is not among them: it becomes
    its claim's exit node."""

    stops: Mapping[int, Report]
    deaths: Mapping[int, AttributedDeath]


def _fresh(base: str, taken: set[str]) -> str:
    """``base``, or ``base~k`` for the least k >= 2 not in ``taken``; then
    taken. The 0.2.0 -> 0.3.0 step's minting, carried here."""
    cand, k = base, 1
    while cand in taken:
        k += 1
        cand = f"{base}~{k}"
    taken.add(cand)
    return cand


def _evidence(text: str | None) -> str:
    """A death's evidence: the text its writer gave, else the migration's label,
    since evidence is never empty (supervisor-v0.5)."""
    return text if text else _THIRD_PARTY


def _death(stop: _Stop) -> Death:
    """The mapping of a third party's stop that names a claim to a death: M10's
    body is ``preempted``, its reason the evidence (§7, M10). A well-formed one
    is ``errored`` when its ``error`` is set, an empty one included, as 0.3.0's
    verdict read it, and ``preempted`` otherwise, a completed one included,
    since a death is never a completion; its ``error`` is the evidence."""
    claim = stop.claim_seq
    assert claim is not None  # an end and a later stop name their claim
    match stop:
        case _Malformed(reason=reason):
            return Death(claim, Outcome.PREEMPTED, _evidence(reason))
        case _Stopped(error=None):
            return Death(claim, Outcome.PREEMPTED, _THIRD_PARTY)
        case _Stopped(error=error):
            return Death(claim, Outcome.ERRORED, _evidence(error))
        case _:
            assert_never(stop)


def _how(stop: _Stopped) -> Exited | Killed:
    """How the backstop's subprocess ended, by the N its text names, a Popen
    return code: a signal -N when N is negative, else an exit N. The runner
    writes the backstop only for a nonzero N."""
    assert stop.error is not None, f"the stop at seq {stop.seq} is no backstop"
    found = _BACKSTOP.fullmatch(stop.error)
    assert found is not None, f"the stop at seq {stop.seq} is no backstop"
    n = int(found[1])
    return Killed(-n) if n < 0 else Exited(n)


class _Refusal(NamedTuple):
    """A refusal a pass after ``parse`` finds, by the seq it names."""

    seq: int
    error: MigrationError


def _refuse_the_first(refusals: Sequence[_Refusal]) -> None:
    """Raises the refusal naming the lowest seq, so a run's refusal names its
    first refused record whichever check finds it."""
    if refusals:
        raise min(refusals, key=lambda x: x.seq).error


def _reported(how: Exited | Killed, claim: int) -> Death:
    """The live reap's mapping (§3), with ``signal s`` or ``exit n`` as the
    evidence: a signal is ``killed``, a nonzero exit code ``errored``, and an
    exit 0 ``lost``. An exit 0 says only that the process is gone, which
    reports only while the claim is held, as one nothing else ended is."""
    match how:
        case Killed(signal=signal):
            return Death(claim, Outcome.KILLED, f"signal {signal}")
        case Exited(code=0):
            return Death(claim, Outcome.LOST, "exit 0")
        case Exited(code=code):
            return Death(claim, Outcome.ERRORED, f"exit {code}")
        case _:
            assert_never(how)


class _Attributions(NamedTuple):
    """§7's attributed death reports, by the seq of the ``terminated`` each
    follows, and the launches refused for want of a handle."""

    deaths: dict[int, AttributedDeath]
    refused: list[_Refusal]


def _attributed(log: _Log, classification: Classification) -> _Attributions:
    """The newest claim naming a launch, which no ``stopped`` names, gets a death
    report from the launch's ``terminated`` after it when the launch's handle,
    its ``launched``'s, found by its id anywhere in the log, is the claim's,
    compared whole. ``parse`` holds each launch to one ``terminated``. Refused:
    a launch that not exactly one ``launched`` names, since there is then no
    handle to attribute the death by."""
    newest: dict[str, _Started] = {}
    for c in log.claims:  # in claim order, so each launch keeps its newest
        if c.launch_id is not None:
            newest[c.launch_id] = c
    launched: dict[str, list[_Launched]] = {}
    for x in log.launched:
        launched.setdefault(x.launch_id, []).append(x)
    deaths: dict[int, AttributedDeath] = {}
    refused: list[_Refusal] = []
    for reap in log.terminated:
        claim = newest.get(reap.launch_id)
        if claim is None or claim.seq > reap.seq or claim.seq in classification.ends:
            continue  # no claim's launch; the claim's, but before it; or a stopped's
        found = launched.get(reap.launch_id, [])
        if len(found) != 1:
            error = MigrationError(
                f"the {_Topic.TERMINATED} at seq {reap.seq} ends launch "
                f"{reap.launch_id!r}, which {len(found)} {_Topic.LAUNCHED} records "
                "name: without exactly one there is no handle to attribute its "
                f"death to the claim at seq {claim.seq} by"
            )
            refused.append(_Refusal(reap.seq, error))
        elif found[0].handle == claim.handle:  # else a wrapper's, or another host's
            deaths[reap.seq] = AttributedDeath(_reported(reap.how, claim.seq), reap.t)
    return _Attributions(deaths, refused)


def _drained_from_nothing(log: _Log, seqs: set[int]) -> list[_Refusal]:
    """``seqs`` holds the stops that leave the log. A heartbeat's watermark
    naming one is refused: a watermark is a read position in the control order,
    which the reference Worker sets only to a control record's seq, and
    renaming cannot carry one that names a record that leaves the log."""
    return [
        _Refusal(
            b.seq,
            MigrationError(
                f"the {_Topic.HEARTBEAT} at seq {b.seq} has consumed_seq "
                f"{b.consumed_seq}, which names the {_Topic.STOPPED} at seq "
                f"{b.consumed_seq}, a third party's that becomes only its "
                "withdrawals: renaming cannot carry a watermark that names a "
                "record that leaves the log"
            ),
        )
        for b in log.beats
        if b.consumed_seq in seqs
    ]


def report(log: _Log, classification: Classification) -> Reports:
    """What each third party's ``stopped`` becomes (review §12, item 14; owner
    ruling c′):

    - **a release,** the stop that ends its claim, becomes a death
      (``_death``);
    - **the backstop that ends no claim,** after its claim's end or naming
      none, becomes a ``supervisor.terminated`` (``Reap``): the runner's report
      of how its subprocess ended, by the N its text names, under a launch id
      minted ``backstop@<seq>`` with its 0.3.0 seq, fresh against every request
      id in the run and every one minted before it;
    - **any other later stop** becomes a death only when its ``error`` is set,
      and otherwise only its withdrawals: once the claim has ended, only a
      report with a cause is written (§3). M10's body has no ``error``;
    - **any other claimless stop** becomes only its withdrawals: a death names
      a claim.

    A claim that 0.3.0 saw ended only by its launch's ``launcher.terminated``
    gets an attributed death report (§7; ``_attributed``). A refusal names the
    lowest seq any of the pass's checks refuses."""
    attributions = _attributed(log, classification)
    ends = {end.stop.seq: end for end in classification.ends.values()}
    later = {x.stop.seq for x in classification.later}
    taken = {x.request_id for x in log.records if x.request_id is not None}
    taken |= {i for s in log.stops for i in s.honored}
    stops: dict[int, Report] = {}
    for s in log.stops:  # in seq order, so the minting is deterministic
        becomes: Becomes
        match ends.get(s.seq):
            case _Exit():
                continue  # its claim's exit node
            case _Release():
                becomes = _death(s)
            case None if isinstance(s, _Stopped) and _is_backstop(s):
                becomes = Reap(_fresh(f"backstop@{s.seq}", taken), _how(s))
            case None if (
                isinstance(s, _Stopped) and s.seq in later and s.error is not None
            ):
                becomes = _death(s)
            case None:
                becomes = Dropped()
            case end:
                assert_never(end)
        t = s.t if isinstance(s, _Stopped) else s.created_at
        stops[s.seq] = Report(s, becomes, t)
    dropped = {seq for seq, x in stops.items() if isinstance(x.becomes, Dropped)}
    _refuse_the_first(attributions.refused + _drained_from_nothing(log, dropped))
    return Reports(stops, attributions.deaths)


# ----- pass 3: synthesis ----------------------------------------------------------


class Place(StrEnum):
    """The three places M5 synthesizes a step commit, and the only three:

    - a tick skipped between two real beats of one claim, even one with no
      values;
    - a trailing step at or below a non-error exit's last step;
    - a trailing step s, whatever ended its claim, of the newest claim before
      a later claim whose first real beat is s+1 to reach step s: the claim M1
      will choose, wherever its commit at s is a candidate."""

    SKIPPED_TICK = "skipped tick"
    BEFORE_A_CLEAN_EXIT = "trailing step before a non-error exit"
    RESUMED_FROM = "trailing step a later claim resumed from"


class Uncommitted(StrEnum):
    """Why a value stays outside every lineage. A value takes the first reason
    that holds, in this order:

    - it is stepless, a register (M3), wherever it stands;
    - it is before every claim, so no claim's (pass 1);
    - it is after its claim's end (M4), whatever ended the claim: the first
      ``stopped`` naming it, or the ``terminated`` a ``died`` is attributed
      from;
    - it is a claim's that beat no integer step, which so reached no step
      (P3's loading cells): whether it wrote it while it loaded or in a step
      it never beat, even at or below a clean exit's last step;
    - it is a trailing step above its claim's exit's last step;
    - it is a trailing step no exit vouches for, which M5's third place does
      not reach: nothing says its iteration finished."""

    STEPLESS = "stepless"
    BEFORE_EVERY_CLAIM = "before every claim"
    AFTER_ITS_END = "after its claim's end"
    LOADING = "a claim's that beat no step"
    ABOVE_THE_LAST_STEP = "above its exit's last step"
    UNREACHED = "an unreached trailing step"


@dataclass(frozen=True)
class Real:
    """A commit that is a 0.3.0 heartbeat with an integer step."""

    beat: _Beat


@dataclass(frozen=True)
class Synthetic:
    """A commit M5 synthesizes, at ``place``. ``previous`` is the real heartbeat
    before it in its claim, worked out once (``_claim_commits``) and read by
    every pass that needs it: M8 here, candidacy (``_beaten``) and placement.
    M8's fields:

    - ``t`` from the last value it commits that carries one, else from
      ``previous``;
    - its ``consumed_seq`` is ``previous``'s.

    M5 synthesizes only above a claim's first real beat, so ``previous`` always
    exists, and M8's further fallbacks (the ``created_at`` of the record it is
    placed after; a ``consumed_seq`` of 0) never apply. Its ``fired`` and
    ``expired`` are ``[]``, as on every migrated node."""

    place: Place
    previous: _Beat
    t: float


@dataclass(frozen=True)
class Commit:
    """A commit at old step ``step``, and the seqs of the values it commits, in
    seq order."""

    step: int
    values: tuple[int, ...]
    origin: Real | Synthetic


@dataclass(frozen=True)
class ExitNode:
    """A claim's end that becomes its exit node: the worker's exit. ``values``
    are what it holds: ``()`` when it vouches for its iteration (a non-error
    exit), None when it discards its open iteration (an error exit). Its
    ``parent`` is its claim's newest commit, else its resume point (pass 4)."""

    stop: _Stopped
    values: tuple[()] | None


class Left(NamedTuple):
    """A value no commit holds, and why."""

    value: _Value
    why: Uncommitted


@dataclass(frozen=True)
class ClaimCommits:
    """A claim's commits, real and synthetic, in its order, which is old-step
    order; and its exit node, when its end is one. A claim that beat no integer
    step commits nothing."""

    claim: Claim
    commits: tuple[Commit, ...]
    exit: ExitNode | None


@dataclass(frozen=True)
class Synthesis:
    """Pass 3: each claim's commits, in claim order, and every value no commit
    holds, in seq order. Each value is committed once or left once."""

    claims: tuple[ClaimCommits, ...]
    uncommitted: tuple[Left, ...]


def _exit_node(end: End | None) -> ExitNode | None:
    match end:
        case _Exit(stop):
            return ExitNode(stop, () if stop.error is None else None)
        case _Release() | None:
            return None
        case _:
            assert_never(end)


def _under_the_ceiling(node: ExitNode | None, step: int) -> bool:
    """Whether a trailing step is at or below its claim's exit's last step, the
    0.3.0 ``final_step``, its ceiling. A claim with no exit node has no ceiling,
    so every step is under it; an exit whose ``final_step`` is null reached no
    step, so none is."""
    if node is None:
        return True
    last = node.stop.final_step
    return last is not None and step <= last


@dataclass(frozen=True)
class _Reading:
    """A claim read by M2, M3, M4 and M5's first two places, before M5's third
    place looks across claims.

    ``real`` holds its heartbeats with an integer step, by step, ascending.
    ``placed`` maps each commit's step (real, or synthesized at a
    ``synthesized_at`` place) to the values it commits. ``trailing`` maps each
    trailing step that only M5's third place can commit to its values.
    ``left`` holds the values no commit can hold."""

    claim: Claim
    exit: ExitNode | None
    real: Mapping[int, _Beat]
    synthesized_at: Mapping[int, Place]
    placed: Mapping[int, tuple[_Value, ...]]
    trailing: Mapping[int, tuple[_Value, ...]]
    left: tuple[Left, ...]

    @property
    def first(self) -> int | None:
        """The step of its first real beat."""
        return next(iter(self.real), None)

    @property
    def reached(self) -> frozenset[int]:
        """The steps it reached, by a commit or by trailing values."""
        return frozenset(self.placed) | frozenset(self.trailing)


def _read(claim: Claim, node: ExitNode | None, end: int | None) -> _Reading:
    """``node`` is its exit node, and ``end`` the seq of the record that ends it
    (``_ends``), each None when it has none."""
    real: dict[int, _Beat] = {}
    for b in claim.beats:
        if b.step is not None:
            real[b.step] = b  # ascending: parse refuses an in-claim rewind
    steps = list(real)
    synthesized_at: dict[int, Place] = {}
    if steps:
        for s in range(steps[0] + 1, steps[-1]):
            if s not in real:
                synthesized_at[s] = Place.SKIPPED_TICK
    placed: dict[int, list[_Value]] = {
        s: [] for s in sorted(real.keys() | synthesized_at)
    }
    trailing: dict[int, list[_Value]] = {}
    left: list[Left] = []
    for v in claim.values:
        if v.step is None:
            left.append(Left(v, Uncommitted.STEPLESS))
        elif end is not None and v.seq > end:
            left.append(Left(v, Uncommitted.AFTER_ITS_END))
        elif not steps:  # a claim that beat no step reached none
            left.append(Left(v, Uncommitted.LOADING))
        elif v.step in placed:  # M2: a real beat's step, or a skipped tick's
            placed[v.step].append(v)
        elif v.step < steps[0]:  # a leading value
            placed[steps[0]].append(v)
        elif not _under_the_ceiling(node, v.step):  # trailing from here on
            left.append(Left(v, Uncommitted.ABOVE_THE_LAST_STEP))
        elif node is not None and node.values is not None:  # the exit vouches
            synthesized_at[v.step] = Place.BEFORE_A_CLEAN_EXIT
            placed[v.step] = [v]
        else:
            trailing.setdefault(v.step, []).append(v)
    return _Reading(
        claim,
        node,
        real,
        synthesized_at,
        {s: tuple(vs) for s, vs in placed.items()},
        {s: tuple(vs) for s, vs in trailing.items()},
        tuple(left),
    )


def _newest_reaching(
    reached: Sequence[frozenset[int]], before: int, step: int
) -> int | None:
    """THE NEWEST CLAIM TO REACH A STEP: the index of the newest claim before
    the claim at index ``before``, in claim order, whose ``reached`` steps hold
    ``step``; None when no claim's do. Claim order, never step nearness (Review
    Focus 2).

    Pass 4 (M1) asks it of the steps of each claim's candidates, after
    synthesis, for a first commit's parent at the step below it: there it is
    the claim M1 chooses. M5's third place asks it of the steps each claim
    reached by a commit or by trailing values, before synthesis: there it is
    the claim M1 will choose wherever that claim's commit at the step is a
    candidate. Without a displaced worker it always is, since the third place
    turns the trailing step it chooses into a candidate; where the two differ
    is a known limit (the module docstring)."""
    for i in range(before - 1, -1, -1):
        if step in reached[i]:
            return i
    return None


def _synthetic(place: Place, values: Sequence[_Value], previous: _Beat) -> Synthetic:
    """M8. ``previous`` is the claim's real heartbeat before the commit."""
    dated = [v.t for v in values if v.t is not None]
    return Synthetic(place, previous, dated[-1] if dated else previous.t)


def _claim_commits(r: _Reading, resumed: set[int]) -> tuple[ClaimCommits, list[Left]]:
    """A claim's commits, once M5's third place has chosen ``resumed``, the
    trailing steps it commits in this claim."""
    synthesized_at = dict(r.synthesized_at)
    placed = dict(r.placed)
    left = list(r.left)
    for s, values in r.trailing.items():
        if s in resumed:
            synthesized_at[s] = Place.RESUMED_FROM
            placed[s] = values
        else:
            left.extend(Left(v, Uncommitted.UNREACHED) for v in values)
    commits: list[Commit] = []
    previous: _Beat | None = None  # the real heartbeat before each step
    for s in sorted(placed):
        values = placed[s]
        beat = r.real.get(s)
        if beat is None:
            # M5 synthesizes only above the first real beat, which is placed first
            assert previous is not None
            origin: Real | Synthetic = _synthetic(synthesized_at[s], values, previous)
        else:
            origin = Real(beat)
            previous = beat
        commits.append(Commit(s, tuple(v.seq for v in values), origin))
    return ClaimCommits(r.claim, tuple(commits), r.exit), left


def _ends(
    classification: Classification, deaths: Mapping[int, AttributedDeath]
) -> dict[int, int]:
    """M4's end of each ended claim, by the claim's seq: the seq of the record
    that ends it. That is the first ``stopped`` naming it, or, for a claim no
    ``stopped`` names, the ``terminated`` a ``died`` is attributed from
    (``report``), since a death report ends its claim (§2)."""
    ends = {claim: end.stop.seq for claim, end in classification.ends.items()}
    for terminated, d in deaths.items():
        assert d.death.claim not in ends, f"the claim {d.death.claim} ends twice"
        ends[d.death.claim] = terminated
    return ends


def synthesize(
    attribution: Attribution,
    classification: Classification,
    deaths: Mapping[int, AttributedDeath],
) -> Synthesis:
    """M2 and M5 assign each value to a commit of its claim, synthesizing the
    commits M5 names; every other value is left, with its reason, M4's among
    them. ``deaths`` are pass 2's attributed death reports (``Reports.deaths``):
    with the first ``stopped`` naming each claim, they say where each claim
    ends (M4). Each claim is read alone first, then M5's third place looks
    across claims; no claim's reading depends on another's, so nothing is
    circular."""
    ends = _ends(classification, deaths)
    readings = [
        _read(
            c,
            _exit_node(classification.ends.get(c.started.seq)),
            ends.get(c.started.seq),
        )
        for c in attribution.claims
    ]
    reached = [r.reached for r in readings]
    resumed: list[set[int]] = [set() for _ in readings]
    for i, r in enumerate(readings):
        first = r.first
        if first is not None and first > 0:
            j = _newest_reaching(reached, i, first - 1)
            if j is not None and first - 1 in readings[j].trailing:
                resumed[j].add(first - 1)
    claims: list[ClaimCommits] = []
    left = [
        Left(
            v,
            Uncommitted.STEPLESS if v.step is None else Uncommitted.BEFORE_EVERY_CLAIM,
        )
        for v in attribution.unclaimed
    ]
    for r, steps in zip(readings, resumed, strict=True):
        claim, its_left = _claim_commits(r, steps)
        claims.append(claim)
        left.extend(its_left)
    return Synthesis(tuple(claims), tuple(sorted(left, key=lambda x: x.value.seq)))


# ----- pass 4: parents and resume points (M1) -------------------------------------


class CommitRef(NamedTuple):
    """A commit by pass 3's identity: its claim's seq and its old step, unique
    within the claim, whose steps rise (M9). Pass 4 writes no seqs: pass 5
    places the commits and renumbers."""

    claim: int
    step: int


@dataclass(frozen=True)
class Fresh:
    """Its first commit is at old step 0, with no step below it: a fresh start,
    with no resume point."""

    node: ClassVar[None] = None


@dataclass(frozen=True)
class Resumed:
    """Its first commit, at old step s, resumes from ``node``: the candidate at
    s-1 of the newest claim before it, in claim order, to hold one."""

    node: CommitRef


@dataclass(frozen=True)
class Parentless:
    """No claim before it holds a candidate at s-1: a parentless resume, with
    no resume point, which P1 counts and P3 names."""

    node: ClassVar[None] = None


@dataclass(frozen=True)
class Inferred:
    """It beat no integer step, so it commits nothing: by inference, ``node`` is
    the newest candidate of the newest claim before it to hold one, or None
    when no claim does."""

    node: CommitRef | None


ResumeFrom = Fresh | Resumed | Parentless | Inferred
"""A claim's ``started.resume_from``, by the rule M1 decided it by: ``node`` is
the commit it names, or None, and only ``Resumed`` and ``Inferred`` name one. A
report counts the rules by variant."""


@dataclass(frozen=True)
class Linkage:
    """Pass 4: every claim's resume point, by its seq, in claim order; every
    commit's ``parent``; and each exit node's ``parent``, by its claim's seq.
    Each names a commit or nothing, never an exit node."""

    resume_from: Mapping[int, ResumeFrom]
    parents: Mapping[CommitRef, CommitRef | None]
    exits: Mapping[int, CommitRef | None]


class _Beaten(NamedTuple):
    """A commit, and the seq of the beat that decides whether it is a
    candidate for a later claim."""

    commit: CommitRef
    beat: int


def _beaten(c: ClaimCommits) -> list[_Beaten]:
    """A claim's commits, in its order, each with the beat that decides whether
    it is placed below a later claim's ``started``: its own heartbeat when it is
    real, else ``Synthetic.previous``, the real heartbeat before it in its
    claim, the one M8 dates it by. M6 places a real commit no earlier than its
    heartbeat, and a synthetic commit after its parent, so that beat binds it
    too.

    Only beats can follow that ``started``, of all M6 places a commit after.
    Pass 1 attributes values by position, so an earlier claim's committed values
    precede it. A ``consumed_seq`` names a record before its heartbeat, and a
    synthetic commit's is the heartbeat's before it (M8). A first commit's
    parent is a candidate of an earlier claim. A later commit's parents are its
    claim's commits below it, whose beats precede the one that decides it. If
    pass 1 ever attributes values by name rather than by position, revisit this
    rule."""
    beaten: list[_Beaten] = []
    for k in c.commits:
        match k.origin:
            case Real(beat=beat) | Synthetic(previous=beat):
                beaten.append(_Beaten(CommitRef(c.claim.started.seq, k.step), beat.seq))
            case _:
                assert_never(k.origin)
    return beaten


def _resume_from(
    claims: Sequence[ClaimCommits], beaten: Sequence[Sequence[_Beaten]], i: int
) -> ResumeFrom:
    """M1's resume point for the claim at index ``i``, among its candidates:
    the commits of the claims before it whose deciding beat (``beaten``, each
    claim's) precedes its ``started``."""
    below = claims[i].claim.started.seq
    held = [[x.commit for x in b if x.beat < below] for b in beaten[:i]]
    commits = claims[i].commits
    if not commits:  # it beat no integer step
        return Inferred(next((h[-1] for h in reversed(held) if h), None))
    first = commits[0].step
    if first == 0:
        return Fresh()
    j = _newest_reaching([frozenset(x.step for x in h) for h in held], i, first - 1)
    if j is None:
        return Parentless()
    # its steps rise, so the chosen claim's last commit at s-1 is its only one
    return Resumed(CommitRef(claims[j].claim.started.seq, first - 1))


def link(synthesis: Synthesis) -> Linkage:
    """M1. A claim's first commit takes its resume point as ``parent``, and each
    later commit the claim's previous commit; its exit node takes its position
    at the exit: its newest commit, else its resume point. A commit is the
    parent even where an extendable exit follows it, since every 0.3.0
    checkpoint was saved at a step, never after an exit (review §12, item 14).

    The claims are read in claim order, after synthesis, so every commit M5
    synthesized can be a parent. A resume point is chosen among the
    candidates, the commits placed below the claim's ``started``: a displaced
    worker's commit made or completed after the claim began is never its
    resume point."""
    claims = synthesis.claims
    beaten = [_beaten(c) for c in claims]
    resume_from: dict[int, ResumeFrom] = {}
    parents: dict[CommitRef, CommitRef | None] = {}
    exits: dict[int, CommitRef | None] = {}
    for i, c in enumerate(claims):
        claim = c.claim.started.seq
        start = _resume_from(claims, beaten, i)
        resume_from[claim] = start
        position = start.node  # a started leaves its resume point
        for k in c.commits:
            parents[CommitRef(claim, k.step)] = position
            position = CommitRef(claim, k.step)  # a commit leaves itself
        if c.exit is not None:
            exits[claim] = position
    return Linkage(resume_from, parents, exits)


# ----- pass 5: placement and renumbering (M6) ---------------------------------------


@dataclass(frozen=True)
class Input:
    """An input record, by its 0.3.0 seq. A real commit is its heartbeat's
    record, moved, so it is keyed by the heartbeat's seq."""

    seq: int


@dataclass(frozen=True)
class Synthesized:
    """A commit M5 synthesized, a record the input does not hold, by pass 3's
    identity."""

    ref: CommitRef


@dataclass(frozen=True)
class Withdrawal:
    """A ``control.withdraw`` a third party's stop becomes, one per id it
    honored: the stop's 0.3.0 seq and the id, unique within its ``honored``."""

    stop: int
    request_id: str


@dataclass(frozen=True)
class Attributed:
    """A ``supervisor.died`` §7 attributes from a launch's ``terminated``, by
    the terminated's 0.3.0 seq: one launch, one newest claim, so at most one
    follows any terminated."""

    terminated: int


Key = Input | Synthesized | Withdrawal | Attributed
"""An output record before renumbering. A pass that adds records adds a
variant for them, and places each through ``After``."""


@dataclass(frozen=True)
class After:
    """A record placed just after ``anchor``, wherever the anchor itself is
    placed: an anchor is a record, never a seq. Records placed after one anchor
    follow it in ``tie`` order, each with the records placed after it. Two
    records placed after one anchor never share a tie. M6 places at most one
    record after any record, so the ties order the records a later pass
    places; a commit's tie is its (claim seq, old step)."""

    anchor: Key
    tie: tuple[int, ...]


@dataclass(frozen=True)
class Placement:
    """Pass 5: each commit's key, by pass 3's identity; where every record
    placed after another goes: every synthetic commit, every real commit that
    moves (M6), every withdrawal and every attributed death report; and
    ``dropped``, the input records that leave the output: each third party's
    stop that becomes only its withdrawals, which take its place. Every other
    input record keeps its place."""

    commits: Mapping[CommitRef, Key]
    moves: Mapping[Key, After]
    dropped: frozenset[Key]


def place(synthesis: Synthesis, linkage: Linkage, reports: Reports) -> Placement:
    """M6, as the migration reads it, then the withdrawals and the attributed
    death reports. Each id a third party's stop honored is withdrawn just after
    the stop, in ``honored`` order, whatever the stop becomes: in its place, a
    death or a terminated replaces it, and a stop that becomes nothing else
    leaves the output, its withdrawals taking its place. A stop never moves,
    and M6 never places a commit after one, so each stop's withdrawals are the
    only records placed after it. An attributed death report goes just after
    its ``terminated``, the only record placed there, with its claim's seq as
    its tie.

    M6: each commit goes just after the latest of
    the records it must follow: its last committed value, its parent, the record
    its ``consumed_seq`` names, and, for a real commit, its own heartbeat. A
    real commit that follows them all at its heartbeat stays there.

    The record its ``consumed_seq`` names never decides, so it is asserted
    rather than compared: ``parse`` holds it to a record before the heartbeat
    whose watermark it is, and never one M6 moves, and the commit follows that
    heartbeat, a synthetic commit through its parent. A synthetic commit's
    watermark is the real heartbeat's before it (M8).

    The commits are placed in claim order, each claim's in its order, so a
    commit's parent, its claim's previous commit or a resume point in an
    earlier claim, is placed before it. Of the records a commit follows, only
    its parent can have moved: values never move and its heartbeat is its own.
    So a record's position is the seq of the input record it follows from and
    its depth below it: one that stays is at (its seq, 0), and a moved one one
    deeper than its anchor. That orders one moved record against any unmoved
    ones, which is every comparison M6 makes.

    For the same reasons M6 places at most one record after any record: a
    commit's last value is its own, and a parent is the latest record only for
    its claim's next commit, since a resume point precedes the claim's
    ``started``. The ties order the records a later pass places."""
    keys: dict[CommitRef, Key] = {}
    moves: dict[Key, After] = {}
    below: dict[Key, tuple[int, int]] = {}  # a moved record's root seq and depth

    def position(key: Key) -> tuple[int, int]:
        if key in below:
            return below[key]
        assert isinstance(key, Input), f"{key} is not placed yet"
        return key.seq, 0

    for c in synthesis.claims:
        for k in c.commits:
            ref = CommitRef(c.claim.started.seq, k.step)
            parent = linkage.parents[ref]
            assert parent is None or parent in keys, f"{ref} precedes its parent"
            follows: list[Key] = [Input(v) for v in k.values[-1:]]
            if parent is not None:
                follows.append(keys[parent])
            match k.origin:
                case Real(beat):
                    own: Key = Input(beat.seq)
                    consumed = beat.consumed_seq
                    follows.append(own)
                case Synthetic(previous=previous):
                    own = Synthesized(ref)
                    consumed = previous.consumed_seq
                    # M5 synthesizes only above a claim's first real beat, so a
                    # synthetic commit's parent is a commit of its own claim
                    assert follows, f"the synthetic commit {ref} follows nothing"
                case _:
                    assert_never(k.origin)
            keys[ref] = own
            latest = max(follows, key=position)
            assert consumed == 0 or position(Input(consumed)) < position(latest)
            if latest != own:
                moves[own] = After(latest, ref)
                root, depth = position(latest)
                below[own] = (root, depth + 1)
    dropped: set[Key] = set()
    for seq, x in reports.stops.items():
        for i, request_id in enumerate(x.stop.honored):
            moves[Withdrawal(seq, request_id)] = After(Input(seq), (i,))
        if isinstance(x.becomes, Dropped):
            dropped.add(Input(seq))
    for seq, d in reports.deaths.items():
        moves[Attributed(seq)] = After(Input(seq), (d.death.claim,))
    return Placement(keys, moves, frozenset(dropped))


def renumber(
    seqs: Sequence[int], moves: Mapping[Key, After], dropped: frozenset[Key]
) -> Mapping[Key, int]:
    """THE RENUMBERING, once, for every record placed by any pass: each input
    record in ``seqs`` (input order) that ``moves`` does not move keeps its
    order, and each record ``moves`` places follows its anchor, after the
    records placed after that anchor with a lower tie, each with its own. An
    input record in ``dropped`` takes no seq, and the records placed after it
    take its place. Returns every record's 0.4.0 seq, gapless from 1, in output
    order."""
    after: dict[Key, list[tuple[tuple[int, ...], Key]]] = {}
    for key, at in moves.items():
        after.setdefault(at.anchor, []).append((at.tie, key))
    out: dict[Key, int] = {}
    stack: list[Key] = [Input(s) for s in reversed(seqs) if Input(s) not in moves]
    while stack:
        key = stack.pop()
        if key not in dropped:
            out[key] = len(out) + 1
        placed = sorted(after.pop(key, []), key=lambda x: x[0])
        ties = [tie for tie, _ in placed]
        assert len(set(ties)) == len(ties), f"records after {key} share a tie"
        stack.extend(k for _, k in reversed(placed))
    assert not after, f"placed after records no order holds: {list(after)}"
    return out


# ----- pass 6: the renames (M7) ------------------------------------------------------


@dataclass(frozen=True)
class ToStarted:
    """A claim's ``started``, with its ``resume_from`` (M1) renumbered."""

    started: _Started
    resume_from: int | None


@dataclass(frozen=True)
class ToCommit:
    """A commit, real or synthetic, named ``ref`` by pass 3's identity: the
    claim it speaks for, its watermark (0 for none), its parent and the values
    it commits, renumbered."""

    ref: CommitRef
    origin: Real | Synthetic
    claim_seq: int
    consumed_seq: int
    parent: int | None
    values: tuple[int, ...]


@dataclass(frozen=True)
class ToHeartbeat:
    """A stepless heartbeat, a beat: its claim and its watermark (0 for none),
    renumbered."""

    beat: _Beat
    claim_seq: int
    consumed_seq: int


@dataclass(frozen=True)
class ToExit:
    """A claim's exit node: its claim and its parent, the claim's position at
    the exit, renumbered."""

    node: ExitNode
    claim_seq: int
    parent: int | None


@dataclass(frozen=True)
class ToDied:
    """A death, a third party's stop's or one attributed from a launch's
    ``terminated``: the claim it names, renumbered, and the ``t`` of the
    record it comes from."""

    death: Death
    claim_seq: int
    t: float


@dataclass(frozen=True)
class ToReap:
    """A backstop that ends no claim, which becomes a ``terminated``, and the
    backstop's ``t``. It holds no seq."""

    reap: Reap
    t: float


@dataclass(frozen=True)
class ToWithdraw:
    """A withdrawal of the stop ``request_id`` names, dated by the stop that
    honored it. It holds no seq."""

    request_id: str
    t: float


@dataclass(frozen=True)
class ToValue:
    """A value. It holds no seq."""

    value: _Value


@dataclass(frozen=True)
class NoSeqs:
    """A record whose body 0.4.0 keeps and which holds no seq: a
    ``control.stop``, a ``launcher.*`` record, a ``nak``, or a topic 0.3.0
    does not define."""

    record: _Record


Remapped = (
    ToStarted
    | ToCommit
    | ToHeartbeat
    | ToExit
    | ToDied
    | ToReap
    | ToWithdraw
    | ToValue
    | NoSeqs
)
"""An output record with every seq-valued field renumbered (M7), before its
0.4.0 body is written."""


@dataclass(frozen=True)
class Out:
    """An output record: its 0.4.0 seq, its key, its ``created_at`` and its
    renamed fields."""

    seq: int
    key: Key
    created_at: float
    record: Remapped


def rename(
    log: _Log,
    synthesis: Synthesis,
    linkage: Linkage,
    reports: Reports,
    placement: Placement,
    numbering: Mapping[Key, int],
) -> tuple[Out, ...]:
    """M7: every output record, in output order, with every seq-valued field
    the 0.4.0 schemas define renumbered to name the record it named:
    ``claim_seq`` (on a heartbeat, a commit, an exit and a death),
    ``consumed_seq``, ``parent``, ``values`` and ``resume_from``. A
    ``consumed_seq`` of 0 names nothing and stays 0. Request ids are strings,
    so a ``stop@N`` stays as it is.

    An input record keeps its ``created_at``, and so does a stop that becomes a
    death or a terminated in its place. A record placed after another takes
    that of its anchor: a withdrawal its stop's, whether the stop stays or
    leaves, and an attributed death its ``terminated``'s."""

    def new(seq: int) -> int:
        return numbering[Input(seq)]

    def node(ref: CommitRef | None) -> int | None:
        return None if ref is None else numbering[placement.commits[ref]]

    def watermark(seq: int) -> int:
        return 0 if seq == 0 else new(seq)

    def commit(ref: CommitRef, k: Commit) -> ToCommit:
        match k.origin:
            case Real(beat=beat) | Synthetic(previous=beat):  # M8's, for a synthetic
                consumed = beat.consumed_seq
            case _:
                assert_never(k.origin)
        return ToCommit(
            ref,
            k.origin,
            claim_seq=new(ref.claim),
            consumed_seq=watermark(consumed),
            parent=node(linkage.parents[ref]),
            values=tuple(new(v) for v in k.values),
        )

    records = {x.seq: x for x in log.records}
    claims = {c.seq: c for c in log.claims}
    beats = {b.seq: b for b in log.beats}
    values = {v.seq: v for v in log.values}
    commits = {
        placement.commits[CommitRef(c.claim.started.seq, k.step)]: (
            CommitRef(c.claim.started.seq, k.step),
            k,
        )
        for c in synthesis.claims
        for k in c.commits
    }
    exits = {c.exit.stop.seq: c.exit for c in synthesis.claims if c.exit is not None}

    def renamed(r: _Record) -> Remapped:
        match r.topic:
            case _Topic.STARTED:
                return ToStarted(claims[r.seq], node(linkage.resume_from[r.seq].node))
            case _Topic.HEARTBEAT if Input(r.seq) in commits:
                return commit(*commits[Input(r.seq)])
            case _Topic.HEARTBEAT:
                b = beats[r.seq]
                return ToHeartbeat(b, new(b.claim_seq), watermark(b.consumed_seq))
            case _Topic.STOPPED if r.seq in exits:
                exit = exits[r.seq]
                claim = exit.stop.claim_seq
                assert claim is not None  # an exit node ends the claim it names
                return ToExit(exit, new(claim), node(linkage.exits[claim]))
            case _Topic.STOPPED:
                x = reports.stops[r.seq]
                match x.becomes:
                    case Death(claim=claim) as death:
                        return ToDied(death, new(claim), x.t)
                    case Reap() as reap:
                        return ToReap(reap, x.t)
                    case Dropped():
                        raise AssertionError(f"the dropped stop {r.seq} is numbered")
                    case _:
                        assert_never(x.becomes)
            case _Topic.VALUE:
                return ToValue(values[r.seq])
            case _:
                return NoSeqs(r)

    out: list[Out] = []
    created: dict[Key, float] = {Input(x.seq): x.created_at for x in log.records}
    for key, seq in sorted(numbering.items(), key=lambda x: x[1]):
        match key:
            case Input(old):
                record = renamed(records[old])
            case Synthesized():
                record = commit(*commits[key])
            case Withdrawal(stop=stop, request_id=request_id):
                record = ToWithdraw(request_id, reports.stops[stop].t)
            case Attributed(terminated=terminated):
                d = reports.deaths[terminated]
                record = ToDied(d.death, new(d.death.claim), d.t)
            case _:
                assert_never(key)
        if not isinstance(key, Input):  # its anchor is numbered before it, or dropped
            created[key] = created[placement.moves[key].anchor]
        out.append(Out(seq, key, created[key], record))
    return tuple(out)


# ----- the 0.4.0 rows -----------------------------------------------------------------


class _Written(StrEnum):
    """The topics format 0.4.0 defines that the step writes in place of another
    or anew, as it spells them (lifecycle-v0.6, subscription-v0.4,
    supervisor-v0.5, value-v0.3). A ``nak``, a stop and a topic 0.3.0 does not
    define keep their own."""

    STARTED = "lifecycle.started"
    COMMIT = "lifecycle.commit"
    HEARTBEAT = "lifecycle.heartbeat"
    STOPPED = "lifecycle.stopped"
    WITHDRAW = "control.withdraw"
    LAUNCHED = "supervisor.launched"
    TERMINATED = "supervisor.terminated"
    DIED = "supervisor.died"
    VALUE = "value"


def _dumps(body: _Body) -> str:
    return json.dumps(body, separators=(",", ":"))  # as the channels write a body


def _commit_t(origin: Real | Synthetic) -> float:
    match origin:
        case Real(beat):
            return beat.t
        case Synthetic(t=t):
            return t
        case _:
            assert_never(origin)


def _carried(o: Out, r: _Record) -> Row:
    """A record whose body 0.4.0 keeps, written with its body's stored text, its
    name and its request id: a ``launcher.*`` record moves to ``supervisor.*``,
    and every other keeps its topic too."""
    topic = r.topic
    match r.topic:
        case _Topic.LAUNCHED:
            topic = _Written.LAUNCHED.value
        case _Topic.TERMINATED:
            topic = _Written.TERMINATED.value
    return Row(o.seq, topic, r.name, r.request_id, r.text, o.created_at)


def write(o: Out) -> Row:
    """The 0.4.0 row of an output record (§3): exactly its schema's fields, each
    present, null or ``[]`` where it does not apply. Every lifecycle record,
    death, terminated and withdrawal is written with ``name`` null; a value
    keeps its name. ``request_id`` is the launch id on a ``started``, the
    minted launch id on a backstop's ``terminated``, the stop's on a
    withdrawal, and null on every other record the step writes in place of
    another or anew.

    - ``started`` gains ``resume_from`` (M1) and ``sources: []``;
    - a commit, real or synthetic, carries its claim, watermark, parent and
      values, ``fired: []`` and ``expired: []``, and its ``t``: its
      heartbeat's, or M8's;
    - a stepless heartbeat drops ``step``;
    - the exit node drops ``final_step`` and carries what it ``honored``, its
      ``parent``, ``values`` (``[]``, or null when it discarded its open
      iteration), ``fired: []`` and ``expired: []``;
    - a value drops ``step``."""

    def row(topic: _Written, request_id: str | None, body: _Body) -> Row:
        return Row(o.seq, topic.value, None, request_id, _dumps(body), o.created_at)

    match o.record:
        case ToStarted(started=s, resume_from=resume_from):
            return row(
                _Written.STARTED,
                s.launch_id,
                {
                    "handle": s.handle,
                    "t": s.t,
                    "resume_from": resume_from,
                    "sources": [],
                },
            )
        case ToCommit() as k:
            return row(
                _Written.COMMIT,
                None,
                {
                    "claim_seq": k.claim_seq,
                    "consumed_seq": k.consumed_seq,
                    "parent": k.parent,
                    "values": list(k.values),
                    "fired": [],
                    "expired": [],
                    "t": _commit_t(k.origin),
                },
            )
        case ToHeartbeat(beat=b, claim_seq=claim, consumed_seq=consumed):
            return row(
                _Written.HEARTBEAT,
                None,
                {"claim_seq": claim, "consumed_seq": consumed, "t": b.t},
            )
        case ToExit(
            node=ExitNode(stop=stop, values=values), claim_seq=claim, parent=parent
        ):
            return row(
                _Written.STOPPED,
                None,
                {
                    "completed": stop.completed,
                    "error": stop.error,
                    "claim_seq": claim,
                    "honored": list(stop.honored),
                    "parent": parent,
                    "values": None if values is None else list(values),
                    "fired": [],
                    "expired": [],
                    "t": stop.t,
                },
            )
        case ToDied(death=d, claim_seq=claim, t=t):
            return row(
                _Written.DIED,
                None,
                {
                    "claim_seq": claim,
                    "outcome": d.outcome.value,
                    "evidence": d.evidence,
                    "t": t,
                },
            )
        case ToReap(reap=reap, t=t):
            match reap.how:
                case Exited(code=code):
                    how: _Body = {"reason": "exited", "exit_code": code, "signal": None}
                case Killed(signal=signal):
                    how = {"reason": "killed", "exit_code": None, "signal": signal}
                case _:
                    assert_never(reap.how)
            return row(_Written.TERMINATED, reap.launch_id, {**how, "t": t})
        case ToWithdraw(request_id=request_id, t=t):
            return row(_Written.WITHDRAW, request_id, {"t": t})
        case ToValue(value=v):
            body = {"value": v.value, "t": v.t}
            return Row(
                o.seq, _Written.VALUE.value, v.name, None, _dumps(body), o.created_at
            )
        case NoSeqs(record=r):
            return _carried(o, r)
        case _:
            assert_never(o.record)


# ----- the step ---------------------------------------------------------------------


@dataclass(frozen=True)
class Passes:
    """Every pass's result for one run, in pass order, each from the parsed log
    and the results before it; ``outs`` are the records ``write`` writes."""

    log: _Log
    attribution: Attribution
    classification: Classification
    reports: Reports
    synthesis: Synthesis
    linkage: Linkage
    placement: Placement
    numbering: Mapping[Key, int]
    outs: tuple[Out, ...]


def run_passes(rows: list[Row]) -> Passes:
    """THE PASS ORDER (§7), the one wiring of the passes: ``transform`` writes
    its ``outs``. ``report`` runs before ``synthesize``, which reads the
    attributed death reports for M4."""
    log = parse(rows)
    attribution = attribute(log)
    classification = classify(log)
    reports = report(log, classification)
    synthesis = synthesize(attribution, classification, reports.deaths)
    linkage = link(synthesis)
    placement = place(synthesis, linkage, reports)
    numbering = renumber(
        [x.seq for x in log.records], placement.moves, placement.dropped
    )
    outs = rename(log, synthesis, linkage, reports, placement, numbering)
    return Passes(
        log,
        attribution,
        classification,
        reports,
        synthesis,
        linkage,
        placement,
        numbering,
        outs,
    )


def _names(body: object, claim: int) -> bool:
    """Whether a body names ``claim`` by ``claim_seq``, as 0.3.0's tolerant read
    took it (``observables._claim_name`` at 63bc165): an integer, whatever else
    the body holds."""
    return (
        isinstance(body, dict)
        and "claim_seq" in body
        and _is_int(body["claim_seq"])
        and body["claim_seq"] == claim
    )


class V0_3_0_to_V0_4_0:
    FROM = "0.3.0"
    TO = "0.4.0"
    RENUMBERS: ClassVar[bool] = True

    def is_live(self, rows: list[Row]) -> bool:
        """0.3.0's liveness, by name: the latest claim is live unless a
        ``stopped`` after it names it by ``claim_seq``, whoever wrote it and
        whatever else its body holds, or its handle resolves dead. An
        unresolvable foreign handle reads as live. The launcher tier does not
        participate."""
        claim = next((x for x in reversed(rows) if x.topic == _Topic.STARTED), None)
        if claim is None:
            return False
        if any(
            x.topic == _Topic.STOPPED
            and x.seq > claim.seq
            and _names(json.loads(x.body), claim.seq)
            for x in rows
        ):
            return False
        body = json.loads(claim.body)  # asked before parse can refuse a bad one
        handle = body.get("handle") if isinstance(body, dict) else None
        if not isinstance(handle, str):
            raise MigrationError(
                f"the {_Topic.STARTED} at seq {claim.seq} carries no handle, its "
                "body not an object holding one, so whether its claim is live "
                "cannot be told"
            )
        return resolve(handle) is not False

    def transform(self, rows: list[Row]) -> list[Row]:
        """The passes, in order (``run_passes``), then the 0.4.0 rows, renumbered
        gapless from 1. Pure and deterministic: every refusal raises before a
        row is returned, so the runner's dry run refuses a run before it seals
        it."""
        return [write(o) for o in run_passes(rows).outs]
