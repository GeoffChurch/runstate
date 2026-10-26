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
   in format 0.2.0, which the root's manifest records (``_born``), the names the
   earlier step filled are its inference, never their writer's word.
3. **The decode** (``_decide``): each fork of a subject's End, Span or Origin,
   asked of the chooser its projection was given, in the decode's stated order
   (subjects from the last claim back to the first, then the run; for each, End,
   Span, Origin; positions in log order), the candidates through each answer kept;
   then the end-time fork of each commit no record dates (``END_TIME``). A
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
- what M9 refuses:
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
  layers than the budget, named by the record;
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
- The end-time fork is detected per claim, on the commits no record dates on any
  candidate; a commit dated on some candidates and not on the decided one asks the
  end-time chooser without having been reported.
- ``written`` picks among the paths left after the decode, which read alike, by
  the ``repr`` of their pieces; two edges with the same pieces to different states
  are told apart by their state's number, which hash order can change.
- A divergence fires where its rule applies; the reads it names may still answer
  alike there (``third-party-release`` on a claim no verdict reads).
"""

from __future__ import annotations

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
from .v0_3_0_to_v0_4_0_facts import (
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
    the typed bodies of those the step writes from, each in seq order. Every
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
)
"""The facts when a consumer states none: one worker version and one third-party
version, every fact its whole domain, and a classifier that answers every record's
whole domain."""


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
    }


FACTS: Facts[Configuration] = Facts(Configuration, UNSTATED, _describe)


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


class EndTime(NamedTuple):
    """An undated commit's end time (§7, "The end-time fork"): its lower bound, the
    newest ``t`` among its claim's own records at or before it, and its upper bounds,
    the ``t`` of the record after each gap it may stand in, earliest first."""

    claim: int
    nth: int
    low: float
    highs: tuple[float, ...]


class _Lower:
    def choose(self, question: EndTime) -> float:
        return E.lower(E.CommitId(question.claim, question.nth), question.low, ())


class _Upper:
    def choose(self, question: EndTime) -> float:
        c = E.CommitId(question.claim, question.nth)
        return E.upper(c, question.low, question.highs)


def _end_time(question: EndTime, answer: object) -> float:
    """An end time is a number at or after the lower bound: a commit is written after
    the values it holds (§3)."""
    if isinstance(answer, bool) or not isinstance(answer, (int, float)):
        raise ValueError(f"{answer!r} is no time")
    if answer < question.low:
        raise ValueError(f"{answer} precedes the lower bound {question.low}")
    return float(answer)


END_TIME = Projection[EndTime, float](
    name="end-time",
    definition="when an iteration ended that no record dates",
    readings="a time at or after the newest of its claim's own records before it",
    generic=(
        Builtin("lower", _Lower(), "the newest t of the claim's own records before it"),
        Builtin("upper", _Upper(), "the t of the next record after it"),
    ),
    check=_end_time,
)

_BY_KEY: Mapping[E.Projection, Projection[E.Question, E.Answer]] = {
    E.Projection.END: END,
    E.Projection.SPAN: SPAN,
    E.Projection.ORIGIN: ORIGIN,
}


# ----- the divergences -----------------------------------------------------------------

UNHELD_VALUE = Divergence(
    "unheld-value",
    "series: a value with a step that no node holds (loading, a register, a stray, "
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


def _born(history: History) -> Born:
    """A log's birth, from the root's manifest (§7, "Names an earlier step filled"): an
    entry of the 0.2.0 -> 0.3.0 step means the earlier step filled its names. No entry
    means born in 0.3.0, which holds because no consumer ran that step before the
    manifest existed."""
    if any(e.from_format == "0.2.0" for e in history):
        return Born020()
    return Born030()


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


def _candidates(
    rows: list[Row], configuration: Configuration, limits: Limits, history: History
) -> E.Graph:
    """The engine's candidates, or the run's refusal naming why: what blame proves
    or leaves undecided, or the record past a limit."""
    born = _born(history)
    g = E.candidates(
        rows,
        configuration,
        born=born,
        cap=limits.cap,
        budget=limits.budget,
        run_on=limits.run_on,
    )
    if E.admits(g):
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


def _site(g: E.Graph, key: E.Key) -> int:
    return g.ctx.recs[key.position].seq


def _jsonable(x: object) -> Json:
    """A reading as the manifest records it: each piece by its type and fields."""
    if isinstance(x, tuple) and hasattr(x, "_fields"):
        return {
            "is": type(x).__name__,
            **{f: _jsonable(getattr(x, f)) for f in x._fields},
        }
    if isinstance(x, tuple):
        return [_jsonable(v) for v in x]
    if isinstance(x, Enum):
        return cast(Json, x.value)
    if is_dataclass(x):
        return {"is": type(x).__name__}
    return cast(Json, x)


class _Decided(NamedTuple):
    """One run as the choosers read it: the path the step writes, and each fork's
    reading by projection and site (by subject where several share a site)."""

    ctx: E.Context
    parts: tuple[tuple[E.Piece, ...], ...]
    lineage: E.Lineage
    readings: Mapping[str, Mapping[int, Json]]


def _decide(g: E.Graph, resolutions: Resolutions) -> _Decided:
    """The decode (§7, the decode order), each fork routed to its projection's chooser,
    then the end-time fork of every commit no record dates."""
    readings: dict[str, dict[int, dict[str, Json]]] = {
        p.name: {} for p in (END, SPAN, ORIGIN, END_TIME)
    }

    def route(q: E.Question) -> E.Answer:
        p = _BY_KEY[q.key.projection]
        a = p.ask(resolutions, q)
        readings[p.name].setdefault(_site(g, q.key), {})[str(q.key.subject)] = (
            _jsonable(a.value)
        )
        return a

    decided = E.decode(g, route, cap=resolutions.assumptions.limits.cap)
    parts = E.written(decided)

    def when(c: E.CommitId, low: float, highs: tuple[float, ...]) -> float:
        t = END_TIME.ask(resolutions, EndTime(c.claim, c.nth, low, highs))
        readings[END_TIME.name].setdefault(c.claim, {})[str(c.nth)] = t
        return t

    lineage = E.lineage(g.ctx, [p for part in parts for p in part], when=when)
    return _Decided(
        g.ctx,
        parts,
        lineage,
        {p: {s: dict(v) for s, v in by.items()} for p, by in readings.items()},
    )


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


class _Ref(NamedTuple):
    """A seq-valued field before renumbering: an input record by its 0.3.0 seq, or a
    node by its name."""

    seq: int | None = None
    node: E.Node | None = None


@dataclass(frozen=True)
class _Out:
    """One output record before renumbering: its topic, envelope and body, whose
    seq-valued fields are ``_Ref``s; the input record it stands for, if any; the node
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
        after[at].append(((c.node.claim, c.node.nth, 1), _commit(c, None)))
    for p in _pieces(parts, E.PRewind):
        at, t = low(p.claim, p.gaps)
        rw = _Out(
            _Written.REWIND,
            None,
            None,
            {
                "claim_seq": _Ref(seq=p.claim),
                "resume_from": _Ref(node=p.target),
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
                            "resume_from": _Ref(node=starts[s.seq]),
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
                        _commit(commits[node], _Ref(seq=b.consumed_seq)),
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
                            "claim_seq": _Ref(seq=beat.writer),
                            "consumed_seq": _Ref(seq=b.consumed_seq),
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


def _commit(c: E.Commit, consumed: _Ref | None) -> _Out:
    """A commit: dated, by its tick (whose watermark it keeps), or not, its ``t`` the
    end-time chooser's and its watermark the newest control record before it
    (``consumed`` None, filled where it is numbered)."""
    return _Out(
        _Written.COMMIT,
        None,
        None,
        {
            "claim_seq": _Ref(seq=c.node.claim),
            "consumed_seq": consumed,
            "parent": _Ref(node=c.parent),
            "values": [_Ref(seq=v) for v in c.values],
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
            "claim_seq": _Ref(seq=p.claim),
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
                        "claim_seq": _Ref(seq=x.node.claim),
                        "honored": list(x.honored),
                        "parent": _Ref(node=x.parent),
                        "values": (
                            None
                            if x.values is None
                            else [_Ref(seq=v) for v in x.values]
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
    """THE RENUMBERING, once (M7): each record its 0.4.0 seq, gapless from 1, and every
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

    def ref(x: _Ref, o: _Out, field: str) -> int | None:
        if x.node is not None:
            return nodes[x.node]
        if x.seq is None:
            return None
        if x.seq == 0 and field == "consumed_seq":
            return 0  # a watermark that names nothing
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
            if isinstance(v, _Ref):
                out[k] = ref(v, o, k)
            elif isinstance(v, list):
                out[k] = [ref(x, o, k) if isinstance(x, _Ref) else x for x in v]
            elif v is None and k == "consumed_seq":
                out[k] = control
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
    values = {v.seq: v for v in log.values}
    for v in log.values:
        if v.step is None:
            continue
        node = held.get(v.seq)
        if node is None:
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
    for part in parts:
        for p in part:
            if not isinstance(p, E.PStop):
                continue
            if p.author is E.Author.FENCED:
                sites[FENCED_EXIT.name].add(p.seq)
            elif p.author is E.Author.THIRD:
                if any(isinstance(q, E.PDied) for q in part):
                    sites[THIRD_PARTY_RELEASE.name].add(p.seq)
                elif any(
                    isinstance(q, E.PTerminated) and q.launch is None for q in part
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
        END_TIME,
    )
    DIVERGENCES: ClassVar[tuple[Divergence, ...]] = DIVERGENCES

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
        """The forks of End, Span and Origin, each with the facts whose statement
        could settle it; and an end-time fork for each claim with a commit that no
        record dates on any candidate."""
        configuration = FACTS.of(assumptions)
        _unread(parse(rows), configuration)
        g = _candidates(rows, configuration, assumptions.limits, history)
        keys = E.forked(g, cap=assumptions.limits.cap)
        forks: dict[str, list[Fork]] = {p.name: [] for p in self.PROJECTIONS}
        for k in sorted(keys, key=lambda k: (k.position, k.subject)):
            forks[_BY_KEY[k.projection].name].append(
                Fork(_site(g, k), E.settled_by(configuration, k))
            )
        dated = {
            (p.claim, p.nth)
            for es in g.live
            for e in es
            for p in e.pieces
            if isinstance(p, E.PDate)
        }
        undated = sorted(
            {
                p.claim
                for es in g.live
                for e in es
                for p in e.pieces
                if isinstance(p, E.PCommit) and (p.claim, p.nth) not in dated
            }
        )
        forks[END_TIME.name] = [Fork(c, ()) for c in undated]
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
        g = _candidates(rows, configuration, a.limits, history)
        d = _decide(g, resolutions)
        out = _numbered(_layout(log, d), log)
        return Migrated(out, d.readings, _diverged(log, d))
