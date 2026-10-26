"""Format 0.2.0 -> 0.3.0: reference by name (docs/specs/reference-by-name.md §6).

Format 0.2.0 related records by log position; 0.3.0 relates them by name
(lifecycle-v0.5, subscription-v0.3). This step writes the names. **Where 0.2.0's
conventions settle a name, it writes what the positional rule said** the record
answered; where they leave it open, it writes the reading its projection's chooser
gives (log-formats.md §6, "Ambiguity"). It keeps every record's seq, because a
``claim_seq`` points at a seq. It deletes nothing, and appends only
``lifecycle.bound`` records, after the last record. It ports the spike's backfill
(``374c1a2:scripts/backfill_reference_by_name.py``), which was measured exact on
2,562 of 2,569 copies of real consumer logs; the other 7 are the stale-beat leak
below. Three rules go further than the spike's, which merged requests the
positional rule kept apart, or let a nak answer a request it could not have
refused: a stop id that other requests also bear, a nak before every subscribe of
its id, and a nak before every stop of its id. None of these occurs in the real
corpus or in the spike's synthetic world. A fourth binds a lease that only the
latest claim follows, which the spike left unbound, so that the next worker served
it again (the final review's Ruling 17); the real corpus has no subscribes.

The rules:

- **Stops.** A nameless stop gets ``stop@<seq>``. A stop keeps its id only when
  no other request bears it: when it is the only stop with that id, and no
  other request a nak could refuse under that id (a subscription, or a verb
  0.2.0 did not know) bears it. Otherwise every stop bearing it gets
  ``<id>#<seq>``.
  Positionally each stop record was its own request, while in 0.3.0 the requests
  of one id are one request, which every nak bearing that id answers. Each
  ``stopped.honored`` lists the stops ``stop-honored`` reads it as answering:
  under ``positional``, every stop since the previous ``stopped``, blind to both
  author and body, as the old rule cleared them.
- **Episodes.** The ``claim_seq`` of a heartbeat or a ``stopped`` is the claim
  ``record-claim`` reads it as speaking for: under ``positional``, the latest
  claim before it. A ``stopped`` before any claim names none (null).
- **Subscriptions.** A reused id is split into segments at each answer, and the
  later segments become ``<id>#<k>``, together with their answers and value
  fires. An answer before every subscribe of its id answered nothing, so it is
  renamed to name nothing: an unsubscribe always, and a nak unless no
  subscription bears its id and a stop bearing it precedes it. A nak cannot
  refuse a stop that comes after it, so one with no stop of its id before it
  answered nothing either. A nak that keeps its id names its stop: the naked
  stop that names fix.
- **Leases.** One ``lifecycle.bound`` for each unanswered episode-local
  subscription that a claim follows, naming the claim ``lease-bound`` reads as
  registering it: under ``first-claim``, the first claim after its latest send.
  Positionally that claim was its last drainer: every later claim, the next
  worker's included, found it between the lease and itself.

Every minted name is fresh: ``_fresh`` checks it against every request id in the
run and every name minted before it.

**Refused:** a run with a heartbeat before any claim. ``Heartbeat.claim_seq`` is
a required integer, so 0.3.0 cannot hold such a beat, and leaving it unnamed
would put a 0.2.0 record in a 0.3.0 log (log-formats.md §2, rule 5). It is never
dropped.

**The projections** (reference-by-name.md §6, "Ambiguities"): ``record-claim``,
``stop-honored`` and ``lease-bound``, asked in that order, each chooser receiving
the readings given before it. The step states no facts. Each site test reads only
the 0.2.0 log.

**The divergences**, each a read of the migrated log that answers differently
from the same read of the input log, found by computing both: 0.2.0's positional
folds on the input (as runstate ``72d9c3f`` read them) and 0.3.0's by-name folds
on the output, both copied here. Three are positional defects that the names fix,
two of them the spike's (T4): the **stale-beat leak**, where ``progress`` read an
earlier episode's heartbeat because the current claim had none; a **naked stop**,
which ``undischarged_stops`` kept listing until the next ``stopped`` and which
its nak now answers; and an **ended lease**: when the first claim after a lease
is the latest claim and a terminal names it, ``live_demand`` listed the lease,
though no 0.2.0 worker would serve it (a ghost launch); bound to that claim, it
is void for every reader. Two follow from a reading other than the positional
one: an **unhonored stop**, which a ``stopped`` cleared positionally and which a
reading leaves pending; and an **unbound lease**, which a later claim voided
positionally and which a reading leaves unbound.

**Known limits:**

- Under ``record-claim``'s ``positional``, a historical misattribution is copied
  faithfully, not corrected (episode-aim's objection). A displaced worker's late
  heartbeat or ``stopped`` is named for its successor's claim, because the
  positional rule attributed it there, and the log does not record which worker
  wrote it.
- Under ``lease-bound``'s ``first-claim``, the ``bound`` records are inferred
  from the positional rule. The 0.2.0 log never recorded which episode
  registered a lease.
- A nak bearing a uniquely named stop's id, after that stop, is presumed to be
  that stop's refusal. The reference worker naks only the request it is
  handling, so only a nak it did not write (a third party's, or another
  worker's) could make that wrong.
- The real corpus has no subscribes, naks or unsubscribes, so those rules are
  tested only on synthetic logs.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, ClassVar, NamedTuple, TypeGuard, TypeVar

from ..vocabulary.handle import resolve
from . import MigrationError, Row
from .ambiguity import (
    NO_FACTS,
    Assumptions,
    Builtin,
    Detected,
    Divergence,
    Facts,
    Fork,
    History,
    Json,
    Migrated,
    NoFacts,
    Projection,
    Refusal,
    Resolutions,
)

# ----- the two formats' record semantics, frozen here --------------------------
#
# The step is retained forever, so it must read 0.2.0 and write 0.3.0 as they
# were defined when it was written, whatever later code does (log-formats.md
# §6: a step carries what it needs of its old format). It imports no record
# semantics from the package: not Envelope, not Topic, not the schedule
# predicates. From outside it takes only Row and MigrationError, resolve(), an
# OS probe of a handle rather than a format rule, and the shared mechanism of
# ambiguity.py, which holds no format's semantics.

_Body = dict[str, Any]


@dataclass(frozen=True)
class _Record:
    """One record with its body parsed: a 0.2.0 envelope (envelope-v0.2)."""

    seq: int
    topic: str
    name: str | None
    request_id: str | None
    body: _Body


class _Topic(StrEnum):
    """The topics the step reads, as 0.2.0 spelled them (lifecycle-v0.4,
    subscription-v0.2, value-v0.2, launcher-v0.4), and BOUND, which 0.3.0 adds
    (lifecycle-v0.5)."""

    STARTED = "lifecycle.started"
    HEARTBEAT = "lifecycle.heartbeat"
    STOPPED = "lifecycle.stopped"
    NAK = "lifecycle.nak"
    BOUND = "lifecycle.bound"
    STOP = "control.stop"
    SUBSCRIBE = "control.subscribe"
    UNSUBSCRIBE = "control.unsubscribe"
    VALUE = "value"
    LAUNCHED = "launcher.launched"
    TERMINATED = "launcher.terminated"


_ANSWERS = (_Topic.UNSUBSCRIBE, _Topic.NAK)  # 0.2.0's answers to a subscribe

# 0.2.0's leases: a schedule naming either atom anywhere in from/every/until,
# through any/all (references_episode_local at 72d9c3f).
_LEASE_ATOMS = frozenset({"time_seconds", "count"})


def _is_lease(schedule: _Body) -> bool:
    def hit(cond: object) -> bool:
        if not isinstance(cond, dict):
            return False
        if "any" in cond and isinstance(cond["any"], list):
            return any(hit(c) for c in cond["any"])
        if "all" in cond and isinstance(cond["all"], list):
            return any(hit(c) for c in cond["all"])
        return bool(_LEASE_ATOMS & cond.keys())

    return any(
        hit(schedule.get(k))
        for k in ("from", "every", "until")
        if schedule.get(k) is not None
    )


def _is_step(v: object) -> TypeGuard[int]:
    """A step measurement, in both formats: an int, never a bool (is_step at
    72d9c3f)."""
    return isinstance(v, int) and not isinstance(v, bool)


def _dumps(body: _Body) -> str:
    return json.dumps(body, separators=(",", ":"))  # as the channels write a body


def _parse(rows: Sequence[Row]) -> list[_Record]:
    return [
        _Record(x.seq, x.topic, x.name, x.request_id, json.loads(x.body)) for x in rows
    ]


# ----- the projections and their built-in choosers -----------------------------


class ClaimQuestion(NamedTuple):
    """``record-claim``'s question: the run's 0.2.0 rows, and its forks, each a
    heartbeat or a ``stopped`` the log leaves to more than one claim. The answer maps
    each fork's seq to the seq of the ``started`` of the claim it speaks for."""

    rows: tuple[Row, ...]
    sites: tuple[int, ...]


