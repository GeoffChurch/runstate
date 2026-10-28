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
on the output, both copied here: ``progress``, the verdict a ``stopped`` gives,
``undischarged_stops`` and ``live_demand``. Three are positional defects that
the names fix, two of them the spike's (T4): the **stale-beat leak**, where
``progress`` read an earlier episode's heartbeat because the current claim had
none; a **naked stop**, which ``undischarged_stops`` kept listing until the next
``stopped`` and which its nak now answers; and an **ended lease**: when the
first claim after a lease is the latest claim and a terminal names it,
``live_demand`` listed the lease, though no 0.2.0 worker would serve it (a ghost
launch); bound to that claim, it is void for every reader. Three follow from a
reading other than the positional one: an **unhonored stop**, which a
``stopped`` cleared positionally and which a reading leaves pending; an
**unbound lease**, which a later claim voided positionally and which a reading
leaves unbound; and a **reattributed record**, a heartbeat or ``stopped`` a
``record-claim`` reading names for another claim, which can change any of the
reads, the verdict included. Each is named by the link that changed the read:
the output is computed under the positional readings, under ``record-claim``'s
alone, and under all of them (``_divergences``).

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
from bisect import bisect_left, bisect_right
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
    """``record-claim``'s question: the run's 0.2.0 rows, its forks, each a heartbeat
    or a ``stopped`` the log leaves to more than one claim, and each fork's
    candidates: the latest claim before it and the displaced claims still alive. The
    answer maps each fork's seq to the seq of the ``started`` of the claim it speaks
    for, one of its candidates."""

    rows: tuple[Row, ...]
    sites: tuple[int, ...]
    candidates: Mapping[int, frozenset[int]]


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


def _per_site(sites: Sequence[int], answer: object) -> Mapping[Any, object]:
    """A chooser's answer as a mapping that reads every fork and nothing else."""
    if not isinstance(answer, Mapping):
        raise ValueError(f"a {type(answer).__name__}, not a mapping of fork seqs")
    if set(answer) != set(sites):
        raise ValueError(
            f"it reads {sorted(map(repr, answer))}, not its forks {sorted(sites)}"
        )
    return answer


def _claim_answer(question: ClaimQuestion, answer: object) -> ClaimAnswer:
    read = _per_site(question.sites, answer)
    out: dict[int, int] = {}
    for s in question.sites:
        c = read[s]
        if not _is_step(c):
            raise ValueError(f"at seq {s}, {c!r} is no claim's seq")
        out[s] = c
    return out


def _honored_answer(question: HonoredQuestion, answer: object) -> HonoredAnswer:
    read = _per_site(question.sites, answer)
    out: dict[int, frozenset[int]] = {}
    for s in question.sites:
        stops = read[s]
        if not isinstance(stops, (set, frozenset, list, tuple)) or not all(
            _is_step(x) for x in stops
        ):
            raise ValueError(f"at seq {s}, {stops!r} is no collection of stop seqs")
        out[s] = frozenset(stops)
    return out


def _bound_answer(question: BoundQuestion, answer: object) -> BoundAnswer:
    read = _per_site(question.sites, answer)
    out: dict[int, int | None] = {}
    for s in question.sites:
        c = read[s]
        if c is not None and not _is_step(c):
            raise ValueError(f"at seq {s}, {c!r} is neither a claim's seq nor None")
        out[s] = c
    return out


def _starts(rows: Sequence[Row]) -> list[int]:
    return [x.seq for x in rows if x.topic == _Topic.STARTED]


def _since(rows: Sequence[Row]) -> dict[int, frozenset[int]]:
    """Each ``stopped``'s stops since the ``stopped`` before it, by its seq."""
    out: dict[int, frozenset[int]] = {}
    stops: set[int] = set()
    for x in rows:
        if x.topic == _Topic.STOPPED:
            out[x.seq], stops = frozenset(stops), set()
        elif x.topic == _Topic.STOP:
            stops.add(x.seq)
    return out


def _latest_before(seqs: Sequence[int], seq: int) -> int:
    """The greatest of the ascending ``seqs`` below ``seq``."""
    return seqs[bisect_left(seqs, seq) - 1]


class PositionalClaim:
    """``positional``: the latest claim before the record."""

    def choose(self, question: ClaimQuestion) -> ClaimAnswer:
        starts = _starts(question.rows)
        return {s: _latest_before(starts, s) for s in question.sites}


