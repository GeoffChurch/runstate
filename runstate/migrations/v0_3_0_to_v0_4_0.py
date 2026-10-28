"""Format 0.3.0 -> 0.4.0: commits and lineage (docs/specs/commits-and-lineage.md §7).

Format 0.3.0 numbered a run's iterations by the ``step`` of its heartbeats and
values; 0.4.0 records them as nodes (lifecycle-v0.6, supervisor-v0.5,
value-v0.3, subscription-v0.4). This step writes the conforming 0.4.0 lineage that
records the 0.3.0 history's own events, under the consumer's word (§7, "The
target"). It is the only step that renumbers, and the last that may (log-formats.md
§6): from 0.4.0 on, checkpoints hold seqs.

A run goes through four parts, in this order:

1. **The boundary** (``parse``): every record read as 0.3.0 pins it, and refused
   where the step cannot carry it (below).
2. **The candidates** (``v0_3_0_to_v0_4_0_engine``): the 0.4.0 lineages of every
   conforming 0.3.0 history that writes the log and satisfies the consumer's stated
   facts and its classifier's answers (``FACTS``; §7, "The writer facts"). A
   statement of none is the whole domain of every fact (``UNSTATED``). In a log born
   in format 0.2.0, the names the earlier step filled are its inference, never their
   writer's word. The step reads the birth from evidence, the root's manifest or the
   store's trace of the log in 0.2.0, else from the consumer's statement, and refuses a
   run neither decides (``_born``).
3. **The decode** (``_decide``): each fork of a subject's End, Span, Origin or
   Writer, asked of the chooser its projection was given, in the decode's stated
   order (subjects from the last claim back to the first, then the run; for each,
   End, Span, Origin, Writer; positions in log order), the candidates through each
   answer kept; then the end-time fork of each commit and rewind no record dates
   (``END_TIME``). A
   projection no ``--resolve`` decides refuses at its first fork.
4. **The writing** (``_layout``, ``_numbered``): the decided lineage written as
   0.4.0 records under runstate's encoding rules, the same for every consumer
   (§7, E1 and E2), and renumbered once:
   - each input record stands where it stood (E2): a ``started`` gains its
     ``resume_from`` and ``sources: []``; a heartbeat that dates a commit, the tick
     that follows its close at once, becomes that commit, and any other a
     ``lifecycle.heartbeat`` without ``step``; a value drops ``step``; a
     ``launcher.*`` record moves to ``supervisor.*``; a stop, a ``nak`` and a user's
     topic are carried as they are;
   - a ``stopped`` becomes its claim's exit node (``parent``, ``values``, and the
     ``honored`` its native drains give, E2), a death report in its place (E1:
     ``migrated: stopped[: <its own evidence>]``), a ``supervisor.terminated`` under
     a launch id minted ``backstop@<seq>``, or nothing; each id a third party's or a
     fenced worker's ``stopped`` honours, not already withdrawn, is a
     ``control.withdraw`` just after it, dated by it;
   - a death report attributed from a launch's ``terminated`` stands just after it
     (``migrated: reap: signal s`` or ``exit n``), and one a later claim's gate
     proves (``migrated: gate: <handle>``) just before that claim's ``started``;
   - a commit or a rewind no record dates stands just after the latest of its
     interval's first record and its claim's own last record before it, its ``t``
     the end-time chooser's, its ``consumed_seq`` the newest control record before
     it;
   - every seq-valued field is renamed to the record it named; each input record
     keeps its ``created_at``, and a record the step adds takes that of the record
     it stands after (a gate's death, that of the claim it precedes).

Where each divergence fires is computed from the decided lineage and the input
(``_diverged``; §7, "The divergences"): each a rule under which a read of the
migrated log answers differently from the same read of the input log.

**Refused at the boundary**, each naming the seq. ``parse`` reads the records in
seq order and refuses the first it cannot carry. Since the step writes a record whose
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
- what 0.4.0 cannot hold:
  - **a subscription's every record:** a ``control.subscribe``, a
    ``control.unsubscribe``, a ``lifecycle.bound``, which binds a lease to a
    claim, and a sample, a value bearing a ``request_id``. Refusing the
    ``bound`` too keeps every lease out of the engine, whether or not its
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
  Such a heartbeat may become a commit, which the watermark would then name.
  A watermark naming a value or any other record is read as it is;
- an envelope field 0.4.0 pins null that 0.3.0 left open and that holds a
  value: a lifecycle record's or a stop's ``name``, a heartbeat's or a
  ``stopped``'s ``request_id``. No 0.3.0 reference writer set one, and the
  step cannot carry it;
- **a ``stopped`` whose body is M10's** (log-formats.md §6, the malformed-record
  rule) under the unstated classifier: only a consumer's classifier may read
  it.

**Refused after the boundary**, each naming why:
- **no history of the stated writers writes the log** (the engine admits no
  candidate): the refusal names each fact or classifier answer whose relaxation
  alone admits one, else the pairs, and those undecided past the cap (§7, "What
  refuses");
- **past a limit:** more states in one layer than the cap, or summed over the
  layers than the budget, named by the record; under no stated facts, the refusal
  names the facts that widen the pass, and the reference preset (``reference``);
- **a run-on cut at the horizon:** a candidate closes a pass at the horizon that
  its loop may continue past, so how many commits a longer run made is open; the
  refusal names the claims and the facts that can clear it (``marker``,
  ``closing``, ``beat-cov``);
- **a log's birth that nothing decides:** no evidence shows it and the
  configuration states none (``Configuration.born``), or evidence contradicts the
  statement, naming both (``_born``);
- **a chooser's refusal,** or an answer of another shape, naming the projection
  and the chooser (``Projection.ask``);
- **a name of a record that leaves the log:** a heartbeat's ``consumed_seq``
  naming a ``stopped`` that becomes nothing (a note, a fenced exit), whose
  withdrawals stand in its place: renumbering cannot carry the name;
- **an attributed death report whose launch several ``launched`` name:** the
  claimant's handle is not known.

**Known limits:**

- The unstated facts let a log run on, rewind and load at every step to the
  horizon, so all but the smallest logs exceed the cap under them: a consumer
  states its writers' facts.
- End-time detection reports every commit not dated by one tick on every
  candidate, before the other forks are decided: an over-approximation of what
  the decode asks, which it asks only on the paths the earlier answers left.
- ``written`` picks among the paths left after the whole decode by the ``repr`` of
  their pieces, then by state number, which the pass keeps free of hash order. The
  paths left differ only in what writes no byte: which writer wrote a value, whose
  output names no writer; who wrote a ``stopped`` that becomes nothing, a fenced
  worker's own or a third party's note; and where in its interval a commit's gaps run.
  A divergence whose rule reads such a difference (``fenced-exit``,
  ``third-party-note``, ``register-value``, ``unheld-value``) fires where any path
  left has it, never by the path ``written`` picked.
- A divergence fires where its rule applies; the reads it names may still answer
  alike there (``third-party-release`` on a claim no verdict reads).
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Callable, Hashable, Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass, replace
from enum import Enum, StrEnum
from typing import Any, ClassVar, NamedTuple, TypeGuard, assert_never, cast

from ..vocabulary.handle import resolve
from . import MigrationError, Row
from . import v0_3_0_to_v0_4_0_engine as E
from .ambiguity import (
    Assumptions,
    Builtin,
    Detected,
    Divergence,
    Facts,
    Fork,
    History,
    Json,
    Limits,
    Migrated,
    Projection,
    Refusal,
    Resolutions,
)
from .v0_3_0_to_v0_4_0_engine import domain
from . import v0_3_0_to_v0_4_0_facts as V
from .v0_3_0_to_v0_4_0_facts import (
    Birth,
    Born,
    Born020,
    Born030,
    Configuration,
    Exited,
    Killed,
    LogReader,
    Note,
    Outcome,
    OwnStop,
    Release,
    Role,
    StopAnswer,
    ThirdPartyFacts,
    ThirdStop,
    ValueRole,
    WorkerFacts,
)

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
    """A value (value-v0.2): its envelope ``name``, which the boundary holds to a name
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
    """A refusal of a record 0.4.0 cannot hold, or holds otherwise."""
    return MigrationError(
        f"the {r.topic} at seq {r.seq} {what}: 0.4.0 cannot hold it, so the step "
        "refuses it"
    )


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
    Condition, which 0.4.0 keeps byte for byte. A step atom in it is refused:
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
    the typed bodies of those the step writes from, each in seq order. Every
    record is 0.3.0's, as its schemas pin it, M10's bodies excepted. Every
    ``claim_seq`` on ``beats`` and ``stops`` names a claim before its record,
    every nonzero ``consumed_seq`` names a record before its heartbeat, each
    claim's integer steps rise, and each launch has at most one
    ``terminated``. No record is a subscription's, and no stop holds a step
    atom."""

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
    refuses; one naming a stop that leaves the log is refused where it is
    renamed (``_numbered``)."""
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
    seq. Such a heartbeat may become a commit, which a watermark renamed to it
    would then name, as if the commit were a control record."""
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
    """A claim's integer steps rise. A heartbeat at or below its claim's
    latest integer step is an in-claim rewind, which 0.4.0 records with a
    ``rewind`` and 0.3.0 did not record at all."""
    if latest is not None and step <= latest.step:
        raise MigrationError(
            f"the {_Topic.HEARTBEAT} at seq {seq} is at step {step}, at or below "
            f"step {latest.step} of its claim's heartbeat at seq {latest.seq}: "
            "an in-claim rewind: 0.4.0 cannot hold it, so the step refuses it"
        )


def parse(rows: list[Row]) -> _Log:
    """One run's rows, in seq order, read as format 0.3.0 defines them. A
    refusal names the first record, by seq, that the step cannot read or that
    0.4.0 cannot hold."""
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


# ----- the facts a consumer states ---------------------------------------------------


class _AnyReader:
    """The unstated classifier's reader: every answer each record allows. A reap's
    process end is no answer it can give, since only a writer's own text says how a
    process ended."""

    def started(self, seq: int) -> frozenset[Hashable]:
        return frozenset({_ANY})

    def stopped(self, seq: int) -> frozenset[StopAnswer]:
        acts: list[Release | Note] = [Release(o) for o in Outcome]
        acts.append(Note())
        return frozenset({OwnStop(), *(ThirdStop(_ANY, a) for a in acts)})

    def value(self, seq: int) -> frozenset[ValueRole]:
        return _ROLES


class Unstated:
    """The classifier no consumer states: it reads no record's content, so a record
    whose body 0.3.0 does not define stays unread (``_unread``)."""

    def read(self, log: Sequence[Row]) -> LogReader:
        return _AnyReader()


_ANY = "any"
_ROLES = frozenset(
    ValueRole(r, register)
    for r in Role
    for register in (False, True)
    if r is not Role.STRAY or register
)

UNSTATED = Configuration(
    {
        _ANY: WorkerFacts(
            **{f.name: domain(WorkerFacts, f.name) for f in fields(WorkerFacts)}
        )
    },
    {
        _ANY: ThirdPartyFacts(
            **{f.name: domain(ThirdPartyFacts, f.name) for f in fields(ThirdPartyFacts)}
        )
    },
    Unstated(),
    None,
)
"""The facts when a consumer states none: one worker version and one third-party
version, every fact its whole domain, a classifier that answers every record's whole
domain, and no birth, so a log no evidence dates is refused."""


def _value_of(x: object) -> Json:
    if isinstance(x, Enum):
        return cast(Json, x.value)
    if isinstance(x, (frozenset, set)):
        return sorted((_value_of(v) for v in x), key=repr)
    if isinstance(x, tuple):
        return [_value_of(v) for v in x]
    return cast(Json, x)


def _describe(c: Configuration) -> Json:
    """A statement as the manifest records it: each version's facts by class, and the
    classifier's identity, since its answers are the consumer's assertions."""

    def facts(f: WorkerFacts | ThirdPartyFacts) -> dict[str, Json]:
        return {x.name: _value_of(getattr(f, x.name)) for x in fields(f)}

    kind = type(c.classifier)
    return {
        "workers": {str(k): facts(f) for k, f in c.workers.items()},
        "third_parties": {str(k): facts(f) for k, f in c.third_parties.items()},
        "classifier": f"{kind.__module__}:{kind.__qualname__}",
        "born": None if c.born is None else _value_of(c.born),
    }


FACTS: Facts[Configuration] = Facts(Configuration, UNSTATED, _describe)


class _ReferenceReader:
    """The reference preset's reader: every claim the reference Worker's, every
    ``stopped`` its own; a value the worker's, in any of its phases, since consumer code
    around the Worker may ``channel.send`` one at any point of its program (``emit``
    writes only inside a pass, and its own message sends caller-clocked points to the
    raw channel): a value with a step is a metric, which a node holds, and one without a
    register, as ``emit`` writes none. Which phases its stepped values fall in is the
    consumer's word, ``values``, which narrows these answers."""

    def __init__(self, log: Sequence[Row]) -> None:
        self._stepless = frozenset(
            x.seq
            for x in log
            if x.topic == _Topic.VALUE and json.loads(x.body).get("step") is None
        )

    def started(self, seq: int) -> frozenset[Hashable]:
        return frozenset({_REFERENCE})

    def stopped(self, seq: int) -> frozenset[StopAnswer]:
        return frozenset({OwnStop()})

    def value(self, seq: int) -> frozenset[ValueRole]:
        register = seq in self._stepless
        return frozenset(ValueRole(r, register) for r in _WORKER_ROLES)