class HonoredQuestion(NamedTuple):
    """``stop-honored``'s question: the rows, its forks (each a ``stopped`` with a
    stop since the previous ``stopped``), and the claim every heartbeat and
    ``stopped`` speaks for, ``record-claim``'s readings included. The answer maps
    each fork's seq to the seqs of the stops it honored."""

    rows: tuple[Row, ...]
    sites: tuple[int, ...]
    claims: Mapping[int, int | None]


class BoundQuestion(NamedTuple):
    """``lease-bound``'s question: the rows, its forks (each the latest send of an
    unanswered lease a claim follows), and the readings before it: every heartbeat's
    and ``stopped``'s claim, and the stops every ``stopped`` honored. The answer maps
    each fork's seq to the claim that registered the lease, or None for none."""

    rows: tuple[Row, ...]
    sites: tuple[int, ...]
    claims: Mapping[int, int | None]
    honored: Mapping[int, frozenset[int]]


ClaimAnswer = Mapping[int, int]
HonoredAnswer = Mapping[int, frozenset[int]]
BoundAnswer = Mapping[int, int | None]


def _starts(rows: Sequence[Row]) -> list[int]:
    return [x.seq for x in rows if x.topic == _Topic.STARTED]


def _since(rows: Sequence[Row], seq: int) -> frozenset[int]:
    """The stops since the ``stopped`` before ``seq``, up to ``seq``."""
    out: set[int] = set()
    for x in rows:
        if x.seq >= seq:
            break
        if x.topic == _Topic.STOPPED:
            out = set()
        elif x.topic == _Topic.STOP:
            out.add(x.seq)
    return frozenset(out)


