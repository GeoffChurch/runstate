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
earlier passes' results to a frozen result:

1. **Claim attribution** (``attribute``): which claim each heartbeat and each
   value belongs to.
2. **Classification** (``classify``): who wrote each ``lifecycle.stopped``, and
   what it ends.
3. **Synthesis** (``synthesize``): M2 and M5 assign values to commits,
   synthesizing the commits M5 names, with M8's fields.
4. **M1** (``link``): parents and ``resume_from``.
5. **M6:** the moves, then renumbering.
6. **M7 and M8:** the renames and the synthetic fields.

Passes 5 and 6 are not written yet, so ``transform`` refuses to write anything.

The rules:

- **Attribution.** A heartbeat belongs to the claim its ``claim_seq`` names, as
  every 0.3.0 read of it did. A value names none, so it belongs to the latest
  claim before it: the positional rule the 0.2.0 -> 0.3.0 step stated for
  episodes, an inference. A value before every claim belongs to none.
- **Classification.** The first ``stopped`` naming a claim is the claim's
  **end**, one of three:
  - **a third party's release,** when it is the reclaim tool's malformed body
    (M10), or the runner's hard-crash backstop, identified by its error text,
    "subprocess exited N with no terminal record": the backstop is a third
    party's wherever it falls;
  - otherwise the rule for lone stops decides, from the log alone: **the
    worker's exit**, as the old read implied; **a third party's release**; or
    **unattributable**, a self-report that commits nothing.

  Every later ``stopped`` naming the same claim is **a third party's later
  stop**: the claim has already ended. A ``stopped`` naming no claim is
  **claimless**, whatever its shape, the backstop's included: a death report
  must name a claim.
- **Lone stops** (``_lone_stop_rule``): the implementation round's census of
  who wrote them yields the rule. Until it lands, the placeholder reads every
  one as its claim's exit.
- **Synthesis.** Each heartbeat with an integer step is a commit. A value with
  one goes to a commit of its own claim, never another's:
  - **M2:** the commit carrying its step, even when the value follows that
    beat;
  - **leading values,** below the claim's first real beat, go to its first
    commit;
  - **a claim that beat no integer step reaches no step,** the reading the
    migration takes: its integer-step values are what it wrote while it
    loaded, left as loading, and nothing is synthesized in it. A trailing
    value is one above a claim's last real beat. The reading replaces one
    that inferred a beatless claim's position from the claims before it,
    which had two self-consistent answers wherever the claim's values sat
    above the inferred step: the third place could synthesize in it or in
    the claim it was inferred from. M1's ``resume_from`` for such a claim
    (pass 4) reads the commits after synthesis, as §7's pass order says;
  - **M5** synthesizes a commit in exactly three places (``Place``): a tick
    skipped between two real beats, even one with no values; a trailing step,
    above the claim's last real beat, at or below a non-error exit's last
    step; and a trailing step s, whatever ended its claim, of the claim M1
    will choose for a later claim whose first real beat is s+1. That claim is
    the newest one before it, in claim order, to reach step s, by a commit or
    by trailing values (``_newest_reaching``, which M1 reuses).

  "The exit's last step" is the 0.3.0 ``final_step``; a null one reached no
  step. Only an exit node has one: a claim a third party released, or that
  nothing ended, has no last step to be above. Every other value is left
  uncommitted, with the first reason that holds (``Uncommitted``): stepless,
  a register (M3); before every claim; after its claim's end, whatever
  ended it (M4); a claim's that beat no step (loading); above its claim's
  exit's last step; or a trailing step no exit vouches for, which
  M5's third place does not reach.
- **The exit node.** The worker's exit holds ``[]``, or null when it errored:
  it discarded its open iteration. A stop the rule for lone stops cannot
  attribute holds null, since it never vouches. A third party's release
  becomes a death, not an exit node.
- **M8.** A synthetic commit takes ``t`` from the last value it commits that
  carries one, else from the real heartbeat before it in its claim; and
  ``consumed_seq`` from that heartbeat. M5 synthesizes only above a claim's
  first real beat, so that heartbeat always exists, and M8's further
  fallbacks never apply.