class Reference:
    """The reference preset's classifier."""

    def read(self, log: Sequence[Row]) -> LogReader:
        return _ReferenceReader(log)


_REFERENCE = "reference"

_WORKER_ROLES = tuple(r for r in Role if r is not Role.STRAY)
"""A value's roles as the worker's own, one per phase of its program (``closing`` a
pass's last value), every one of which consumer code may write a value in."""


def reference(
    *,
    stride: frozenset[V.Stride],
    loading: frozenset[V.Loading],
    closing: frozenset[V.Closing],
    marker: frozenset[V.Marker],
    values: frozenset[V.Phase],
    loop_end: frozenset[V.LoopEnd],
    discards: frozenset[V.Discards],
    cut_save: frozenset[V.CutSave],
    save_at: frozenset[V.SaveAt],
    save_cov: frozenset[V.Saved],
    leave_save: frozenset[V.Saved],
    load: frozenset[V.Load],
    resume: frozenset[V.Resume],
    strays: frozenset[V.Stray],
    born: frozenset[V.Birth] | None,
) -> Configuration:
    """A statement a consumer may build (§7, "The writer facts"): every claim's worker
    runstate's reference ``Worker`` driven by ``steps()``, and no third party, every
    ``stopped`` the worker's own. A consumer names its own call of it, ``--assume
    mine:FACTS`` with ``FACTS = reference(...)``, giving its word.

    It states what ``worker.py`` fixes: each pass after the first labelled one past the
    last (``step += 1``) and every pass ticked after its body, the loop continuing only
    past a tick; the last pass begun as any exit's ``final_step`` (``_last_step``, set
    before the ``yield``); every stop it took in honoured, its fold at the claim, the
    newest control record its drain read as its watermark; no rewind; a takeover only
    of a halted worker (the gate, ``live_episode``).

    Every fact the consumer's own code decides is a required argument, none left wide
    unasked. A claim's first pass is labelled by the consumer's ``steps(start=...)``,
    whose ``start`` its checkpoint code reads: ``resume`` says where that is against the
    save it loaded, and ``stride`` whether the pass is at the floor so set. The Worker's
    own docs say to resume "from checkpoint step k" with ``start=k``, and what a save
    labelled ``k`` holds is the consumer's: pass ``k``'s result, so that ``start=k``
    runs pass ``k`` again (``resume`` ``at``, and ``resume_from`` names pass ``k``'s
    commit), or the state before pass ``k``, pass ``k - 1``'s result, so that
    ``start=k`` continues it (``after``, and ``resume_from`` names pass ``k - 1``'s
    commit); a consumer that saves pass ``k``'s result as ``k`` and resumes at ``k +
    1`` also resumes ``after``. The two write one log, so a consumer stating both has an
    Origin fork at each resumed claim whose ``load`` admits both saves, which detection
    reports and the origin chooser decides. ``stride`` is ``next`` where the first pass is at the floor (the resume
    point, or 0 when fresh), ``skip`` where it is above (the fact has one class per
    pass, so a consumer whose first pass skips states both, and the engine then admits a
    skip at any pass). What its clock reads before its first pass (``loading``) matters
    where its code writes a record before the first pass: ``emit`` raises there, but its
    own message sends caller-clocked points to ``channel.send``, as a resumed claim
    logging the score of the checkpoint it loaded at that checkpoint's step. Where its
    values are written (``values``: the Worker itself writes only inside a pass, and a
    subscription's samples are refused at the boundary, so any other phase is such a
    raw send); how a pass's body ends (``closing``) and whether a pass that closes
    silently wrote a value (``marker``); how its loop is left (``loop_end``); its
    checkpoints (``discards``, ``cut_save``, ``save_at``, ``save_cov``, ``leave_save``,
    ``load``); and other writers' values (``strays``). A consumer that does not know
    one passes its whole domain, ``every(...)``. Where its root's logs were born
    (``born``) is the root's history, no fact of the Worker's: where no evidence dates a
    log, it is read from this statement, and ``None`` refuses such a log
    (``Configuration``).

    It can be false of a run: a control record landing between a drain and its
    heartbeat breaks ``watermark``, an explicit ``final_step`` breaks ``final-step``.
    A false word is not silent: no history writes the log, and the refusal names the
    fact to relax."""
    return Configuration(
        {
            _REFERENCE: WorkerFacts(
                stride=stride,
                loading=loading,
                values=values,
                beat_pos=frozenset({V.BeatPos.AFTER_CLOSE}),
                beat_cov=frozenset({V.Covered.BEATEN}),
                marker=marker,
                closing=closing,
                loop_end=loop_end,
                discards=discards,
                final_step=frozenset({V.FinalStep.YIELDED}),
                error_final_step=frozenset({V.FinalStep.YIELDED}),
                cut_save=cut_save,
                save_at=save_at,
                save_cov=save_cov,
                leave_save=leave_save,
                load=load,
                rewinds=frozenset(),
                resume=resume,
                takeover=frozenset({V.Takeover.DEAD}),
                strays=strays,
                fold=frozenset({V.Fold.AT_CLAIM}),
                honored=frozenset({V.Honored.EXIT}),
                watermark=frozenset({V.Watermark.CONTROL}),
            )
        },
        {},
        Reference(),
        born,
    )