class PositionalClaim:
    """``positional``: the latest claim before the record."""

    def choose(self, question: ClaimQuestion) -> ClaimAnswer:
        starts = _starts(question.rows)
        return {s: max(c for c in starts if c < s) for s in question.sites}


class PositionalHonored:
    """``positional``: every stop since the previous ``stopped``."""

    def choose(self, question: HonoredQuestion) -> HonoredAnswer:
        return {s: _since(question.rows, s) for s in question.sites}


class DrainedHonored:
    """``drained``: those that the newest heartbeat of its claim before it had
    consumed, its ``consumed_seq`` at or above the stop's seq."""

    def choose(self, question: HonoredQuestion) -> HonoredAnswer:
        beats = [x for x in question.rows if x.topic == _Topic.HEARTBEAT]
        out: dict[int, frozenset[int]] = {}
        for s in question.sites:
            claim = question.claims[s]
            mine = [b for b in beats if b.seq < s and question.claims[b.seq] == claim]
            consumed = json.loads(mine[-1].body).get("consumed_seq") if mine else None
            out[s] = frozenset(
                x
                for x in _since(question.rows, s)
                if _is_step(consumed) and consumed >= x
            )
        return out


class NoneHonored:
    """``none``: no stop, so each stays pending."""

    def choose(self, question: HonoredQuestion) -> HonoredAnswer:
        return {s: frozenset() for s in question.sites}


class FirstClaim:
    """``first-claim``: the first claim after its latest send."""

    def choose(self, question: BoundQuestion) -> BoundAnswer:
        starts = _starts(question.rows)
        return {s: min(c for c in starts if c > s) for s in question.sites}


class Unbound:
    """``unbound``: no ``bound`` record, so the lease is void for no episode."""

    def choose(self, question: BoundQuestion) -> BoundAnswer:
        return {s: None for s in question.sites}


RECORD_CLAIM: Projection[ClaimQuestion, ClaimAnswer] = Projection(
    "record-claim",
    "which claim a heartbeat or a lifecycle.stopped speaks for, where an earlier "
    "claim's worker may have outlived a release of its claim.",
    "the seq of the claim's lifecycle.started",
    (
        Builtin(
            "positional",
            PositionalClaim(),
            "the latest claim before it: no worker outlived a release of its claim",
        ),
    ),
)
STOP_HONORED: Projection[HonoredQuestion, HonoredAnswer] = Projection(
    "stop-honored",
    "which stops a lifecycle.stopped answered, of those since the previous one.",
    "the seqs of the stops it honored",
    (
        Builtin(
            "positional", PositionalHonored(), "every stop since the previous stopped"
        ),
        Builtin(
            "drained",
            DrainedHonored(),
            "the stops its claim's newest heartbeat before it had consumed",
        ),
        Builtin("none", NoneHonored(), "no stop: each stays pending"),
    ),
)
LEASE_BOUND: Projection[BoundQuestion, BoundAnswer] = Projection(
    "lease-bound",
    "which claim registered an episode-local subscription, where the first claim "
    "after it shows no heartbeat that drained it.",
    "the seq of the claim's lifecycle.started, or none",
    (
        Builtin(
            "first-claim",
            FirstClaim(),
            "the first claim after its latest send, as 0.2.0's positional void read",
        ),
        Builtin("unbound", Unbound(), "no claim: the next claim registers it"),
    ),
)

# ----- the divergences ---------------------------------------------------------