- **M1** (``link``) reads the commits after synthesis, in claim order, never
  by step nearness, and chooses a claim's resume point among its
  **candidates**: the commits placed below its ``started``, whose placement
  depends on no later record, through their parents too. A claim's later
  commits take its previous commit as ``parent``. Its first commit, at old
  step s, takes the candidate at s-1 of the newest claim before it to hold
  one; at step 0 it is a fresh start, and with no candidate at s-1 its parent
  is null, a parentless resume. ``started.resume_from`` is the first commit's
  parent, and a claim that beat no integer step takes, as an inference, the
  newest candidate of the newest claim before it to hold one. Each rule is a
  ``ResumeFrom`` variant, so a report can name it. A commit is the parent
  even where an extendable exit follows it. The exit node's ``parent`` is the
  claim's position: its newest commit, else its resume point.

**Refused**, each naming the seq:

- a ``lifecycle.started``, ``lifecycle.heartbeat`` or ``value`` whose body is not
  format 0.3.0's (lifecycle-v0.5, value-v0.2): the step writes 0.4.0 from the
  fields 0.3.0 pins, and such a body may lack them;
- a ``lifecycle.stopped`` whose body is neither 0.3.0's nor M10's
  ``{reason, honored, claim_seq}``: what it ends, and who wrote it, cannot be
  told;
- a heartbeat or ``stopped`` whose ``claim_seq`` names no claim before it: no
  0.3.0 writer wrote one, and renumbering would point it at whatever record
  took that seq;
- a heartbeat at or below an integer step its claim already beat: an in-claim
  rewind (M9), two heartbeats of one step or a step below an earlier one. A
  0.4.0 writer records a rewind, and nothing in 0.3.0 says where one resumed.

**Known limits:**

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
- The rule for lone stops is a placeholder until the census replaces it: a
  third party's lone stop that is not the backstop is read as its claim's exit,
  and none is read as unattributable.
- An unattributable stop's ``final_step`` caps its claim's trailing values,
  as a worker's exit's does, though who wrote it is unknown. The census's rule
  for lone stops must revisit this.
- A claim that beat no integer step reaches no step, even one that finished
  a whole step and checkpointed it before it crashed: nothing is synthesized
  in it, so the next claim's first commit may be a parentless resume, which
  P3 names.
- A claim that beat no integer step resumes, by inference, from the newest
  candidate before it: the log does not say which checkpoint it loaded.
- A claim that loaded a checkpoint a displaced worker saved after the claim
  began has no candidate at the step below its first: its first commit is a
  parentless resume, which P3 names. A 0.3.0 worker claimed and then loaded,
  where a 0.4.0 worker loads first, and ``resume_from`` must name a node below
  its ``started``.
- M5's third place and M1 can choose different claims. The third place asks
  which claim before a resuming one reached step s at all; M1 asks only among
  the candidates, which depend on the parents M1 itself sets, so pass 3
  cannot ask it. They differ only where a displaced worker beat after the
  resuming claim began. M1 then takes an older claim's candidate at s, or
  none, a parentless resume. The third place synthesizes a commit M1 then
  passes over only when values pass 1 gives its claim precede the resuming
  claim while that claim's own beat below them follows it, which takes a
  second displaced worker's values; nothing continues such a commit. Either
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
    """One record with its body parsed: a 0.3.0 envelope (envelope-v0.2), and
    the time it was written."""

    seq: int
    topic: str
    name: str | None
    request_id: str | None
    body: _Body
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
    """A value's body (value-v0.2). Its envelope ``name`` is the record's."""

    seq: int
    value: Any
    step: int | None
    t: float | None


@dataclass(frozen=True)
class _Stopped:
    """A ``stopped`` as lifecycle-v0.5 pins it."""

    seq: int
    completed: bool
    error: str | None
    final_step: int | None
    claim_seq: int | None
    honored: tuple[str, ...]
    t: float


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


def _fits(body: _Body, shape: dict[str, _Test]) -> bool:
    return body.keys() == shape.keys() and all(
        test(body[k]) for k, test in shape.items()
    )