# ----- the projections, and their built-in choosers ----------------------------------


def _answer(question: E.Question, answer: object) -> E.Answer:
    """A fork's answer is one of its own."""
    if not isinstance(answer, E.Answer) or answer not in question.answers:
        raise ValueError("the answer is none of the fork's own")
    return answer


def _rank(node: E.Node | None) -> tuple[int, int, int]:
    """A node's age: a later claim's is newer, and within a claim a later commit's, its
    exit newest; no node is oldest."""
    if node is None:
        return (-1, -1, -1)
    if isinstance(node, E.CommitId):
        return (node.claim, 0, node.nth)
    return (node.claim, 1, 0)


def _commits(a: E.Answer) -> int:
    return sum(isinstance(p, (E.PCommit, E.PRewind)) for p in a.value)


def _holds(a: E.Answer) -> int:
    return sum(isinstance(p, E.PHold) for p in a.value)


def _beats(a: E.Answer) -> int:
    """How many heartbeats an answer has its subject's worker write."""
    return sum(isinstance(p, E.PBeat) for p in a.value)


def _parents(a: E.Answer) -> tuple[tuple[int, int, int], ...]:
    """The ages of the nodes an answer's commits, rewinds and exit continue."""
    out: list[tuple[int, int, int]] = []
    for p in a.value:
        if isinstance(p, (E.PCommit, E.PExit)):
            out.append(_rank(p.parent))
        elif isinstance(p, E.PRewind):
            out.append(_rank(p.target))
        elif isinstance(p, E.Resumes):
            out.append(_rank(p.resume))
    return tuple(out)


class _Least:
    """The one answer least by ``key``, or a refusal where several tie: a built-in never
    picks among answers it cannot tell apart."""

    def __init__(self, name: str, key: Callable[[E.Answer], Any]) -> None:
        self._name = name
        self._key = key

    def choose(self, question: E.Question) -> E.Answer | Refusal:
        best = min(self._key(a) for a in question.answers)
        least = [a for a in question.answers if self._key(a) == best]
        if len(least) > 1:
            return Refusal(f"{len(least)} answers tie under {self._name}")
        return least[0]


def _newest(a: E.Answer) -> tuple[tuple[int, int, int], ...]:
    return tuple((-x, -y, -z) for x, y, z in _parents(a))


def _builtin(name: str, key: Callable[[E.Answer], Any], description: str) -> Builtin:
    return Builtin(name, _Least(name, key), description)


FEWEST = _builtin(
    "fewest",
    lambda a: (_commits(a), _holds(a), _newest(a)),
    "the fewest iterations finished, then the fewest values held, then the newest "
    "nodes continued",
)
MOST = _builtin(
    "most",
    lambda a: (-_commits(a), -_holds(a), _newest(a)),
    "the most iterations finished, then the most values held, then the newest nodes "
    "continued",
)
NEWEST = _builtin(
    "newest",
    _newest,
    "continue the newest node offered (a later claim's, a later commit's)",
)

END = Projection[E.Question, E.Answer](
    name="end",
    definition="how a claim ended: its death reports, and the stops its stops answer",
    readings="the claim's death reports, withdrawals and process ends",
    generic=(),
    check=_answer,
)
SPAN = Projection[E.Question, E.Answer](
    name="span",
    definition="what a claim did: its finished iterations, where each stands and what "
    "it holds, its rewinds, and its exit",
    readings="the claim's commits (parent, values, placement), rewinds and exit node",
    generic=(FEWEST, MOST),
    check=_answer,
)
ORIGIN = Projection[E.Question, E.Answer](
    name="origin",
    definition="where a claim's state came from: the node it resumed from, if any",
    readings="the claim's resume_from: a node of the run, or none",
    generic=(NEWEST,),
    check=_answer,
)


class Unlogged(NamedTuple):
    """One place a commit may stand where no tick dates it: just after the record at
    seq ``after``; its end at or after ``low``, the newest ``t`` among its claim's own
    records at or before it there; ``next`` the ``t`` of the record after that one, None
    after the log's last."""

    after: int
    low: float
    next: float | None


class EndTime(NamedTuple):
    """When and where a silent act ended (§7, "The end-time fork"): a commit, by its
    claim's seq and its index in the claim (``E.CommitId``), or a rewind, by its claim's
    seq and the epoch it closed (``E.RewindId``); the ticks that may date it, a
    heartbeat that follows its pass's close at once (a commit's only); and each place it
    may stand where no tick dates it, in log order. A question offers a tick or a place,
    or several."""

    act: E.CommitId | E.RewindId
    ticks: tuple[E.Tick, ...]
    unlogged: tuple[Unlogged, ...]


class DatedBy(NamedTuple):
    """The reading: the tick at ``seq`` dates the commit, which takes its place and
    its ``t``."""

    seq: int


class EndedAt(NamedTuple):
    """The reading: no tick dates the commit or the rewind, which stands just after the
    record at seq ``after`` and ended at ``t``."""

    after: int
    t: float


EndTimeReading = DatedBy | EndedAt


def _offered(
    question: EndTime, *, late: bool
) -> list[tuple[float, int, EndTimeReading]]:
    """Every answer a built-in may give, each by its time and its place: a tick at its
    own ``t`` in its own place, and a place undated at its lower bound (``late``: at
    the next record's ``t``, or its lower bound where that is later, as another writer's
    clock may run behind, or after the log's last record). Each is an answer
    ``_end_time`` accepts: never a time below its place's bound."""
    out: list[tuple[float, int, EndTimeReading]] = [
        (t.t, t.seq, DatedBy(t.seq)) for t in question.ticks
    ]
    for u in question.unlogged:
        t = max(u.low, u.next) if late and u.next is not None else u.low
        out.append((t, u.after, EndedAt(u.after, t)))
    return out


class _Lower:
    """``lower``: the earliest end the log allows, by time, then by place: a tick at
    its ``t``, or a place at its lower bound."""

    def choose(self, question: EndTime) -> EndTimeReading:
        return min(_offered(question, late=False), key=lambda x: (x[0], x[1]))[2]


class _Upper:
    """``upper``: the latest end the log allows, by time, then by place: a tick at its
    ``t``, or a place at the ``t`` of the next record after it, or at its lower bound
    where that is later (the next record is another writer's, whose clock runs behind)
    or after the log's last record."""

    def choose(self, question: EndTime) -> EndTimeReading:
        return max(_offered(question, late=True), key=lambda x: (x[0], x[1]))[2]


def _end_time(question: EndTime, answer: object) -> EndTimeReading:
    """A tick the question offers, or a place it offers with a time at or after that
    place's lower bound: a commit is written after the values it holds (§3)."""
    if isinstance(answer, DatedBy):
        if answer.seq not in {t.seq for t in question.ticks}:
            raise ValueError(f"no tick at seq {answer.seq} may date it")
        return answer
    if isinstance(answer, EndedAt):
        at = {u.after: u for u in question.unlogged}.get(answer.after)
        if at is None:
            raise ValueError(f"it may stand after no record at seq {answer.after}")
        t = answer.t
        if isinstance(t, bool) or not isinstance(t, (int, float)):
            raise ValueError(f"{t!r} is no time")
        if t < at.low:
            raise ValueError(f"{t} precedes the lower bound {at.low} there")
        return EndedAt(answer.after, float(t))
    raise ValueError(f"{answer!r} is neither DatedBy nor EndedAt")