STALE_BEAT = Divergence(
    "stale-beat",
    "progress reads only the records that name the latest claim, where 0.2.0's read "
    "the newest heartbeat of any claim.",
)
NAKED_STOP = Divergence(
    "naked-stop",
    "undischarged_stops no longer lists a stop its own nak answered, which 0.2.0's "
    "listed until the next stopped.",
)
ENDED_LEASE = Divergence(
    "ended-lease",
    "live_demand no longer lists a lease bound to the latest claim once a terminal "
    "names that claim, which 0.2.0's listed though no worker would serve it.",
)
UNHONORED_STOP = Divergence(
    "unhonored-stop",
    "undischarged_stops lists a stop that a later stopped cleared positionally and "
    "that its stop-honored reading leaves pending.",
)
UNBOUND_LEASE = Divergence(
    "unbound-lease",
    "live_demand lists a lease that a later claim voided positionally and that its "
    "lease-bound reading leaves unbound.",
)

# ----- the rules ---------------------------------------------------------------


def _fresh(base: str, taken: set[str]) -> str:
    """``base``, or ``base~k`` for the least k >= 2 not in ``taken``; then taken."""
    cand, k = base, 1
    while cand in taken:
        k += 1
        cand = f"{base}~{k}"
    taken.add(cand)
    return cand


@dataclass
class Backfill:
    """A log as the rules rewrite it. ``records`` holds the records in their
    input order; ``appended`` the records after the last; ``rebodied`` the seqs
    whose body changed; ``taken`` every request id in use, seeded with the
    run's own."""

    records: list[_Record]
    taken: set[str]
    rebodied: set[int] = field(default_factory=set)
    appended: list[_Record] = field(default_factory=list)

    def mint(self, base: str) -> str:
        return _fresh(base, self.taken)

    def set_rid(self, i: int, rid: str) -> None:
        self.records[i] = replace(self.records[i], request_id=rid)

    def name(self, i: int, **fields: Any) -> None:
        e = self.records[i]
        self.records[i] = replace(e, body={**e.body, **fields})
        self.rebodied.add(e.seq)


def _ids(records: list[_Record], keep: Callable[[str], bool]) -> set[str]:
    """The request ids of the records whose topic ``keep`` selects."""
    return {e.request_id for e in records if keep(e.topic) and e.request_id is not None}


def _name_stops(log: Backfill, records: list[_Record]) -> None:
    """Each stop's id, kept or minted: determined, since each 0.2.0 stop record was
    its own request."""
    # every other request a nak could refuse by id; an unsubscribe is never one
    shared = _ids(
        records,
        lambda t: t.startswith("control.")
        and t not in (_Topic.STOP, _Topic.UNSUBSCRIBE),
    )
    uses = Counter(
        e.request_id
        for e in records
        if e.topic == _Topic.STOP and e.request_id is not None
    )
    for i, e in enumerate(records):
        if e.topic == _Topic.STOP:
            rid = e.request_id
            if rid is None:
                log.set_rid(i, log.mint(f"stop@{e.seq}"))
            elif uses[rid] > 1 or rid in shared:
                log.set_rid(i, log.mint(f"{rid}#{e.seq}"))


@dataclass
class _Segment:
    """The current request of one subscription id: its name, and whether an
    answer has spent it."""

    name: str
    k: int = 0
    live: bool = True


def _segment_subscriptions(log: Backfill, records: list[_Record]) -> None:
    subscribed = _ids(records, lambda t: t == _Topic.SUBSCRIBE)
    stopped_so_far: set[str] = set()  #  the ids stops have borne so far
    segments: dict[str, _Segment] = {}
    for i, e in enumerate(records):
        rid = e.request_id
        if rid is None:
            continue
        seg = segments.get(rid)
        if e.topic == _Topic.STOP:
            stopped_so_far.add(rid)
        elif e.topic == _Topic.SUBSCRIBE:
            if seg is None:
                seg = segments[rid] = _Segment(rid)
            elif not seg.live:
                seg.k += 1
                seg.live = True
                seg.name = log.mint(f"{rid}#{seg.k}")
            if seg.name != rid:
                log.set_rid(i, seg.name)
        elif e.topic == _Topic.VALUE:
            if seg is not None and seg.name != rid:
                log.set_rid(i, seg.name)
        elif e.topic in _ANSWERS:
            if seg is None:
                if (
                    e.topic == _Topic.UNSUBSCRIBE
                    or rid in subscribed
                    or rid not in stopped_so_far
                ):
                    log.set_rid(i, log.mint(f"{rid}!orphan@{e.seq}"))
                continue
            if seg.name != rid:
                log.set_rid(i, seg.name)
            seg.live = False


def _leases(log: Backfill) -> dict[int, int]:
    """Each unanswered episode-local subscription that a claim follows: the seq of
    its latest send, and the first claim after it."""
    claims = [e.seq for e in log.records if e.topic == _Topic.STARTED]
    live: dict[str, _Record] = {}
    for e in log.records:
        if e.request_id is None:
            continue
        if e.topic == _Topic.SUBSCRIBE:
            live[e.request_id] = e
        elif e.topic in _ANSWERS:
            live.pop(e.request_id, None)
    out: dict[int, int] = {}
    for e in live.values():
        after = [c for c in claims if c > e.seq]
        if _is_lease(e.body) and after:
            out[e.seq] = after[0]
    return out


