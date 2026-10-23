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
3. **Synthesis:** M2 and M5 assign values to commits, synthesizing the commits
   M5 names.
4. **M1:** parents and ``resume_from``.
5. **M6:** the moves, then renumbering.
6. **M7 and M8:** the renames and the synthetic fields.

Passes 3-6 are not written yet, so ``transform`` refuses to write anything.

The rules:

- **Attribution.** A heartbeat belongs to the claim its ``claim_seq`` names, as
  every 0.3.0 read of it did. A value names none, so it belongs to the latest
  claim before it: the positional rule the 0.2.0 -> 0.3.0 step stated for
  episodes, an inference. A value before every claim belongs to none.
- **Classification.** Each ``stopped`` is exactly one of four:
  - **a worker's exit:** the first ``stopped`` naming a claim, when well-formed,
    as the old read implied, unless the rule for lone stops says it is a third
    party's;
  - **a third party's release,** which ends its claim: the first ``stopped``
    naming a claim when it is the reclaim tool's malformed body (M10), or when
    the rule for lone stops says so;
  - **a third party's later stop:** any ``stopped`` after the first naming the
    same claim, a malformed first one included. The claim has already ended;
  - **claimless:** one naming no claim, whatever its shape.
- **Lone stops** (``_first_is_the_workers``): the census of who wrote them
  yields the rule, which reads the log alone. Until it lands, the rule knows one
  third party: the runner's hard-crash backstop, identified by its error text,
  "subprocess exited N with no terminal record", is a third party's wherever it
  falls. Every other well-formed first ``stopped`` is its claim's exit.

**Refused**, each naming the seq:

- a ``lifecycle.started``, ``lifecycle.heartbeat`` or ``value`` whose body is not
  format 0.3.0's (lifecycle-v0.5, value-v0.2): the step writes 0.4.0 from the
  fields 0.3.0 pins, and such a body may lack them;
- a ``lifecycle.stopped`` whose body is neither 0.3.0's nor M10's
  ``{reason, honored, claim_seq}``: what it ends, and who wrote it, cannot be
  told;
- a heartbeat or ``stopped`` whose ``claim_seq`` names no claim before it: no
  0.3.0 writer wrote one, and renumbering would point it at whatever record
  took that seq.

**Known limits:**

- A displaced worker's values are attributed to its successor's claim, because
  the positional rule did so, and the log does not record which worker wrote
  them. Its heartbeats name its own claim and keep it.
- A value before every claim is no claim's, so no lineage holds it, where
  0.3.0's ``value_series`` read it.
- The rule for lone stops is a placeholder until the census replaces it: a
  third party's lone stop that is not the backstop is read as its claim's exit.
"""

from __future__ import annotations

import json
import re
from bisect import bisect_right
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar, TypeGuard

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
    """A claim (lifecycle-v0.5 ``started``), named by its seq."""

    seq: int
    handle: str
    t: float


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
    0.3.0 step named what it ended and honored."""

    seq: int
    reason: str
    honored: tuple[str, ...]
    claim_seq: int | None


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
    return _Started(r.seq, handle=r.body["handle"], t=r.body["t"])


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
    ``claim_seq`` on ``beats`` and ``stops`` names a claim before its record."""

    records: tuple[_Record, ...]
    claims: tuple[_Started, ...]
    beats: tuple[_Beat, ...]
    values: tuple[_Value, ...]
    stops: tuple[_Stop, ...]


def _names_a_claim_before_it(
    seq: int, topic: str, claim_seq: int | None, claims: set[int]
) -> None:
    if claim_seq is not None and (claim_seq not in claims or claim_seq > seq):
        raise MigrationError(
            f"the {topic} at seq {seq} names claim {claim_seq}, which is not a "
            "claim before it"
        )


def parse(rows: list[Row]) -> _Log:
    """One run's rows, in seq order, read as format 0.3.0 defines them."""
    records = tuple(
        _Record(x.seq, x.topic, x.name, x.request_id, json.loads(x.body), x.created_at)
        for x in rows
    )
    log = _Log(
        records,
        claims=tuple(_started(r) for r in records if r.topic == _Topic.STARTED),
        beats=tuple(_beat(r) for r in records if r.topic == _Topic.HEARTBEAT),
        values=tuple(_value(r) for r in records if r.topic == _Topic.VALUE),
        stops=tuple(_stop(r) for r in records if r.topic == _Topic.STOPPED),
    )
    claims = {c.seq for c in log.claims}
    for b in log.beats:
        _names_a_claim_before_it(b.seq, _Topic.HEARTBEAT, b.claim_seq, claims)
    for s in log.stops:
        _names_a_claim_before_it(s.seq, _Topic.STOPPED, s.claim_seq, claims)
    return log


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


def _first_is_the_workers(stop: _Stopped) -> bool:
    """Whether the first well-formed ``stopped`` naming a claim is the worker's
    own exit rather than a third party's release. The backstop never is: §7
    gives it to a third party by its text, wherever it falls, and that clause
    stays. The rest is THE RULE FOR LONE STOPS, a placeholder (§7, "lone
    stops"): the implementation round's census of who wrote them yields a rule
    that reads the log alone, and replaces it. Until then every other first
    ``stopped`` is the worker's."""
    return not _is_backstop(stop)


@dataclass(frozen=True)
class Classification:
    """Pass 2: every ``stopped``, by who wrote it and what it ends. Each is in
    exactly one field. ``exits`` and ``releases`` map a claim's seq to the record
    that ends it, the first ``stopped`` naming it, so no claim is in both.
    ``later`` and ``claimless`` are in seq order."""

    exits: Mapping[int, _Stopped]  # the worker's own exit
    releases: Mapping[int, _Stop]  # a third party's stop that ends its claim
    later: tuple[_Stop, ...]  # a third party's, after its claim ended
    claimless: tuple[_Stop, ...]  # names no claim


def classify(log: _Log) -> Classification:
    """The first ``stopped`` naming a claim ends it: the worker's exit, unless it
    is malformed (M10) or the rule for lone stops gives it to a third party.
    Every later one naming that claim is a third party's."""
    exits: dict[int, _Stopped] = {}
    releases: dict[int, _Stop] = {}
    later: list[_Stop] = []
    claimless: list[_Stop] = []
    for s in log.stops:
        if s.claim_seq is None:
            claimless.append(s)
        elif s.claim_seq in exits or s.claim_seq in releases:
            later.append(s)
        elif isinstance(s, _Stopped) and _first_is_the_workers(s):
            exits[s.claim_seq] = s
        else:
            releases[s.claim_seq] = s
    return Classification(exits, releases, tuple(later), tuple(claimless))


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
        to write: passes 3-6 are not written."""
        log = parse(rows)
        attribute(log)
        classify(log)
        raise NotImplementedError(
            "the 0.3.0 -> 0.4.0 step writes nothing yet: pass 3 (synthesis), "
            "pass 4 (M1), pass 5 (M6) and pass 6 (M7, M8) are not written"
        )