END_TIME = Projection[EndTime, EndTimeReading](
    name="end-time",
    definition="when and where a silent act ended, an iteration (a commit) or a "
    "reload (a rewind): at a tick that may date it, or undated, at a place and a time "
    "no record dates",
    readings="DatedBy(seq), a tick the log offers; or EndedAt(after, t), a place the log "
    "offers and a time at or after the newest of its claim's own records there",
    generic=(
        Builtin(
            "lower",
            _Lower(),
            "the earliest end by time, then by place: a tick at its t, or a place at "
            "its lower bound",
        ),
        Builtin(
            "upper",
            _Upper(),
            "the latest end by time, then by place: a tick at its t, or a place at the "
            "t of the next record after it, or at its lower bound where that is later",
        ),
    ),
    check=_end_time,
)

WRITER_NEWEST = _builtin(
    "newest",
    lambda a: -_beats(a),
    "the newest live worker wrote it: the decode asks the last claim first, so the "
    "answer in which the claim asked wrote it",
)
WRITER = Projection[E.Question, E.Answer](
    name="writer",
    definition="who wrote a heartbeat, in a log born in 0.2.0, where any live worker "
    "may have: 0.4.0 writes it as the heartbeat's claim_seq",
    readings="the heartbeats the claim's worker wrote",
    generic=(WRITER_NEWEST,),
    check=_answer,
)

_BY_KEY: Mapping[E.Projection, Projection[E.Question, E.Answer]] = {
    E.Projection.END: END,
    E.Projection.SPAN: SPAN,
    E.Projection.ORIGIN: ORIGIN,
    E.Projection.WRITER: WRITER,
}


# ----- the divergences -----------------------------------------------------------------

REGISTER_VALUE = Divergence(
    "register-value",
    "series: a value with a step the classifier answers a register (a stray's "
    "included), which no node holds, leaves every lineage",
)
UNHELD_VALUE = Divergence(
    "unheld-value",
    "series: a value with a step that no node holds though no register (loading, "
    "after its claim's end, of an iteration that never finished) leaves every lineage",
)
RECOUNTED_VALUE = Divergence(
    "recounted-value",
    "series: a value a node holds is read at that node's count along its lineage, "
    "where it differs from the value's own step (a leading value, a stride, a resume "
    "from outside or from no node)",
)
OFF_HEAD_VALUE = Divergence(
    "off-head-value",
    "series: a value a node holds off the newest claim's lineage (a displaced "
    "worker's branch, a claim resumed from an older node) is read on no head",
)
SEVERAL_VALUES = Divergence(
    "several-values",
    "series: one node holds several values of one name, each an element, where 0.3.0 "
    "read the newest at its step",
)
EXIT_HONORED = Divergence(
    "exit-honored",
    "the stop fold and the exit's honored (E2): a migrated exit honours the stops its "
    "native drains read, which can differ from its 0.3.0 list (E2's five cost cases)",
)
THIRD_PARTY_RELEASE = Divergence(
    "third-party-release",
    "the verdict: a third party's stopped read as a release is a death report, whose "
    "outcome is its act's, where 0.3.0 read the stopped's own fields",
)
THIRD_PARTY_NOTE = Divergence(
    "third-party-note",
    "the verdict: a third party's stopped that ends nothing (a note, a second report "
    "without a cause) leaves the log, where 0.3.0 read it as its claim's end",
)
THIRD_PARTY_REAP = Divergence(
    "third-party-reap",
    "the verdict: a third party's stopped read as a process's end is a terminated, "
    "on which a claimed run's verdict no longer rests",
)
FENCED_EXIT = Divergence(
    "fenced-exit",
    "the verdict: a worker's own stopped after a release or a takeover fenced it is "
    "no exit node and leaves the log, where 0.3.0 read it as its claim's end",
)
ATTRIBUTED_DEATH = Divergence(
    "attributed-death",
    "the verdict: a claim that 0.3.0 saw ended only by its launch's terminated gets a "
    "death report from it (an exit 0 is lost, not completed)",
)
UNATTRIBUTED_REAP = Divergence(
    "unattributed-reap",
    "the verdict: a launch's terminated that reports no claim dead (a wrapper's, "
    "another host's) leaves the newest claim naming its launch held, where 0.3.0 "
    "took it as that claim's verdict when nothing else ended it",
)

DIVERGENCES: tuple[Divergence, ...] = (
    REGISTER_VALUE,
    UNHELD_VALUE,
    RECOUNTED_VALUE,
    OFF_HEAD_VALUE,
    SEVERAL_VALUES,
    EXIT_HONORED,
    THIRD_PARTY_RELEASE,
    THIRD_PARTY_NOTE,
    THIRD_PARTY_REAP,
    FENCED_EXIT,
    ATTRIBUTED_DEATH,
    UNATTRIBUTED_REAP,
)


# ----- the candidates ---------------------------------------------------------------


def _born(history: History, stated: frozenset[Birth] | None) -> Born:
    """A log's birth (§7, "Names an earlier step filled"), never read from silence.

    From evidence first, in this order: the root's manifest entry of the 0.2.0 -> 0.3.0
    step; the store's trace of the log in format 0.2.0 (a sealed v0.2.0 log, a legacy
    tombstone, a ``sealed_runs`` row). Evidence only ever shows a birth in 0.2.0: no
    record says a log was born in 0.3.0. The log itself cannot show either: the earlier
    step's output is a log some conforming 0.3.0 history writes, so nothing in it, which
    a copy between roots would keep, tells it from a log written in 0.3.0.

    Where no evidence decides, the consumer's statement does (``Configuration.born``):
    ``{0.3.0}`` reads names as their writers' word; ``{0.2.0}``, or both, the wider
    reading, as inferences. Unstated, or stated empty, the run is refused, naming the
    statement; so is evidence the statement contradicts, naming both."""
    shown = [
        "the root's manifest entry of the 0.2.0 -> 0.3.0 step"
        for e in history.entries
        if e.from_format == Birth.V0_2_0.value
    ] + [t.where for t in history.traces if t.format == Birth.V0_2_0.value]
    if shown:
        if stated is not None and Birth.V0_2_0 not in stated:
            raise MigrationError(
                f"its log was born in format 0.2.0, as {shown[0]} shows, but the "
                f"configuration states its root's logs were born in {_births(stated)} "
                "(Configuration.born): the statement is false of this run"
            )
        return Born020()
    if stated is None or not stated:
        said = (
            "states none (Configuration.born is None, as under no --assume)"
            if stated is None
            else "states that every log of the root has evidence (born is empty)"
        )
        raise MigrationError(
            "nothing shows where its log was born: the root's manifest holds no "
            "0.2.0 -> 0.3.0 entry for it, the store no trace of it in format 0.2.0, and "
            f"the log alone cannot tell; the configuration {said}. State the births its "
            "root's logs may have, Configuration.born: {Birth.V0_3_0} where runstate "
            "0.3.0 wrote them, {Birth.V0_2_0} where a runstate that wrote no manifest "
            "migrated them from 0.2.0, both where either may hold"
        )
    return Born020() if Birth.V0_2_0 in stated else Born030()


def _births(stated: frozenset[Birth]) -> str:
    return " or ".join(sorted(b.value for b in stated)) or "no format at all"


def _unread(log: _Log, configuration: Configuration) -> None:
    """The malformed-record rule (log-formats.md §6): a ``stopped`` whose body 0.3.0 does
    not define (M10's) has no conforming writer, so only a consumer's classifier may
    read it. Under the unstated one it is refused."""
    if not isinstance(configuration.classifier, Unstated):
        return
    for s in log.stops:
        if isinstance(s, _Malformed):
            raise MigrationError(
                f"the {_Topic.STOPPED} at seq {s.seq} has a body format 0.3.0 does "
                "not define (M10's {reason, honored, claim_seq}): only a consumer's "
                "classifier may read it, and none was stated (--assume)"
            )


class _Built(NamedTuple):
    """What a run's candidates are a function of."""

    rows: str  # the rows' digest
    configuration: Configuration
    limits: Limits
    born: Born

    def same(self, other: _Built) -> bool:
        return (
            self.rows == other.rows
            and self.configuration is other.configuration
            and self.limits == other.limits
            and self.born == other.born
        )


class _Held(NamedTuple):
    """The step's memo of one run's candidates: what they were built from, and the
    graph."""

    built: _Built
    graph: E.Graph