# ----- the site tests: the 0.2.0 log alone ---------------------------------------


def _claim_sites(records: list[_Record]) -> list[int]:
    """``record-claim``'s forks: each heartbeat or ``stopped`` while some earlier
    claim's worker may live on, displaced. A claim is displaced when a ``stopped``
    that is not ``completed: true`` follows it before the next claim begins (a
    malformed one counts), since such a ``stopped`` let the next claim begin and a
    third party may have written it, and until a ``launcher.terminated`` reports its
    launch's process ended, the launch its ``started`` re-emits with an equal handle.
    A claim no ``stopped`` followed was dead when the next one began: the claim guard
    takes a claim only when the latest one is followed by a ``stopped`` or its worker
    resolves dead (design-v0.2.md §4, §8)."""
    handles: dict[str, object] = {}  # launch id -> its process's handle
    ended: set[str] = set()  # launches whose process ended
    current: _Record | None = None
    released = False
    displaced: list[_Record] = []
    out: list[int] = []

    def alive(c: _Record) -> bool:
        launch = c.request_id
        return not (
            launch is not None
            and launch in ended
            and handles.get(launch) == c.body.get("handle")
        )

    for e in records:
        if e.topic == _Topic.LAUNCHED and e.request_id is not None:
            handles[e.request_id] = e.body.get("handle")
        elif e.topic == _Topic.TERMINATED and e.request_id is not None:
            ended.add(e.request_id)
            displaced = [c for c in displaced if alive(c)]
        elif e.topic == _Topic.STARTED:
            if current is not None and released and alive(current):
                displaced.append(current)
            current, released = e, False
        elif e.topic in (_Topic.HEARTBEAT, _Topic.STOPPED):
            if displaced:
                out.append(e.seq)
            if e.topic == _Topic.STOPPED and e.body.get("completed") is not True:
                released = True
    return out


def _honored_sites(records: list[_Record]) -> list[int]:
    """``stop-honored``'s forks: each ``stopped`` with a stop since the previous
    one."""
    out: list[int] = []
    pending = False
    for e in records:
        if e.topic == _Topic.STOP:
            pending = True
        elif e.topic == _Topic.STOPPED:
            if pending:
                out.append(e.seq)
            pending = False
    return out


def _bound_sites(
    log: Backfill, records: list[_Record], claim_sites: list[int]
) -> tuple[list[int], dict[int, int]]:
    """``lease-bound``'s forks, and its proven sites with their proven reading. A
    lease's first claim proves it registered the lease when a heartbeat of that
    claim, before the next claim, has a ``consumed_seq`` reaching the lease's latest
    send: the watermark passes a request only once it is registered or refused
    (design-v0.2.md §6), and an unanswered one was not refused. A heartbeat is that
    claim's only where no ``record-claim`` fork leaves it to another."""
    starts = [e.seq for e in records if e.topic == _Topic.STARTED]
    open_ = set(claim_sites)
    forks: list[int] = []
    proven: dict[int, int] = {}
    for send, first in _leases(log).items():
        nxt = min((c for c in starts if c > first), default=None)
        drained = any(
            e.topic == _Topic.HEARTBEAT
            and e.seq > first
            and (nxt is None or e.seq < nxt)
            and e.seq not in open_
            and _is_step(e.body.get("consumed_seq"))
            and e.body["consumed_seq"] >= send
            for e in records
        )
        if drained:
            proven[send] = first
        else:
            forks.append(send)
    return forks, proven


class _Sites(NamedTuple):
    claim: list[int]
    honored: list[int]
    bound: list[int]
    bound_proven: dict[int, int]
    leases: list[int]  # every lease a claim follows, by latest send, in bound order


def _prepare(records: list[_Record]) -> tuple[Backfill, _Sites]:
    """The names 0.2.0's own rules settle, and every projection's forks."""
    log = Backfill(
        list(records), {e.request_id for e in records if e.request_id is not None}
    )
    _name_stops(log, records)
    _segment_subscriptions(log, records)
    claim = _claim_sites(records)
    bound, proven = _bound_sites(log, records, claim)
    leases = list(_leases(log))
    return log, _Sites(claim, _honored_sites(records), bound, proven, leases)


# ----- the readings ------------------------------------------------------------

_K = TypeVar("_K")
_V = TypeVar("_V")


