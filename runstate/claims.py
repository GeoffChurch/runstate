"""The claim plane's presence predicates of log format 0.4.0, staged
(docs/specs/commits-and-lineage.md §3, "The helpers"; §5, "The answer fold"): which
claim a record names, which claim it ends, a claim's window, and the records in it
that end the claim.

INTERNAL. It is what every reader of a claim's end shares: the reads and the folds
(``observables``), and the launchers' reporters. It is not part of
``runstate.__all__``: the public claim-plane reads are ``observables'``. Its names
carry no leading underscore because other modules of the package import them.

STAGED. Task 19 of docs/plans/2026-10-28-layer2-live.md moves this module to
``runstate/claims.py``.

PRESENCE NEVER RAISES. Each predicate here asks whether a record is there, never
what an unreadable one says: a contradiction in a claim's window, two readable
exits say, is the verdict's to raise (``observables.claim_verdict``), never a
reporter's or a fold's (§3), so a malformed record in an old claim's window cannot
poison the present.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from .channel import Channel, Envelope
from .vocabulary.payloads import Bound, Stopped, Topic
from .walk import FIRST_WINDOW, readable

# The attempts each compare-and-append loop makes before it gives up: a death
# report's (``launcher``), and the Worker's claim, drain and exit. Such a CAS loses
# only to a record that landed inside one round trip of its loop (``last_seq``, the
# reads that loop makes, perhaps a probe, then the append): about a millisecond on
# SQLite and a few on Postgres. Losing 256 in a row takes a log that grew by a record
# in each of 256 round trips, a quarter of a second to a few seconds of out-writing
# every read: no burst, but a writer outrunning its reader, where more attempts
# change nothing and the caller had better learn that its record has not landed.
CAS_ATTEMPTS = 256


def request_name(e: Envelope) -> str | None:
    """The request ``e`` names: its ``request_id``, or ``None`` when that is missing
    or empty. An empty id is no name (§4, "A drain"): nothing can spend it, so on
    every control topic and in every fold a record bearing one is nameless."""
    return e.request_id or None


def spent_by(e: Envelope) -> Iterable[str]:
    """The request ids ``e`` spends by name, for the whole log (§2, "Requests"): a
    readable exit's ``honored``, and the name of a ``control.withdraw`` or a
    ``lifecycle.nak``. An unreadable exit's ``honored`` is not read, as no fold
    reads more of an unreadable record (§5), and a nameless record spends nothing.

    Request ids are one namespace: an id spent here is dead on arrival on every
    control topic, whatever kind of request first bore it.

    A node's ``expired`` is not here, since it spends by class, not by name (§2,
    "Requests"; §5, "The answer fold"): a lease's from any node, its claim's, and
    a durable subscription's only on the head's lineage, so a durable subscription
    another lineage expired is live again. The folds (``live_demand``) and the
    Worker (its rebuild, and each node it lands) each read it so."""
    if e.topic == Topic.LIFECYCLE_STOPPED:
        exit_ = readable(Stopped, e)
        return () if exit_ is None else [r for r in exit_.honored if r]
    if e.topic in (Topic.CONTROL_WITHDRAW, Topic.LIFECYCLE_NAK):
        r = request_name(e)
        return () if r is None else (r,)
    return ()


def named_claim(e: Envelope) -> int | None:
    """The claim a record's ``claim_seq`` names, read on its own so that a record
    whose other fields are unreadable still names its claim; ``None`` when it
    names none."""
    if "claim_seq" not in e.body:
        return None
    c = e.body["claim_seq"]
    return c if isinstance(c, int) and not isinstance(c, bool) and c >= 1 else None


def ends(e: Envelope) -> int | None:
    """The claim ``e`` ends, if any (§2, "How a claim ended"; §5, "The answer
    fold"): a ``lifecycle.stopped`` or a ``supervisor.died`` whose ``claim_seq``
    names a seq below it, readable or not. Its presence is all it says, so it never
    raises: ``claim_verdict`` is not ``None`` exactly where it ends the claim, and
    is where a contradiction in the claim's window raises."""
    if e.topic not in (Topic.LIFECYCLE_STOPPED, Topic.SUPERVISOR_DIED):
        return None
    claim = named_claim(e)
    return claim if claim is not None and claim < e.seq else None


class Bindings:
    """Which subscriptions a ``lifecycle.bound`` binds to a claim that has ended
    (§2, "Per claim"; §5, "The answer fold"): a lease is void once its claim has
    ended. Applied record by record in seq order, from the claims, the records that
    end them (``ends``, presence alone, which never raises) and the bindings.

    Its two readers take the lease question from it, since only they know a
    subscription's class (its first send's): the answer fold (``observables``),
    which kills a lease the moment ``apply`` names it, and the Worker's claim, which
    folds the log below its claim and asks ``void`` of each lease its drain meets.
    A binding before its subscription's first send is kept, so that send can be
    judged when it lands."""

    TOPICS: tuple[Topic, ...] = (
        Topic.LIFECYCLE_STARTED,
        Topic.LIFECYCLE_STOPPED,
        Topic.SUPERVISOR_DIED,
        Topic.LIFECYCLE_BOUND,
    )
    """The records ``apply`` reads; it passes over any other."""

    def __init__(self) -> None:
        self._bound: dict[str, set[int]] = {}  # each id's claims
        self._bound_to: dict[int, set[str]] = {}  # each held claim's ids
        self._claims: set[int] = set()
        self._ended: set[int] = set()  # the claims a record has ended

    def apply(self, e: Envelope) -> Iterable[str]:
        """Apply ``e``; the ids it newly binds to an ended claim: every id bound to
        the claim it ends, or the id it binds when that claim has already ended. A
        record ends a claim below it, so whether that was a claim is known."""
        if e.topic == Topic.LIFECYCLE_STARTED:
            self._claims.add(e.seq)
            return ()
        ended = ends(e)
        if ended is not None and ended in self._claims and ended not in self._ended:
            self._ended.add(ended)
            return self._bound_to.pop(ended, set())
        if e.topic == Topic.LIFECYCLE_BOUND:
            r, b = request_name(e), readable(Bound, e)
            if r is not None and b is not None:
                self._bound.setdefault(r, set()).add(b.claim_seq)
                if b.claim_seq in self._ended:
                    return (r,)
                self._bound_to.setdefault(b.claim_seq, set()).add(r)
        return ()

    def void(self, r: str) -> bool:
        """Is ``r`` bound to a claim that has ended? Void, if ``r`` is a lease."""
        return not self._bound.get(r, set()).isdisjoint(self._ended)

    def forget(self, r: str) -> None:
        """Drop ``r``'s bindings: its reader holds it dead, which never reverts."""
        self._bound.pop(r, None)


def claim_window(
    ch: Channel, claim: Envelope, w: int, topics: list[str]
) -> list[Envelope]:
    """The records on ``topics`` after ``claim`` and at or below ``w``: the
    claim's window, since a record cannot speak for a claim it never saw. The
    read is capped at ``w``'s distance from the claim, and the filter is the
    guard; it is never ``limit=0``."""
    if w <= claim.seq:
        return []
    got = ch.read(after=claim.seq, topics=topics, limit=w - claim.seq)
    return [e for e in got if e.seq <= w]


def endings(ch: Channel, claim: Envelope, w: int) -> list[Envelope]:
    """The records at or below ``w`` that end ``claim``, by presence (§5, the
    answer fold's ``ended``): each ``lifecycle.stopped`` and ``supervisor.died``
    naming it, readable or not. Empty exactly while the claim is held; it never
    raises."""
    topics: list[str] = [Topic.LIFECYCLE_STOPPED, Topic.SUPERVISOR_DIED]
    return [e for e in claim_window(ch, claim, w, topics) if ends(e) == claim.seq]


def has_exit(endings: list[Envelope]) -> bool:
    """Has the claim a readable exit among ``endings``? Two of them are a
    contradiction the verdict raises on; here they are one that exists."""
    return any(
        e.topic == Topic.LIFECYCLE_STOPPED and readable(Stopped, e) is not None
        for e in endings
    )


def backward(
    ch: Channel, *, after: int, hi: int, topics: list[str]
) -> Iterator[Envelope]:
    """The records on ``topics`` above ``after`` and at or below ``hi``, newest
    first, read in windows that grow by 4x back from ``hi``: a search that stops at
    its first match costs the distance back to it, not the log's length."""
    width = FIRST_WINDOW
    while hi > after:
        lo = max(after, hi - width)
        window = ch.read(after=lo, topics=topics, limit=hi - lo)
        yield from reversed([e for e in window if e.seq <= hi])
        hi, width = lo, width * 4


def claim_before(ch: Channel, seq: int) -> Envelope | None:
    """The newest ``lifecycle.started`` below ``seq``, or ``None``."""
    return next(
        backward(ch, after=0, hi=seq - 1, topics=[Topic.LIFECYCLE_STARTED]), None
    )


def watermark(ch: Channel, claim: Envelope, w: int) -> int | None:
    """The newest watermark ``claim`` wrote at or below ``w`` (§4, "A drain"): its
    exit's seq, or else the ``consumed_seq`` of its newest commit or heartbeat;
    ``None`` when it wrote none. A claim's watermark only grows and its exit is its
    last record, so the newest of these records is the greatest; one whose
    ``consumed_seq`` is unreadable is passed over. It reads back from ``w`` only as
    far as that record, and never raises."""
    topics: list[str] = [
        Topic.LIFECYCLE_COMMIT,
        Topic.LIFECYCLE_HEARTBEAT,
        Topic.LIFECYCLE_STOPPED,
    ]
    for e in backward(ch, after=claim.seq, hi=w, topics=topics):
        if named_claim(e) != claim.seq:
            continue
        if e.topic == Topic.LIFECYCLE_STOPPED:
            return e.seq
        c = e.body.get("consumed_seq")
        if isinstance(c, int) and not isinstance(c, bool) and c >= 0:
            return c
    return None