def _digest(rows: Sequence[Row]) -> str:
    return hashlib.sha256(repr(tuple(rows)).encode()).hexdigest()


_SILENT = ("marker", "closing", "beat-cov")
"""The facts each of which, stated narrowly enough, clears a run-on cut at the horizon:
no pass closes silently (``marker``), none closes but at a value (``closing``), no loop
continues past an unticked pass (``beat-cov``). Raising --run-on clears none: a run the
facts let continue past a silent close reaches any horizon."""


def _candidates(
    rows: list[Row], configuration: Configuration, limits: Limits, born: Born
) -> E.Graph:
    """The engine's candidates, or the run's refusal naming why: past a limit (under no
    stated facts, which facts to state); a candidate cut at the horizon (which facts
    bound it); or no history at all (what blame proves or leaves undecided)."""
    try:
        g = E.candidates(
            rows,
            configuration,
            born=born,
            cap=limits.cap,
            budget=limits.budget,
            run_on=limits.run_on,
        )
    except E.Refused as exc:
        if configuration is not UNSTATED:
            raise
        widening = sorted(
            {n.value.replace("_", "-") for fs in E.FEEDS.values() for n in fs}
        )
        raise MigrationError(
            f"{exc}, under no stated facts (no --assume): every fact's whole domain "
            "leaves this log's writers too much to follow. State its writers' facts "
            '(commits-and-lineage.md §7, "The writer facts"); those that widen the '
            f"pass are {', '.join(widening)}. A consumer whose worker is runstate's "
            "reference Worker may build its statement with "
            "runstate.migrations.v0_3_0_to_v0_4_0.reference(...), which takes "
            "the facts its own code decides"
        ) from exc
    if E.admits(g):
        cut = sorted(E.reaching(g))
        if cut:
            claims = ("claim " if len(cut) == 1 else "claims ") + ", ".join(
                map(str, cut)
            )
            raise MigrationError(
                f"a candidate of {claims} closes a pass at the horizon, its loop free "
                f"to continue ({limits.run_on} labels a claim past the log's steps, "
                "--run-on): the commits of a longer run that no record pins were cut "
                "there, so how many it made is open. State a fact that rules out a "
                f"silent run that long: {', '.join(_SILENT)}"
            )
        return g
    at = (
        f"the record at seq {g.ctx.recs[g.stopped_at].seq}"
        if g.stopped_at is not None and g.stopped_at < len(g.ctx.recs)
        else "the log's end"
    )
    b = E.blame(
        rows,
        configuration,
        born=born,
        cap=limits.cap,
        budget=limits.budget,
        run_on=limits.run_on,
    )

    def names(found: tuple[tuple[E.Named, ...], ...]) -> str:
        return "; ".join(" with ".join(n.value for n in x) for x in found)

    proven = f"relaxing {names(b.names)} admits one" if b.names else "no relaxation"
    open_ = (
        f"; undecided past the cap of {b.cap}: {names(b.undecided)}"
        if b.undecided
        else ""
    )
    raise MigrationError(
        f"no history of the stated writers writes the log, stopped at {at}: "
        f"{proven}{open_}"
    )


def _site(ctx: E.Context, key: E.Key) -> int:
    return ctx.recs[key.position].seq


class _Candidates:
    """A run's candidates, handed to the decode once. ``take`` gives the graph and
    forgets it, so that while the decode narrows, the built graph is held by the
    decode's own name alone and goes when its first answer's narrowing replaces it:
    the decode then holds the graph and the one its answer leaves, never the built one
    beside them. ``ctx`` stays, for the readings' sites."""

    def __init__(self, g: E.Graph) -> None:
        self.ctx = g.ctx
        self._graph: E.Graph | None = g

    def take(self) -> E.Graph:
        g = self._graph
        assert g is not None, "the candidates were taken already"
        self._graph = None
        return g


_RECORDED = (
    E.Resumes,
    E.PDied,
    E.PWithdrawal,
    E.PTerminated,
    E.PCommit,
    E.PHold,
    E.PRewind,
    E.PExit,
    E.PBeat,
    E.CommitId,
    E.ExitId,
    E.Gaps,
    Exited,
    Killed,
    DatedBy,
    EndedAt,
)
"""The types a reading is made of, the engine's reads and the end-time answers: each
recorded by its type's name and its fields."""


def _jsonable(x: object) -> Json:
    """A reading as the manifest records it: each piece by its type and fields."""
    if isinstance(x, _RECORDED):
        fields_: dict[str, Json] = {"is": type(x).__name__}
        for k, v in x._asdict().items():
            fields_[k] = _jsonable(v)
        return fields_
    if isinstance(x, tuple):
        if type(x) is not tuple:
            raise TypeError(f"a reading holds a {type(x).__name__}, no recorded type")
        return [_jsonable(v) for v in x]
    if isinstance(x, Enum):
        return cast(Json, x.value)
    return cast(Json, x)


class _Decided(NamedTuple):
    """One run as the choosers read it: the paths every fork's answer leaves, the one
    the step writes, and each fork's reading by projection and site (by subject where
    several share a site)."""

    graph: E.Graph
    parts: tuple[tuple[E.Piece, ...], ...]
    lineage: E.Lineage
    readings: Mapping[str, Mapping[int, Json]]
    rewinds: Mapping[E.RewindId, float]  # each rewind's time, its end-time reading's

    @property
    def ctx(self) -> E.Context:
        return self.graph.ctx


def _decide(candidates: _Candidates, resolutions: Resolutions) -> _Decided:
    """The decode (§7, the decode order), each fork routed to its projection's chooser;
    then the end-time forks, subjects from the last claim back: commit by commit, which
    tick dates it, if any, else at which of the places it may stand undated it ended,
    and when; then rewind by rewind, each a place and a time. Each answer narrows the
    graph in place of the last. The candidates are taken from their holder as the
    decode is called, so no name here holds the built graph while the decode narrows
    it: the decode holds the graph and the one its answer leaves, and a chooser's
    witness one more for as long as it reads it (``E.Answer.witness``)."""
    ctx = candidates.ctx
    readings: dict[str, dict[int, dict[str, Json]]] = {
        p.name: {} for p in (END, SPAN, ORIGIN, WRITER, END_TIME)
    }

    def route(q: E.Question) -> E.Answer:
        p = _BY_KEY[q.key.projection]
        a = p.ask(resolutions, q)
        readings[p.name].setdefault(_site(ctx, q.key), {})[str(q.key.subject)] = (
            _jsonable(a.value)
        )
        return a

    # the end-time stage: one index of the decided graph, kept by every graph an answer
    # leaves, so that each question and each answer reads only its act's layers
    ends = E.end_times(
        E.decode(candidates.take(), route, cap=resolutions.assumptions.limits.cap)
    )
    ended: dict[E.CommitId, EndedAt] = {}
    for c in sorted(ends.datings(), key=lambda c: (-c.claim, c.nth)):
        d = ends.dating(c)
        # Span is decided, so every path left holds the commits Span read, and an
        # answer keeps paths that hold them
        assert d is not None
        if not d.undated and len(d.ticks) == 1:
            continue  # the log dates it: a proven site
        # the places are read off the paths that leave it undated, and an EndedAt
        # answer keeps only those (``ended_at`` drops the dated ones itself)
        offered = _unlogged(ctx, ends.places(c)) if d.undated else ()
        answer = END_TIME.ask(resolutions, EndTime(c, d.ticks, offered))
        readings[END_TIME.name].setdefault(c.claim, {})[_act(c)] = _jsonable(answer)
        if isinstance(answer, DatedBy):
            (tick,) = [t for t in d.ticks if t.seq == answer.seq]
            ends = ends.dated_by(c, tick)
        else:
            ended[c] = answer
            ends = ends.ended_at(c, answer.after, answer.t)
    rewound: dict[E.RewindId, EndedAt] = {}
    for x in sorted(E.reloads(ends.graph), key=lambda x: (-x.claim, x.epoch)):
        q = EndTime(x, (), _unlogged(ctx, ends.places(x)))
        reload = END_TIME.ask(resolutions, q)
        readings[END_TIME.name].setdefault(x.claim, {})[_act(x)] = _jsonable(reload)
        assert isinstance(reload, EndedAt)  # no tick is offered (``_end_time``)
        rewound[x] = reload
        ends = ends.ended_at(x, reload.after, reload.t)
    g = ends.graph
    parts = E.written(g)

    def when(c: E.CommitId, low: float, highs: tuple[float, ...]) -> float:
        # Every commit the written path leaves undated was asked, and the paths kept
        # are those on which it stands where its answer says, with its lower bound at
        # or before the answer's time (``E.EndTimes.ended_at``); later answers
        # only narrow them. So the bound holds on the path written.
        assert low <= ended[c].t
        return ended[c].t

    lineage = E.lineage(g.ctx, _flat(parts), when=when)
    return _Decided(
        g,
        parts,
        lineage,
        {p: {s: dict(v) for s, v in by.items()} for p, by in readings.items()},
        {x: a.t for x, a in rewound.items()},
    )