class PositionalHonored:
    """``positional``: every stop since the previous ``stopped``."""

    def choose(self, question: HonoredQuestion) -> HonoredAnswer:
        since = _since(question.rows)
        return {s: since[s] for s in question.sites}


class DrainedHonored:
    """``drained``: those that the newest heartbeat of its claim before it had
    consumed, its ``consumed_seq`` at or above the stop's seq."""

    def choose(self, question: HonoredQuestion) -> HonoredAnswer:
        since = _since(question.rows)
        sites = set(question.sites)
        consumed: dict[int | None, object] = {}  # claim -> its newest beat's watermark
        out: dict[int, frozenset[int]] = {}
        for x in question.rows:
            if x.topic == _Topic.HEARTBEAT:
                body = json.loads(x.body)
                consumed[question.claims[x.seq]] = body.get("consumed_seq")
            elif x.seq in sites:
                mark = consumed.get(question.claims[x.seq])
                out[x.seq] = frozenset(
                    s for s in since[x.seq] if _is_step(mark) and mark >= s
                )
        return out


class NoneHonored:
    """``none``: no stop, so each stays pending."""

    def choose(self, question: HonoredQuestion) -> HonoredAnswer:
        return {s: frozenset() for s in question.sites}


class FirstClaim:
    """``first-claim``: the first claim after its latest send."""

    def choose(self, question: BoundQuestion) -> BoundAnswer:
        return _first_claims(question.rows, question.sites)


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
    _claim_answer,
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
    _honored_answer,
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
    _bound_answer,
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
REATTRIBUTED = Divergence(
    "reattributed-record",
    "progress, peek_terminal, live_episode, undischarged_stops or live_demand read "
    "the records that name the latest claim, and a record-claim reading names "
    "another claim than the positional rule for a heartbeat or a stopped.",
)
_DIVERGENCES = (
    STALE_BEAT,
    NAKED_STOP,
    ENDED_LEASE,
    UNHONORED_STOP,
    UNBOUND_LEASE,
    REATTRIBUTED,
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

    @property
    def written(self) -> list[_Record]:
        return self.records + self.appended

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


def _claim_sites(records: list[_Record]) -> dict[int, frozenset[int]]:
    """``record-claim``'s forks, each with its candidates: each heartbeat or
    ``stopped`` while some earlier claim's worker may live on, displaced, and the
    claims that may have written it, the latest and the displaced ones still alive. A claim is displaced when a ``stopped``
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
    out: dict[int, frozenset[int]] = {}

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
            if displaced and current is not None:
                out[e.seq] = frozenset({current.seq, *(c.seq for c in displaced)})
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
    log: Backfill, records: list[_Record], claim_sites: Mapping[int, frozenset[int]]
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
    claim: dict[int, frozenset[int]]  # each fork's candidates
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


def _excluded(
    projection: Projection[Any, Any], resolutions: Resolutions, seq: int, why: str
) -> MigrationError:
    flag = resolutions.choosers[projection.name].flag(projection.name)
    return MigrationError(f"{flag} gave seq {seq} a reading the log excludes: {why}")


def _positional_claims(records: list[_Record]) -> dict[int, int | None]:
    """The claim every heartbeat and ``stopped`` speaks for by position: the latest
    claim before it. A ``stopped`` before any claim names none; a heartbeat before
    any claim refuses the run."""
    out: dict[int, int | None] = {}
    claim: int | None = None
    for e in records:
        if e.topic == _Topic.STARTED:
            claim = e.seq
        elif e.topic == _Topic.HEARTBEAT and claim is None:
            raise MigrationError(
                f"the heartbeat at seq {e.seq} precedes every claim: format 0.3.0 "
                "requires a heartbeat to name its claim, so this run cannot move "
                "to it"
            )
        elif e.topic in (_Topic.HEARTBEAT, _Topic.STOPPED):
            out[e.seq] = claim
    return out


def _claims(
    rows: tuple[Row, ...],
    positional: Mapping[int, int | None],
    sites: Mapping[int, frozenset[int]],
    resolutions: Resolutions,
) -> dict[int, int | None]:
    """The claim every heartbeat and ``stopped`` speaks for: ``record-claim``'s
    reading at a fork, one of its candidates, else the latest claim before it, which
    the log proves."""
    if not sites:
        return dict(positional)
    read = RECORD_CLAIM.ask(resolutions, ClaimQuestion(rows, tuple(sites), dict(sites)))
    for seq, claim in read.items():
        if claim not in sites[seq]:
            raise _excluded(
                RECORD_CLAIM,
                resolutions,
                seq,
                f"claim {claim} is neither the latest before it nor a displaced one "
                f"still alive ({sorted(sites[seq])})",
            )
    return {**positional, **read}


def _honored(
    rows: tuple[Row, ...],
    since: Mapping[int, frozenset[int]],
    sites: list[int],
    claims: Mapping[int, int | None],
    resolutions: Resolutions,
) -> dict[int, frozenset[int]]:
    """The stops every ``stopped`` honored: ``stop-honored``'s reading at a fork,
    else none, since no stop came since the previous ``stopped``. A reading may name
    only stops since the previous ``stopped``: a 0.2.0 worker's drain skipped every
    stop before it."""
    out = {seq: frozenset[int]() for seq in since}
    if not sites:
        return out
    read = STOP_HONORED.ask(resolutions, HonoredQuestion(rows, tuple(sites), claims))
    for seq, stops in read.items():
        if not stops <= since[seq]:
            raise _excluded(
                STOP_HONORED,
                resolutions,
                seq,
                "a stop that is not one since the previous stopped",
            )
    return {**out, **read}


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
    if not sites.bound:
        return dict(sites.bound_proven)
    read = LEASE_BOUND.ask(
        resolutions, BoundQuestion(rows, tuple(sites.bound), claims, honored)
    )
    starts = _starts(rows)
    for send, claim in read.items():
        first = starts[bisect_right(starts, send)]
        if claim is not None and claim != first:
            raise _excluded(
                LEASE_BOUND, resolutions, send, "a claim other than the first after it"
            )
    return {**sites.bound_proven, **read}


def _first_claims(rows: Sequence[Row], sends: Sequence[int]) -> dict[int, int]:
    """``first-claim``'s binding of each lease fork: the positional one."""
    starts = _starts(rows)
    return {s: starts[bisect_right(starts, s)] for s in sends}


def _named(
    log: Backfill,
    records: list[_Record],
    sites: _Sites,
    claims: Mapping[int, int | None],
    honored: Mapping[int, frozenset[int]],
    bindings: Mapping[int, int | None],
) -> Backfill:
    """A copy of ``log`` with the names the readings write: each ``stopped``'s
    ``honored``, each heartbeat's and ``stopped``'s ``claim_seq``, and a ``bound``
    after the last record for each lease bound to a claim."""
    out = Backfill(list(log.records), set(log.taken), set(log.rebodied), [])
    ids = {
        e.seq: e.request_id
        for e in log.records
        if e.topic == _Topic.STOP and e.request_id is not None
    }
    for i, e in enumerate(records):
        if e.topic == _Topic.STOPPED:
            out.name(i, honored=sorted(ids[s] for s in honored[e.seq]))
    for i, e in enumerate(records):
        if e.topic in (_Topic.HEARTBEAT, _Topic.STOPPED):
            out.name(i, claim_seq=claims[e.seq])
    last = records[-1].seq if records else 0
    rid = {e.seq: e.request_id for e in log.records}
    for send in sites.leases:
        claim = bindings[send]
        if claim is not None:
            seq = last + len(out.appended) + 1
            out.appended.append(
                _Record(seq, _Topic.BOUND.value, None, rid[send], {"claim_seq": claim})
            )
    return out


# ----- the reads a divergence changes ------------------------------------------


class _Reads(NamedTuple):
    """The reads a step's rules or readings can change: ``progress`` with the record
    it took its value from; the verdict ``peek_terminal`` reads off a ``stopped``,
    and with it whether ``live_episode`` finds the latest claim ended, as that
    ``stopped``'s body without the names the step writes (None for none);
    ``undischarged_stops`` and ``live_demand`` by seq."""

    progress: tuple[int | None, int | None]
    verdict: str | None
    stops: frozenset[int]
    demand: frozenset[int]


def _verdict(stop: _Record | None) -> str | None:
    if stop is None:
        return None
    return json.dumps(
        {k: v for k, v in stop.body.items() if k not in ("claim_seq", "honored")},
        sort_keys=True,
    )


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
        _verdict(stop),  # the latest stopped, unless a claim follows it
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
        _verdict(stop),
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


def _items(reads: _Reads) -> dict[tuple[str, int], object]:
    """A log's reads as items: a value per scalar read, membership per stop and
    lease."""
    out: dict[tuple[str, int], object] = {
        ("progress", 0): reads.progress[0],
        ("verdict", 0): reads.verdict,
    }
    out.update({("stop", seq): True for seq in reads.stops})
    out.update({("lease", seq): True for seq in reads.demand})
    return out


# Which divergence names a read that changed, by the link that changed it: the
# step's rules (input -> the output under positional readings), record-claim's
# reading (-> the output under it alone), and the other readings (-> the output).
_BY_RULE = {"progress": STALE_BEAT, "stop": NAKED_STOP, "lease": ENDED_LEASE}
_BY_READING = {"stop": UNHONORED_STOP, "lease": UNBOUND_LEASE}


def _divergences(
    before: list[_Record],
    positional: list[_Record],
    reattributed: list[_Record],
    after: list[_Record],
    forks: list[int],
) -> dict[str, list[int]]:
    """Where each divergence fires: each read that answers differently on the output
    than on the input, named by every link that changed it. ``positional`` is the
    output under the positional readings, ``reattributed`` under record-claim's
    reading and the others positional, and ``forks`` record-claim's forks read
    otherwise than by position. A read changed back along the way changed nothing."""
    reads = [_reads_0_2_0(before)]
    reads += [_reads_0_3_0(log) for log in (positional, reattributed, after)]
    links = [_items(r) for r in reads]
    fired: dict[str, set[int]] = {d.name: set() for d in _DIVERGENCES}
    for key in set().union(*links):
        kind, seq = key
        a, p, c, z = (x.get(key, False) for x in links)
        if a == z:
            continue
        named = False
        if a != p:
            if kind not in _BY_RULE:
                raise MigrationError(f"the step's rules changed the {kind} read")
            # progress's site: the record the input's read took its value from, else
            # the output's under the positional readings
            by = [r.progress[1] for r in reads[:2] if r.progress[1] is not None]
            fired[_BY_RULE[kind].name].add(by[0] if kind == "progress" else seq)
            named = True
        if p != c:
            fired[REATTRIBUTED.name].update(forks)
            named = True
        if c != z:
            if kind not in _BY_READING:
                raise MigrationError(f"a reading changed the {kind} read")
            fired[_BY_READING[kind].name].add(seq)
            named = True
        if not named:  # a != z, so some link changed it
            raise MigrationError(f"the {kind} read changed with no link")
    return {name: sorted(seqs) for name, seqs in fired.items()}


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
    DIVERGENCES: ClassVar[tuple[Divergence, ...]] = _DIVERGENCES

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
        positional = _positional_claims(records)
        since = _since(rows)
        claims = _claims(given, positional, sites.claim, resolutions)
        honored = _honored(given, since, sites.honored, claims, resolutions)
        bindings = _bindings(given, sites, claims, honored, resolutions)
        first = {**sites.bound_proven, **_first_claims(rows, sites.bound)}
        named = _named(log, records, sites, claims, honored, bindings)
        out = [
            x._replace(
                request_id=e.request_id,
                body=_dumps(e.body) if x.seq in named.rebodied else x.body,
            )
            for x, e in zip(rows, named.records, strict=True)
        ]
        created = max((x.created_at for x in rows), default=0.0)
        out += [
            Row(e.seq, e.topic, e.name, e.request_id, _dumps(e.body), created)
            for e in named.appended
        ]
        readings: dict[str, dict[int, Json]] = {
            RECORD_CLAIM.name: {s: claims[s] for s in sites.claim},
            STOP_HONORED.name: {s: _seqs(honored[s]) for s in sites.honored},
            LEASE_BOUND.name: {s: bindings[s] for s in sites.bound},
        }
        divergences = _divergences(
            records,
            _named(log, records, sites, positional, since, first).written,
            _named(log, records, sites, claims, since, first).written,
            named.written,
            [seq for seq in sites.claim if claims[seq] != positional[seq]],
        )
        return Migrated(
            out,
            readings,
            {name: tuple(seqs) for name, seqs in divergences.items()},
        )