def _at_sites(
    projection: Projection[Any, Mapping[_K, _V]],
    resolutions: Resolutions,
    answer: Mapping[_K, _V],
    sites: Sequence[_K],
) -> Mapping[_K, _V]:
    """A chooser's answer, refused unless it reads every fork and nothing else."""
    if set(answer) != set(sites):
        raise MigrationError(
            f"--resolve {projection.name}={resolutions.choosers[projection.name].spelling}"
            f" read seqs {sorted(map(str, answer))}, not its forks "
            f"{sorted(map(str, sites))}"
        )
    return answer


def _excluded(
    projection: Projection[Any, Any], resolutions: Resolutions, seq: int, why: str
) -> MigrationError:
    spelling = resolutions.choosers[projection.name].spelling
    return MigrationError(
        f"--resolve {projection.name}={spelling} gave seq {seq} a reading the log "
        f"excludes: {why}"
    )


def _claims(
    rows: tuple[Row, ...],
    records: list[_Record],
    sites: list[int],
    resolutions: Resolutions,
) -> dict[int, int | None]:
    """The claim every heartbeat and ``stopped`` speaks for: ``record-claim``'s
    reading at a fork, else the latest claim before it, which the log proves. A
    ``stopped`` before any claim names none; a heartbeat before any claim refuses
    the run."""
    read: Mapping[int, int] = {}
    if sites:
        answer = RECORD_CLAIM.ask(resolutions, ClaimQuestion(rows, tuple(sites)))
        read = _at_sites(RECORD_CLAIM, resolutions, answer, sites)
    starts = {e.seq for e in records if e.topic == _Topic.STARTED}
    out: dict[int, int | None] = {}
    claim: int | None = None
    for e in records:
        if e.topic == _Topic.STARTED:
            claim = e.seq
        elif e.topic in (_Topic.HEARTBEAT, _Topic.STOPPED):
            if e.topic == _Topic.HEARTBEAT and claim is None:
                raise MigrationError(
                    f"the heartbeat at seq {e.seq} precedes every claim: format 0.3.0 "
                    "requires a heartbeat to name its claim, so this run cannot move "
                    "to it"
                )
            if e.seq in read:
                c = read[e.seq]
                if not (_is_step(c) and c in starts and c < e.seq):
                    raise _excluded(
                        RECORD_CLAIM, resolutions, e.seq, "no claim before it"
                    )
                out[e.seq] = c
            else:
                out[e.seq] = claim
    return out


def _honored(
    rows: tuple[Row, ...],
    records: list[_Record],
    sites: list[int],
    claims: Mapping[int, int | None],
    resolutions: Resolutions,
) -> dict[int, frozenset[int]]:
    """The stops every ``stopped`` honored: ``stop-honored``'s reading at a fork,
    else none, since no stop came since the previous ``stopped``. A reading may name
    only stops since the previous ``stopped``: a 0.2.0 worker's drain skipped every
    stop before it."""
    read: Mapping[int, frozenset[int]] = {}
    if sites:
        answer = STOP_HONORED.ask(
            resolutions, HonoredQuestion(rows, tuple(sites), claims)
        )
        read = _at_sites(STOP_HONORED, resolutions, answer, sites)
    out: dict[int, frozenset[int]] = {}
    for e in records:
        if e.topic == _Topic.STOPPED:
            stops = frozenset(read.get(e.seq, frozenset()))
            if not stops <= _since(rows, e.seq):
                raise _excluded(
                    STOP_HONORED,
                    resolutions,
                    e.seq,
                    "a stop that is not one since the previous stopped",
                )
            out[e.seq] = stops
    return out


def _bindings(
    rows: tuple[Row, ...],
    sites: _Sites,
    claims: Mapping[int, int | None],
    honored: Mapping[int, frozenset[int]],
    resolutions: Resolutions,
) -> dict[int, int | None]:
    """The claim each lease a claim follows is bound to: ``lease-bound``'s reading
    at a fork, else its proven reading. A reading may name only the first claim after
    the lease's latest send, or none: every later claim found that one between."""
    read: Mapping[int, int | None] = {}
    if sites.bound:
        answer = LEASE_BOUND.ask(
            resolutions, BoundQuestion(rows, tuple(sites.bound), claims, honored)
        )
        read = _at_sites(LEASE_BOUND, resolutions, answer, sites.bound)
    starts = _starts(rows)
    for send, claim in read.items():
        first = min(c for c in starts if c > send)
        if claim is not None and claim != first:
            raise _excluded(
                LEASE_BOUND, resolutions, send, "a claim other than the first after it"
            )
    return {**sites.bound_proven, **read}


# ----- the reads a divergence changes ------------------------------------------


class _Reads(NamedTuple):
    """The three reads a step's rules can change: ``progress`` with the record it
    took its value from, ``undischarged_stops`` and ``live_demand`` by seq."""

    progress: tuple[int | None, int | None]
    stops: frozenset[int]
    demand: frozenset[int]