def _act(x: E.CommitId | E.RewindId) -> str:
    """An end-time reading's key within its claim: a commit's index, or a rewind's
    ``rewind <epoch>``."""
    return str(x.nth) if isinstance(x, E.CommitId) else f"rewind {x.epoch}"


def _unlogged(ctx: E.Context, found: frozenset[E.Place]) -> tuple[Unlogged, ...]:
    """The places a commit may stand undated, each once, at the least lower bound any
    path has there, in log order."""
    least: dict[int, float] = {}
    for p in found:
        least[p.after] = min(least.get(p.after, p.low), p.low)
    out: list[Unlogged] = []
    for after in sorted(least):
        k = ctx.by_seq[after] + 1
        out.append(
            Unlogged(after, least[after], ctx.recs[k].t if k < len(ctx.recs) else None)
        )
    return tuple(out)


def _flat(parts: Sequence[Sequence[E.Piece]]) -> list[E.Piece]:
    return [p for part in parts for p in part]


# ----- the 0.4.0 records ------------------------------------------------------------


class _Written(StrEnum):
    """The topics format 0.4.0 defines that the step writes in place of another or
    anew, as it spells them (lifecycle-v0.6, subscription-v0.4, supervisor-v0.5,
    value-v0.3). A ``nak``, a stop and a topic 0.3.0 does not define keep their own."""

    STARTED = "lifecycle.started"
    COMMIT = "lifecycle.commit"
    HEARTBEAT = "lifecycle.heartbeat"
    REWIND = "lifecycle.rewind"
    STOPPED = "lifecycle.stopped"
    WITHDRAW = "control.withdraw"
    LAUNCHED = "supervisor.launched"
    TERMINATED = "supervisor.terminated"
    DIED = "supervisor.died"
    VALUE = "value"


_MIGRATED = "migrated"  # E1: a death report the step writes says so


def _evidence(kind: str, own: str | None) -> str:
    """E1: ``migrated: <the 0.3.0 record's kind>[: <its own evidence>]``."""
    return f"{_MIGRATED}: {kind}" if not own else f"{_MIGRATED}: {kind}: {own}"


def _reaped(how: Exited | Killed) -> str:
    match how:
        case Exited(code=code):
            return f"exit {code}"
        case Killed(signal=signal):
            return f"signal {signal}"
        case _:
            assert_never(how)


class SeqRef(NamedTuple):
    """A seq-valued field naming an input record, by its 0.3.0 seq."""

    seq: int


class NodeRef(NamedTuple):
    """A seq-valued field naming a node of the decided lineage."""

    node: E.Node


@dataclass(frozen=True)
class NewestControl:
    """An undated commit's ``consumed_seq``: the newest control record before it, 0 for
    none, since its native drain reads there."""


_NEWEST_CONTROL = NewestControl()


def _watermark(seq: int) -> SeqRef | int:
    """A heartbeat's ``consumed_seq``: 0 names nothing, and stays 0."""
    return 0 if seq == 0 else SeqRef(seq)


def _node(node: E.Node | None) -> NodeRef | None:
    return None if node is None else NodeRef(node)


@dataclass(frozen=True)
class _Out:
    """One output record before renumbering: its topic, envelope and body, whose
    seq-valued fields are ``SeqRef``s and ``NodeRef``s; the input record it stands for, if any; the node
    it is, if any; and its ``created_at``."""

    topic: str
    name: str | None
    request_id: str | None
    body: Mapping[str, Any] | None  # None: the input record's stored text, carried
    text: str | None
    stands_for: int | None
    node: E.Node | None
    created_at: float


def _pieces(parts: Sequence[Sequence[E.Piece]], kind: type[Any]) -> list[Any]:
    return [p for part in parts for p in part if isinstance(p, kind)]


def _fresh(base: str, taken: set[str]) -> str:
    """``base``, or ``base~k`` for the least k >= 2 not in ``taken``; then taken. The
    0.2.0 -> 0.3.0 step's minting, carried here."""
    cand, k = base, 1
    while cand in taken:
        k += 1
        cand = f"{base}~{k}"
    taken.add(cand)
    return cand


def _layout(log: _Log, d: _Decided) -> list[_Out]:
    """The output records in order (§7, "Records"; E2: no record moves). Each input
    record stands where it stood, written as 0.4.0 writes what it was; a stop's
    withdrawals and an attributed death follow it at once; a death the gate proves
    stands just before the claim that passed it; and each commit or rewind no record
    dates stands just after the latest of its interval's first record and its claim's
    own last record before it, each record's in claim and commit order."""
    ctx, parts, lin = d.ctx, d.parts, d.lineage
    records = {r.seq: r for r in log.records}
    claims = {c.seq: c for c in log.claims}
    beats = {b.seq: b for b in log.beats}
    values = {v.seq: v for v in log.values}
    stops = {s.seq: s for s in log.stops}
    terminated = {t.seq: t for t in log.terminated}
    flat = [p for part in parts for p in part]
    own = E._own(flat)
    starts = {s.claim: s.resume_from for s in lin.starts}
    commits = {c.node: c for c in lin.commits}
    exits = {x.node.claim: x for x in lin.exits}
    dated = {(p.claim, p.nth): p.seq for p in _pieces(parts, E.PDate)}
    by_tick = {seq: E.CommitId(*k) for k, seq in dated.items()}
    gaps = {E.CommitId(p.claim, p.nth): p.gaps for p in _pieces(parts, E.PCommit)}
    taken = {r.request_id for r in log.records if r.request_id is not None}
    taken |= {x for s in log.stops for x in s.honored}

    def low(claim: int, g: E.Gaps) -> tuple[int, float]:
        """Where an undated record stands, the seq it follows, and its lower bound."""
        last = max(s for s in own[claim] if s <= g.last)
        at = max(g.first, last)
        return at, ctx.recs[ctx.by_seq[last]].t

    after: dict[int, list[tuple[tuple[int, int, int], _Out]]] = defaultdict(list)
    before: dict[int, list[_Out]] = defaultdict(list)
    for c in lin.commits:
        if (c.node.claim, c.node.nth) in dated:
            continue
        at, _ = low(c.node.claim, gaps[c.node])
        after[at].append(((c.node.claim, c.node.nth, 1), _commit(c, _NEWEST_CONTROL)))
    for p in _pieces(parts, E.PRewind):
        at, bound = low(p.claim, p.gaps)
        t = d.rewinds[E.RewindId(p.claim, p.epoch)]
        assert bound <= t  # the paths kept have it here, its bound at or before t
        rw = _Out(
            _Written.REWIND,
            None,
            None,
            {
                "claim_seq": SeqRef(p.claim),
                "resume_from": _node(p.target),
                "t": t,
            },
            None,
            None,
            None,
            0.0,
        )
        after[at].append(((p.claim, p.commits, 0), rw))
    for i, part in enumerate(parts[: len(ctx.recs)]):
        later = ctx.recs[i].seq
        for p in part:
            if isinstance(p, E.PDied) and p.source is E.Source.GATE:
                dead = claims[p.claim]
                before[later].append(
                    _died(p, _evidence("gate", dead.handle), claims[later].t)
                )

    out: list[_Out] = []
    for i, r in enumerate(log.records):
        part = parts[i]
        for x in before[r.seq]:
            out.append(replace(x, created_at=r.created_at))
        mine: list[_Out] = []
        match r.topic:
            case _Topic.STARTED:
                s = claims[r.seq]
                mine.append(
                    _Out(
                        _Written.STARTED,
                        None,
                        s.launch_id,
                        {
                            "handle": s.handle,
                            "t": s.t,
                            "resume_from": _node(starts[s.seq]),
                            "sources": [],
                        },
                        None,
                        r.seq,
                        None,
                        r.created_at,
                    )
                )
            case _Topic.HEARTBEAT if r.seq in by_tick:
                b = beats[r.seq]
                node = by_tick[r.seq]
                mine.append(
                    replace(
                        _commit(commits[node], _watermark(b.consumed_seq)),
                        stands_for=r.seq,
                        created_at=r.created_at,
                    )
                )
            case _Topic.HEARTBEAT:
                b = beats[r.seq]
                (beat,) = [p for p in part if isinstance(p, E.PBeat)]
                mine.append(
                    _Out(
                        _Written.HEARTBEAT,
                        None,
                        None,
                        {
                            "claim_seq": SeqRef(beat.writer),
                            "consumed_seq": _watermark(b.consumed_seq),
                            "t": b.t,
                        },
                        None,
                        r.seq,
                        None,
                        r.created_at,
                    )
                )
            case _Topic.VALUE:
                v = values[r.seq]
                mine.append(
                    _Out(
                        _Written.VALUE,
                        v.name,
                        None,
                        {"value": v.value, "t": v.t},
                        None,
                        r.seq,
                        None,
                        r.created_at,
                    )
                )
            case _Topic.STOPPED:
                mine += _stopped(stops[r.seq], part, exits, taken)
            case _Topic.LAUNCHED | _Topic.TERMINATED:
                topic = (
                    _Written.LAUNCHED
                    if r.topic == _Topic.LAUNCHED
                    else _Written.TERMINATED
                )
                mine.append(
                    _Out(
                        topic,
                        r.name,
                        r.request_id,
                        None,
                        r.text,
                        r.seq,
                        None,
                        r.created_at,
                    )
                )
            case _:
                mine.append(
                    _Out(
                        r.topic,
                        r.name,
                        r.request_id,
                        None,
                        r.text,
                        r.seq,
                        None,
                        r.created_at,
                    )
                )
        stop = stops.get(r.seq)
        for w in (p for p in part if isinstance(p, E.PWithdrawal)):
            assert stop is not None  # a withdrawal answers a stopped
            mine.append(
                _Out(
                    _Written.WITHDRAW,
                    None,
                    w.request_id,
                    {"t": stop.t if isinstance(stop, _Stopped) else stop.created_at},
                    None,
                    None,
                    None,
                    r.created_at,
                )
            )
        for p in part:
            if isinstance(p, E.PDied) and p.source is E.Source.REAP:
                reap = terminated[r.seq]
                _one_launched(log, reap)
                died = _died(p, _evidence("reap", _reaped(reap.how)), reap.t)
                mine.append(replace(died, created_at=r.created_at))
        out += mine
        for _, x in sorted(after[r.seq], key=lambda kx: kx[0]):
            out.append(replace(x, created_at=r.created_at))
    return out