def _unreadable(r: _Record, shape: str) -> MigrationError:
    return MigrationError(
        f"the {r.topic} at seq {r.seq} is not {shape}, so this step cannot read it"
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


def _value(r: _Record) -> _Value:
    if not _fits(r.body, _VALUE):
        raise _unreadable(r, "format 0.3.0's (value-v0.2)")
    return _Value(r.seq, value=r.body["value"], step=r.body["step"], t=r.body["t"])


def _stop(r: _Record) -> _Stop:
    b = r.body
    if _fits(b, _STOPPED) and not (b["completed"] and b["error"] is not None):
        return _Stopped(
            r.seq,
            completed=b["completed"],
            error=b["error"],
            final_step=b["final_step"],
            claim_seq=b["claim_seq"],
            honored=tuple(b["honored"]),
            t=b["t"],
        )
    if _fits(b, _MALFORMED):
        return _Malformed(
            r.seq,
            reason=b["reason"],
            honored=tuple(b["honored"]),
            claim_seq=b["claim_seq"],
            created_at=r.created_at,
        )
    raise _unreadable(
        r,
        "format 0.3.0's (lifecycle-v0.5), nor the reclaim tool's malformed "
        "{reason, honored, claim_seq} (M10): what it ends cannot be told",
    )


@dataclass(frozen=True)
class _Log:
    """One run's 0.3.0 records, read once at the boundary: every record, and
    the typed bodies of those the passes interpret, each in seq order. Every
    ``claim_seq`` on ``beats`` and ``stops`` names a claim before its record,
    and each claim's integer steps rise."""

    records: tuple[_Record, ...]
    claims: tuple[_Started, ...]
    beats: tuple[_Beat, ...]
    values: tuple[_Value, ...]
    stops: tuple[_Stop, ...]


def _names_a_claim_before_it(
    seq: int, topic: _Topic, claim_seq: int | None, claims: set[int]
) -> None:
    """``claims`` holds the claims before the record at ``seq``."""
    if claim_seq is not None and claim_seq not in claims:
        raise MigrationError(
            f"the {topic} at seq {seq} names claim {claim_seq}, which is not a "
            "claim before it"
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
    refusal names the first record, by seq, that the step cannot read."""
    records: list[_Record] = []
    claims: list[_Started] = []
    beats: list[_Beat] = []
    values: list[_Value] = []
    stops: list[_Stop] = []
    before: set[int] = set()  # the claims so far
    stepped: dict[int, _Stepped] = {}  # each claim's latest integer step so far
    for x in rows:
        r = _Record(
            x.seq, x.topic, x.name, x.request_id, json.loads(x.body), x.created_at
        )
        records.append(r)
        match r.topic:
            case _Topic.STARTED:
                claims.append(_started(r))
                before.add(r.seq)
            case _Topic.HEARTBEAT:
                b = _beat(r)
                _names_a_claim_before_it(r.seq, _Topic.HEARTBEAT, b.claim_seq, before)
                if b.step is not None:
                    _not_a_rewind(b.seq, b.step, stepped.get(b.claim_seq))
                    stepped[b.claim_seq] = _Stepped(b.step, b.seq)
                beats.append(b)
            case _Topic.VALUE:
                values.append(_value(r))
            case _Topic.STOPPED:
                stop = _stop(r)
                _names_a_claim_before_it(r.seq, _Topic.STOPPED, stop.claim_seq, before)
                stops.append(stop)
    return _Log(
        tuple(records), tuple(claims), tuple(beats), tuple(values), tuple(stops)
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
_BACKSTOP = re.compile(r"subprocess exited -?\d+ with no terminal record")


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


@dataclass(frozen=True)
class _Unattributed:
    """A lone stop the rule for lone stops cannot attribute: it stays a
    self-report that commits nothing (``values: null``)."""

    stop: _Stopped


End = _Exit | _Release | _Unattributed
"""How a claim ended: the first ``stopped`` naming it, by who wrote it. A later
pass matches on the variant, ending in ``assert_never``."""


class _Later(NamedTuple):
    """A third party's ``stopped`` after the first naming the same claim."""

    claim: int
    stop: _Stop


def _lone_stop_rule(stop: _Stopped) -> End:
    """THE RULE FOR LONE STOPS, a placeholder (§7, "lone stops"): how the first
    ``stopped`` naming a claim ended it, when it is well-formed and not the
    backstop. The implementation round's census of who wrote such stops yields
    a rule that reads the log alone, and replaces this body. Until then every
    one is the worker's exit."""
    return _Exit(stop)


@dataclass(frozen=True)
class Classification:
    """Pass 2: every ``stopped``, by who wrote it and what it ends. Each is in
    exactly one field. ``ends`` maps each ended claim's seq to its end, the
    first ``stopped`` naming it. ``later`` and ``claimless`` are in seq
    order."""

    ends: Mapping[int, End]
    later: tuple[_Later, ...]
    claimless: tuple[_Stop, ...]  # names no claim, so ends none


def classify(log: _Log) -> Classification:
    """The first ``stopped`` naming a claim ends it. It is a third party's
    release when it is malformed (M10) or the backstop, which is a third
    party's wherever it falls; otherwise the rule for lone stops decides.
    Every later one naming that claim is a third party's."""
    ends: dict[int, End] = {}
    later: list[_Later] = []
    claimless: list[_Stop] = []
    for s in log.stops:
        claim = s.claim_seq
        if claim is None:
            claimless.append(s)
        elif claim in ends:
            later.append(_Later(claim, s))
        elif isinstance(s, _Malformed) or _is_backstop(s):
            ends[claim] = _Release(s)
        else:
            ends[claim] = _lone_stop_rule(s)
    return Classification(ends, tuple(later), tuple(claimless))


# ----- pass 3: synthesis ----------------------------------------------------------


class Place(StrEnum):
    """The three places M5 synthesizes a step commit, and the only three:

    - a tick skipped between two real beats of one claim, even one with no
      values;
    - a trailing step at or below a non-error exit's last step;
    - a trailing step s, whatever ended its claim, of the claim M1 will choose
      for a later claim whose first real beat is s+1."""

    SKIPPED_TICK = "skipped tick"
    BEFORE_A_CLEAN_EXIT = "trailing step before a non-error exit"
    RESUMED_FROM = "trailing step a later claim resumed from"


class Uncommitted(StrEnum):
    """Why a value stays outside every lineage. A value takes the first reason
    that holds, in this order:

    - it is stepless, a register (M3), wherever it stands;
    - it is before every claim, so no claim's (pass 1);
    - it is after its claim's end, whatever ended the claim (M4);
    - it is a claim's that beat no integer step, which so reached no step:
      what it wrote while it loaded (P3's loading cells);
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
    """A commit M5 synthesizes, at ``place``, with M8's fields:

    - ``t`` from the last value it commits that carries one, else from the real
      heartbeat before it in its claim;
    - ``consumed_seq`` from that heartbeat.

    M5 synthesizes only above a claim's first real beat, so that heartbeat
    always exists, and M8's further fallbacks (the ``created_at`` of the record
    it is placed after; a ``consumed_seq`` of 0) never apply. Its ``fired`` and
    ``expired`` are ``[]``, as on every migrated node."""

    place: Place
    t: float
    consumed_seq: int


@dataclass(frozen=True)
class Commit:
    """A commit at old step ``step``, and the seqs of the values it commits, in
    seq order."""

    step: int
    values: tuple[int, ...]
    origin: Real | Synthetic


@dataclass(frozen=True)
class ExitNode:
    """A claim's end that becomes its exit node: the worker's exit, or a stop the
    rule for lone stops cannot attribute, which stays a self-report. ``values``
    are what it holds: ``()`` when it vouches for its iteration (a non-error
    exit), None when it discards its open iteration (an error exit, or an
    unattributed stop, which never vouches). Its ``parent`` is its claim's
    newest commit, else its resume point (pass 4)."""

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
        case _Unattributed(stop):
            return ExitNode(stop, None)
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
    ``synthesized_at`` place) to the values it commits. ``trailing`` maps each trailing step that only M5's third place
    can commit to its values. ``left`` holds the values no commit can hold."""

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


def _read(claim: Claim, end: End | None) -> _Reading:
    node = _exit_node(end)
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
        elif end is not None and v.seq > end.stop.seq:
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
    """THE CLAIM M1 WILL CHOOSE: the index of the newest claim before the claim
    at index ``before``, in claim order, whose ``reached`` steps hold ``step``;
    None when no claim's do. Claim order, never step nearness (Review Focus 2).

    M5's third place asks it of the steps each claim reached by a commit or by
    trailing values, before synthesis. Pass 4 (M1) asks it of the steps of
    each claim's candidates, after synthesis, for a first commit's parent at
    the step below it. Without a displaced worker the two choose the same
    claim, since the third place turns the trailing step it chooses into a
    candidate; where they differ is a known limit (the module docstring)."""
    for i in range(before - 1, -1, -1):
        if step in reached[i]:
            return i
    return None


def _synthetic(place: Place, values: Sequence[_Value], previous: _Beat) -> Synthetic:
    """M8. ``previous`` is the claim's real heartbeat before the commit."""
    dated = [v.t for v in values if v.t is not None]
    return Synthetic(place, dated[-1] if dated else previous.t, previous.consumed_seq)


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


def synthesize(attribution: Attribution, classification: Classification) -> Synthesis:
    """M2 and M5 assign each value to a commit of its claim, synthesizing the
    commits M5 names; every other value is left, with its reason. Each claim
    is read alone first, then M5's third place looks across claims; no claim's
    reading depends on another's, so nothing is circular."""
    readings = [
        _read(c, classification.ends.get(c.started.seq)) for c in attribution.claims
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


def _placed_after(k: Commit) -> int:
    """The newest input record a commit's placement depends on, its parent's
    aside: its own heartbeat when it is real, its values, and the record its
    ``consumed_seq`` names (0 names none). M6 places a commit after its last
    value, its parent and that record. For a candidate the beats decide it,
    its own and its parents': pass 1 gives an earlier claim only values below
    the next claim's ``started``, and a watermark is below its beat."""
    match k.origin:
        case Real(beat):
            return max((beat.seq, beat.consumed_seq, *k.values))
        case Synthetic(consumed_seq=consumed):
            return max((consumed, *k.values))
        case _:
            assert_never(k.origin)


def _refs(c: ClaimCommits) -> list[CommitRef]:
    return [CommitRef(c.claim.started.seq, k.step) for k in c.commits]


def _resume_from(
    claims: Sequence[ClaimCommits], placed_after: Mapping[CommitRef, int], i: int
) -> ResumeFrom:
    """M1's resume point for the claim at index ``i``, among its candidates:
    the commits of the claims before it whose placement depends only on records
    below its ``started``. ``placed_after`` maps each of their commits to the
    newest such record, its parents' included."""
    below = claims[i].claim.started.seq
    held = [[x for x in _refs(c) if placed_after[x] < below] for c in claims[:i]]
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
    candidates, the commits placed below the claim's ``started``: one whose
    placement depends on a later record, a displaced worker's commit made or
    completed after the claim, is never its resume point."""
    claims = synthesis.claims
    resume_from: dict[int, ResumeFrom] = {}
    parents: dict[CommitRef, CommitRef | None] = {}
    exits: dict[int, CommitRef | None] = {}
    placed_after: dict[CommitRef, int] = {}
    for i, c in enumerate(claims):
        claim = c.claim.started.seq
        start = _resume_from(claims, placed_after, i)
        resume_from[claim] = start
        position = start.node  # a started leaves its resume point
        for k in c.commits:
            ref = CommitRef(claim, k.step)
            parents[ref] = position
            placed_after[ref] = max(
                _placed_after(k), 0 if position is None else placed_after[position]
            )
            position = ref  # a commit leaves itself
        if c.exit is not None:
            exits[claim] = position
    return Linkage(resume_from, parents, exits)


# ----- the step ---------------------------------------------------------------------


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
        handle = json.loads(claim.body).get("handle")
        if not isinstance(handle, str):
            raise MigrationError(
                f"the claim at seq {claim.seq} carries no handle, so whether it "
                "is live cannot be told"
            )
        return resolve(handle) is not False

    def transform(self, rows: list[Row]) -> list[Row]:
        """Runs the passes written so far, whose refusals stand, then refuses
        to write: passes 5 and 6 are not written."""
        log = parse(rows)
        link(synthesize(attribute(log), classify(log)))
        raise NotImplementedError(
            "the 0.3.0 -> 0.4.0 step writes nothing yet: pass 5 (M6) and pass 6 "
            "(M7, and the records M8's fields go on) are not written"
        )