def _reads_0_2_0(records: list[_Record]) -> _Reads:
    """0.2.0's positional folds (runstate 72d9c3f, observables.py)."""
    beat = next((e for e in reversed(records) if e.topic == _Topic.HEARTBEAT), None)
    stop = next((e for e in reversed(records) if e.topic == _Topic.STOPPED), None)
    starts = [e.seq for e in records if e.topic == _Topic.STARTED]
    # undischarged_stops: every stop after the latest stopped
    last_stopped = stop.seq if stop is not None else 0
    if stop is not None and starts and starts[-1] > stop.seq:
        stop = None  # progress: a past episode's dying breath
    pending: dict[str, _Record] = {}
    for e in records:
        if e.request_id is None:
            continue
        if e.topic == _Topic.SUBSCRIBE:
            pending[e.request_id] = e
        elif e.topic in _ANSWERS:
            pending.pop(e.request_id, None)
    latest = starts[-1] if starts else 0
    return _Reads(
        _frontier(beat, stop),
        frozenset(
            e.seq for e in records if e.topic == _Topic.STOP and e.seq > last_stopped
        ),
        frozenset(
            e.seq
            for e in pending.values()
            if not (_is_lease(e.body) and any(e.seq < b < latest for b in starts))
        ),
    )


def _reads_0_3_0(records: list[_Record]) -> _Reads:
    """0.3.0's by-name folds (observables.py at this step's writing): ``progress``
    from the heartbeat and the ``stopped`` naming the latest claim;
    ``undischarged_stops``, the stops no ``honored`` or nak names;
    ``live_demand``, the subscriptions no answer names, a lease bound to a claim
    other than the latest, or to the latest once a terminal names it, void."""
    starts = [e for e in records if e.topic == _Topic.STARTED]
    claim = starts[-1] if starts else None
    want = claim.seq if claim is not None else None
    floor = want if want is not None else 0

    def names(e: _Record) -> bool:
        c = e.body.get("claim_seq", "absent")
        return (c is None or _is_step(c)) and c == want

    beat = None
    if claim is not None:
        beat = next(
            (
                e
                for e in reversed(records)
                if e.topic == _Topic.HEARTBEAT and e.seq > floor and names(e)
            ),
            None,
        )
    stop = next(
        (
            e
            for e in reversed(records)
            if e.topic == _Topic.STOPPED and e.seq > floor and names(e)
        ),
        None,
    )
    stops: dict[str, _Record] = {}
    answered: set[str] = set()
    subs: dict[str, _Record] = {}
    spent: set[str] = set()
    bound: dict[str, set[int]] = {}
    for e in records:
        if e.topic == _Topic.STOP and e.request_id is not None:
            stops[e.request_id] = e
        elif e.topic == _Topic.STOPPED:
            h = e.body.get("honored")
            if isinstance(h, list):
                answered.update(r for r in h if isinstance(r, str))
        elif e.topic == _Topic.SUBSCRIBE and e.request_id is not None:
            subs[e.request_id] = e
        elif e.topic in _ANSWERS and e.request_id is not None:
            spent.add(e.request_id)
            if e.topic == _Topic.NAK:
                answered.add(e.request_id)
        elif e.topic == _Topic.BOUND and e.request_id is not None:
            k = e.body.get("claim_seq")
            if _is_step(k):
                bound.setdefault(e.request_id, set()).add(k)
    ended = claim is not None and (
        stop is not None
        or (
            claim.request_id is not None
            and any(
                e.topic == _Topic.TERMINATED
                and e.seq > claim.seq
                and e.request_id == claim.request_id
                for e in records
            )
        )
    )

    def void(rid: str) -> bool:
        ks = bound.get(rid, set())
        return any(k != want for k in ks) or (want in ks and ended)

    return _Reads(
        _frontier(beat, stop),
        frozenset(e.seq for r, e in stops.items() if r not in answered),
        frozenset(e.seq for r, e in subs.items() if r not in spent and not void(r)),
    )


def _frontier(
    beat: _Record | None, stop: _Record | None
) -> tuple[int | None, int | None]:
    """``progress``: the greater integer of the heartbeat's ``step`` and the
    ``stopped``'s ``final_step``, with the seq of the record it came from, which
    names a divergence's site and is no part of the read."""
    found = [
        (e.body[k], e.seq)
        for e, k in ((beat, "step"), (stop, "final_step"))
        if e is not None and _is_step(e.body.get(k))
    ]
    if not found:
        return None, None
    return max(found)


def _seqs(seqs: frozenset[int]) -> list[Json]:
    return [s for s in sorted(seqs)]