def _one_launched(log: _Log, reap: _Terminated) -> None:
    """An attributed death report reads its launch's handle off the one ``launched``
    naming it: two leave the claimant unknown."""
    n = sum(1 for x in log.launched if x.launch_id == reap.launch_id)
    if n != 1:
        raise MigrationError(
            f"the {_Topic.TERMINATED} at seq {reap.seq} reports launch "
            f"{reap.launch_id!r}, which {n} {_Topic.LAUNCHED} records name: its "
            "death report needs the launch's one handle"
        )


def _commit(c: E.Commit, consumed: SeqRef | int | NewestControl) -> _Out:
    """A commit: dated, by its tick (whose watermark it keeps), or not, its ``t`` the
    end-time chooser's and its watermark the newest control record before it
    (``consumed`` None, filled where it is numbered)."""
    return _Out(
        _Written.COMMIT,
        None,
        None,
        {
            "claim_seq": SeqRef(c.node.claim),
            "consumed_seq": consumed,
            "parent": _node(c.parent),
            "values": [SeqRef(v) for v in c.values],
            "fired": [],
            "expired": [],
            "t": c.t,
        },
        None,
        None,
        c.node,
        0.0,
    )


def _died(p: E.PDied, evidence: str, t: float) -> _Out:
    return _Out(
        _Written.DIED,
        None,
        None,
        {
            "claim_seq": SeqRef(p.claim),
            "outcome": p.outcome.value,
            "evidence": evidence,
            "t": t,
        },
        None,
        None,
        None,
        0.0,
    )


def _stopped(
    stop: _Stop,
    part: Sequence[E.Piece],
    exits: Mapping[int, E.ExitNode],
    taken: set[str],
) -> list[_Out]:
    """What a ``stopped`` becomes (§7, "Records"; E1): its claim's exit node; a death
    report in its place; a process's end; or nothing, its withdrawals in its place."""
    (who,) = [p for p in part if isinstance(p, E.PStop)]
    match who.author:
        case E.Author.EXIT:
            assert isinstance(stop, _Stopped)  # no worker writes a body 0.3.0 lacks
            x = exits[who.claim] if who.claim is not None else None
            assert x is not None
            return [
                _Out(
                    _Written.STOPPED,
                    None,
                    None,
                    {
                        "completed": stop.completed,
                        "error": stop.error,
                        "claim_seq": SeqRef(x.node.claim),
                        "honored": list(x.honored),
                        "parent": _node(x.parent),
                        "values": (
                            None if x.values is None else [SeqRef(v) for v in x.values]
                        ),
                        "fired": [],
                        "expired": [],
                        "t": stop.t,
                    },
                    None,
                    stop.seq,
                    x.node,
                    stop.created_at,
                )
            ]
        case E.Author.FENCED:
            return []
        case E.Author.THIRD:
            t = stop.t if isinstance(stop, _Stopped) else stop.created_at
            own = stop.error if isinstance(stop, _Stopped) else stop.reason
            for p in part:
                if isinstance(p, E.PDied) and p.source is E.Source.STOPPED:
                    died = _died(p, _evidence("stopped", own), t)
                    return [
                        replace(died, stands_for=stop.seq, created_at=stop.created_at)
                    ]
                if isinstance(p, E.PTerminated) and p.launch is None:
                    how = _how(p.how)
                    launch = _fresh(f"backstop@{stop.seq}", taken)
                    return [
                        _Out(
                            _Written.TERMINATED,
                            None,
                            launch,
                            {**how, "t": t},
                            None,
                            stop.seq,
                            None,
                            stop.created_at,
                        )
                    ]
            return []
        case _:
            assert_never(who.author)


def _how(how: Exited | Killed) -> dict[str, Any]:
    match how:
        case Exited(code=code):
            return {"reason": "exited", "exit_code": code, "signal": None}
        case Killed(signal=signal):
            return {"reason": "killed", "exit_code": None, "signal": signal}
        case _:
            assert_never(how)


def _dumps(body: Mapping[str, Any]) -> str:
    return json.dumps(body, separators=(",", ":"))  # as the channels write a body


def _numbered(outs: Sequence[_Out], log: _Log) -> list[Row]:
    """THE RENUMBERING, once: each record its 0.4.0 seq, gapless from 1, and every
    seq-valued field renamed to the record it named. A watermark of 0 stays 0; an
    undated commit's is the newest control record before it."""
    seqs: dict[int, int] = {}
    nodes: dict[E.Node, int] = {}
    for k, o in enumerate(outs, 1):
        if o.stands_for is not None:
            seqs[o.stands_for] = k
        if o.node is not None:
            nodes[o.node] = k

    records = {r.seq: r for r in log.records}

    def ref(x: SeqRef | NodeRef, o: _Out, field: str) -> int:
        if isinstance(x, NodeRef):
            return nodes[x.node]
        if x.seq not in seqs:
            whose = (
                f"the {records[o.stands_for].topic} at seq {o.stands_for}"
                if o.stands_for is not None
                else f"a {o.topic} the step writes"
            )
            raise MigrationError(
                f"{whose} has {field} {x.seq}, which names the "
                f"{records[x.seq].topic} at seq {x.seq}, which leaves the log: "
                "renumbering cannot carry the name"
            )
        return seqs[x.seq]

    def body(o: _Out, control: int) -> dict[str, Any]:
        assert o.body is not None
        out: dict[str, Any] = {}
        for k, v in o.body.items():
            if isinstance(v, (SeqRef, NodeRef)):
                out[k] = ref(v, o, k)
            elif isinstance(v, NewestControl):
                out[k] = control
            elif isinstance(v, list):
                out[k] = [
                    ref(x, o, k) if isinstance(x, (SeqRef, NodeRef)) else x for x in v
                ]
            else:
                out[k] = v
        return out

    rows: list[Row] = []
    control = 0
    for k, o in enumerate(outs, 1):
        topic = str(o.topic)  # a _Written's value: the row holds the plain text
        if o.text is not None:
            rows.append(Row(k, topic, o.name, o.request_id, o.text, o.created_at))
        else:
            rows.append(
                Row(
                    k,
                    topic,
                    o.name,
                    o.request_id,
                    _dumps(body(o, control)),
                    o.created_at,
                )
            )
        if o.topic.startswith("control."):
            control = k
    return rows


# ----- where each divergence fires ------------------------------------------------------


