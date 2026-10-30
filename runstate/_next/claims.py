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

from ..channel import Channel, Envelope
from .vocabulary.payloads import Stopped, Topic
from .walk import readable


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