def _divergences(before: list[_Record], after: list[_Record]) -> dict[str, list[int]]:
    """Where each divergence fires: each read that answers differently on the output
    than on the input, at the records that make it differ."""
    old, new = _reads_0_2_0(before), _reads_0_3_0(after)
    (was, by), (now, at) = old.progress, new.progress
    stale = [] if was == now else [by if by is not None else at]
    return {
        STALE_BEAT.name: [s for s in stale if s is not None],
        NAKED_STOP.name: sorted(old.stops - new.stops),
        ENDED_LEASE.name: sorted(old.demand - new.demand),
        UNHONORED_STOP.name: sorted(new.stops - old.stops),
        UNBOUND_LEASE.name: sorted(new.demand - old.demand),
    }


# ----- the step ----------------------------------------------------------------


class V0_2_0_to_V0_3_0:
    FROM = "0.2.0"
    TO = "0.3.0"
    RENUMBERS: ClassVar[bool] = False
    FACTS: ClassVar[Facts[NoFacts]] = NO_FACTS
    PROJECTIONS: ClassVar[tuple[Projection[Any, Any], ...]] = (
        RECORD_CLAIM,
        STOP_HONORED,
        LEASE_BOUND,
    )
    DIVERGENCES: ClassVar[tuple[Divergence, ...]] = (
        STALE_BEAT,
        NAKED_STOP,
        ENDED_LEASE,
        UNHONORED_STOP,
        UNBOUND_LEASE,
    )

    def is_live(self, rows: list[Row]) -> bool:
        """0.2.0's liveness, positional: the latest claim is live unless a
        ``stopped`` follows it, whatever that record's body, or its handle
        resolves dead. An unresolvable foreign handle reads as live. The launcher
        tier does not participate."""
        claim = next((x for x in reversed(rows) if x.topic == _Topic.STARTED), None)
        if claim is None:
            return False
        if any(x.topic == _Topic.STOPPED and x.seq > claim.seq for x in rows):
            return False
        handle = json.loads(claim.body).get("handle")
        if not isinstance(handle, str):
            raise MigrationError(
                f"the claim at seq {claim.seq} carries no handle, so whether it "
                "is live cannot be told"
            )
        return resolve(handle) is not False

    def detect(
        self, rows: list[Row], *, assumptions: Assumptions, history: History
    ) -> Detected:
        """Each projection's forks, from the 0.2.0 log alone. No fact settles one."""
        _, sites = _prepare(_parse(rows))
        return Detected(
            {
                RECORD_CLAIM.name: tuple(Fork(s, ()) for s in sites.claim),
                STOP_HONORED.name: tuple(Fork(s, ()) for s in sites.honored),
                LEASE_BOUND.name: tuple(Fork(s, ()) for s in sites.bound),
            }
        )

    def transform(
        self, rows: list[Row], *, resolutions: Resolutions, history: History
    ) -> Migrated:
        """Each record keeps its seq, topic, name and ``created_at``, and its body
        text byte for byte unless the body changed. An appended ``bound`` takes
        the latest ``created_at`` in the run."""
        records = _parse(rows)
        log, sites = _prepare(records)
        given = tuple(rows)
        claims = _claims(given, records, sites.claim, resolutions)
        honored = _honored(given, records, sites.honored, claims, resolutions)
        bindings = _bindings(given, sites, claims, honored, resolutions)
        ids = {
            e.seq: e.request_id
            for e in log.records
            if e.topic == _Topic.STOP and e.request_id is not None
        }
        for i, e in enumerate(records):
            if e.topic == _Topic.STOPPED:
                log.name(i, honored=sorted(ids[s] for s in honored[e.seq]))
        for i, e in enumerate(records):
            if e.topic in (_Topic.HEARTBEAT, _Topic.STOPPED):
                log.name(i, claim_seq=claims[e.seq])
        last = records[-1].seq if records else 0
        rid = {e.seq: e.request_id for e in log.records}
        for send in sites.leases:
            claim = bindings[send]
            if claim is not None:
                seq = last + len(log.appended) + 1
                log.appended.append(
                    _Record(
                        seq, _Topic.BOUND.value, None, rid[send], {"claim_seq": claim}
                    )
                )
        out = [
            x._replace(
                request_id=e.request_id,
                body=_dumps(e.body) if x.seq in log.rebodied else x.body,
            )
            for x, e in zip(rows, log.records, strict=True)
        ]
        created = max((x.created_at for x in rows), default=0.0)
        out += [
            Row(e.seq, e.topic, e.name, e.request_id, _dumps(e.body), created)
            for e in log.appended
        ]
        readings: dict[str, dict[int, Json]] = {
            RECORD_CLAIM.name: {s: claims[s] for s in sites.claim},
            STOP_HONORED.name: {s: _seqs(honored[s]) for s in sites.honored},
            LEASE_BOUND.name: {s: bindings[s] for s in sites.bound},
        }
        divergences = _divergences(records, log.records + log.appended)
        return Migrated(
            out,
            readings,
            {name: tuple(seqs) for name, seqs in divergences.items()},
        )