def _chain(
    node: E.Node | None, parents: Mapping[E.Node, E.Node | None]
) -> list[E.Node]:
    """The nodes from ``node`` back to the root."""
    out: list[E.Node] = []
    while node is not None:
        out.append(node)
        node = parents[node]
    return out


def _head(lin: E.Lineage) -> E.Node | None:
    """The newest claim's head (LatestClaimHead, §5): its exit node, else its position
    after its last commit or rewind, else its resume point."""
    if not lin.starts:
        return None
    claim = max(s.claim for s in lin.starts)
    exit_ = next((x for x in lin.exits if x.node.claim == claim), None)
    if exit_ is not None:
        return exit_.node
    position = next(s.resume_from for s in lin.starts if s.claim == claim)
    steps: list[tuple[int, int, E.Node | None]] = [
        (c.node.nth, 1, c.node) for c in lin.commits if c.node.claim == claim
    ]
    steps += [(r.commits, 0, r.target) for r in lin.rewinds if r.claim == claim]
    for _, _, node in sorted(steps, key=lambda x: (x[0], x[1])):
        position = node
    return position


def _diverged(log: _Log, d: _Decided) -> dict[str, tuple[int, ...]]:
    """Each divergence's sites, by the 0.3.0 seq of the record its rule fires at (§7,
    "The divergences"). Each fires where its rule applies, which a read may then
    answer differently; a rule that cannot apply here fires nowhere."""
    lin, parts = d.lineage, d.parts
    sites: dict[str, set[int]] = {x.name: set() for x in DIVERGENCES}
    parents: dict[E.Node, E.Node | None] = {c.node: c.parent for c in lin.commits}
    parents |= {x.node: x.parent for x in lin.exits}
    held: dict[int, E.Node] = {}
    for c in lin.commits:
        held |= {v: c.node for v in c.values}
    for x in lin.exits:
        held |= {v: x.node for v in x.values or ()}
    count = {
        n: sum(isinstance(m, E.CommitId) for m in _chain(n, parents)) for n in parents
    }
    on_head = set(_chain(_head(lin), parents))
    # each value's writer's answers on the paths every answer left, a register or not:
    # Span reads holds, not the flag, so where the classifier answers both, both stay
    registers: dict[int, set[bool]] = defaultdict(set)
    for es in d.graph.live:
        for e in es:
            for p in e.pieces:
                if isinstance(p, E.PValue):
                    registers[p.seq].add(p.register)
    values = {v.seq: v for v in log.values}
    for v in log.values:
        if v.step is None:
            continue
        node = held.get(v.seq)
        if node is None:
            if True in registers[v.seq]:
                sites[REGISTER_VALUE.name].add(v.seq)
            if False in registers[v.seq]:
                sites[UNHELD_VALUE.name].add(v.seq)
            continue
        if count[node] - 1 != v.step:
            sites[RECOUNTED_VALUE.name].add(v.seq)
        if node not in on_head:
            sites[OFF_HEAD_VALUE.name].add(v.seq)
    for node_values in (
        *(c.values for c in lin.commits),
        *(x.values or () for x in lin.exits),
    ):
        names = [values[v].name for v in node_values]
        sites[SEVERAL_VALUES.name] |= {
            v for v in node_values if names.count(values[v].name) > 1
        }
    stops = {s.seq: s for s in log.stops}
    for p in _pieces(parts, E.PExit):
        if set(p.honored) != set(stops[p.seq].honored):
            sites[EXIT_HONORED.name].add(p.seq)
    # a stopped's author on the paths every answer left: no reading reads it where it
    # writes nothing (a fenced worker's exit, a third party's note), so where the paths
    # left read it both ways, both divergences fire; what a third party's records (a
    # death report, a process's end) is End's, decided, so every path left agrees on it
    for es in d.graph.live:
        for e in es:
            for p in e.pieces:
                if not isinstance(p, E.PStop):
                    continue
                if p.author is E.Author.FENCED:
                    sites[FENCED_EXIT.name].add(p.seq)
                elif p.author is E.Author.THIRD:
                    if any(isinstance(q, E.PDied) for q in e.pieces):
                        sites[THIRD_PARTY_RELEASE.name].add(p.seq)
                    elif any(
                        isinstance(q, E.PTerminated) and q.launch is None
                        for q in e.pieces
                    ):
                        sites[THIRD_PARTY_REAP.name].add(p.seq)
                    else:
                        sites[THIRD_PARTY_NOTE.name].add(p.seq)
    for i, part in enumerate(parts[: len(d.ctx.recs)]):
        seq = d.ctx.recs[i].seq
        if any(isinstance(p, E.PDied) and p.source is E.Source.REAP for p in part):
            sites[ATTRIBUTED_DEATH.name].add(seq)
    ended = {x.node.claim for x in lin.exits} | {
        p.claim for p in _pieces(parts, E.PDied)
    }
    for t in log.terminated:
        named = [c.seq for c in log.claims if c.launch_id == t.launch_id]
        if named and max(named) not in ended:
            sites[UNATTRIBUTED_REAP.name].add(t.seq)
    return {k: tuple(sorted(v)) for k, v in sites.items()}


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
    FACTS: ClassVar[Facts[Configuration]] = FACTS
    # in the order the decode asks them; end-time last, once the path is decided
    PROJECTIONS: ClassVar[tuple[Projection[Any, Any], ...]] = (
        END,
        SPAN,
        ORIGIN,
        WRITER,
        END_TIME,
    )
    DIVERGENCES: ClassVar[tuple[Divergence, ...]] = DIVERGENCES

    def __init__(self) -> None:
        # the last run's candidates, so detect and the transform that follows it build
        # them once; it changes no result
        self._last: _Held | None = None

    def _candidates(
        self,
        rows: list[Row],
        configuration: Configuration,
        limits: Limits,
        history: History,
    ) -> E.Graph:
        built = _Built(
            _digest(rows), configuration, limits, _born(history, configuration.born)
        )
        if self._last is not None and self._last.built.same(built):
            return self._last.graph
        # the previous run's graph goes before this one is built: memory holds one
        # graph at a time, which the budget bounds
        self._last = None
        g = _candidates(rows, configuration, limits, built.born)
        self._last = _Held(built, g)
        return g

    def _taken(
        self,
        rows: list[Row],
        configuration: Configuration,
        limits: Limits,
        history: History,
    ) -> _Candidates:
        """The candidates for a transform, which narrows them answer by answer: the
        step lets go of them, and hands them over in a holder the decode takes them
        from, so that the decode holds the graph and the one its answer leaves and
        nothing else holds a third. A transform of the same run again (the runner's
        memo full) builds them again, which changes no result."""
        g = self._candidates(rows, configuration, limits, history)
        self._last = None
        return _Candidates(g)

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

    def detect(
        self, rows: list[Row], *, assumptions: Assumptions, history: History
    ) -> Detected:
        """The forks of End, Span, Origin and Writer, each with the facts whose statement
        could settle it; and an end-time fork, at its claim's seq, for each commit that
        some candidate holds and no tick dates on every one, or that several ticks may
        date, and for each rewind, which no record dates."""
        configuration = FACTS.of(assumptions)
        _unread(parse(rows), configuration)
        g = self._candidates(rows, configuration, assumptions.limits, history)
        keys = E.forked(g, cap=assumptions.limits.cap)
        forks: dict[str, list[Fork]] = {p.name: [] for p in self.PROJECTIONS}
        for k in sorted(keys, key=lambda k: (k.position, k.subject)):
            forks[_BY_KEY[k.projection].name].append(
                Fork(
                    _site(g.ctx, k), E.settled_by(configuration, E.FEEDS[k.projection])
                )
            )
        dates = E.settled_by(configuration, E.DATES)
        forks[END_TIME.name] = [
            Fork(c.claim, dates)
            for c, d in sorted(E.end_times(g).datings().items())
            if d.undated or len(d.ticks) > 1
        ] + [Fork(x.claim, ()) for x in sorted(E.reloads(g))]
        return Detected({n: tuple(fs) for n, fs in forks.items()})

    def transform(
        self, rows: list[Row], *, resolutions: Resolutions, history: History
    ) -> Migrated:
        """The candidates of the stated facts (the engine), each fork decided by its
        projection's chooser in the decode's order, the decided path written as 0.4.0
        records, renumbered gapless from 1. Pure and deterministic: every refusal
        raises before a row is returned, so the runner's dry run refuses a run before
        it seals it."""
        a = resolutions.assumptions
        configuration = FACTS.of(a)
        log = parse(rows)
        _unread(log, configuration)
        d = _decide(self._taken(rows, configuration, a.limits, history), resolutions)
        out = _numbered(_layout(log, d), log)
        return Migrated(out, d.readings, _diverged(log, d))
