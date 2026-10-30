"""The checkpoint recipe's save policies and choice strategies, staged
(docs/specs/commits-and-lineage.md §6, "Save", "Choosing a checkpoint").

A RECIPE, NOT PROTOCOL. runstate supplies no directory and owns no checkpoint
store: whether a node has a checkpoint is the store's fact, never the log's.

THE WORKER FIXES *WHEN*, THE POLICY DECIDES *WHETHER*. The Worker calls
``SavePolicy.after`` at exactly two moments: immediately after a commit lands, and
immediately after an extendable exit lands, when the process's state is exactly the
state the node commits. It never calls it after a torn exit, whose state is part of
an iteration ahead of its parent, nor after a closed one. So no policy can save at
a wrong moment, and no checkpoint names a node whose state it does not hold: no
lineage lie can be written (§6).

A SAVE THAT RAISES PROPAGATES, and the node it followed stands (§6, amended
2026-10-30): out of ``commit()``, which ends the claim with an errored, torn exit
whose ``parent`` is that node; or out of the exit, which stands with its own
verdict. Nothing records the failed save.

CHOOSING IS A STRATEGY, OVER THE CALLER'S OWN CHECKPOINTS. ``CheckpointChoice``
picks one of the candidates the caller lists from its store, and returns that very
object, typed as the caller's ``C`` (bound to ``NodeRef``), so it loads and is
passed to ``resume_from`` or ``rewind`` with no cast. It takes **complete**
checkpoints only, those whose node is at or below the head's ``w``: by "a node
follows what it names" and the substrate contract, every ancestor of such a node
and every value it commits is readable there.

STAGED. Task 19 of docs/plans/2026-10-28-layer2-live.md moves this module to
``runstate/checkpoint.py``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, TypeVar

from ..channel import Channel
from .observables import HeadStrategy, PrefixLen
from .vocabulary.coordinate import NodeRef
from .walk import walk


class SavePolicy(Protocol):
    """Whether to save at a moment the Worker chose (§6): ``node`` is the seq of
    the node that just landed, ``commits`` its ``count(Commits(), node)``, and
    ``exit`` True when it is an extendable exit rather than a commit."""

    # ``exit`` shadows the builtin, kept since it is the spec's keyword (§6:
    # ``after(node, *, commits, exit)``).
    def after(self, node: int, *, commits: int, exit: bool) -> None: ...


@dataclass(frozen=True)
class NoSave:
    """Never saves. Passed explicitly, since a chunked run without checkpoints
    restarts at the root every claim (§6)."""

    def after(self, node: int, *, commits: int, exit: bool) -> None:
        return None


@dataclass(frozen=True)
class Every:
    """Calls ``fn(node)`` at each commit whose ``commits`` is a multiple of ``k``,
    and at every extendable exit (§6), so a preempted chunk always leaves a
    checkpoint of where it stopped. ``k >= 1``, checked once here."""

    k: int
    fn: Callable[[int], None]

    def __post_init__(self) -> None:
        k = self.k
        if not (isinstance(k, int) and not isinstance(k, bool) and k >= 1):
            raise ValueError(f"k must be an integer >= 1, got {k!r}")

    def after(self, node: int, *, commits: int, exit: bool) -> None:
        if exit or commits % self.k == 0:
            self.fn(node)


# ----- choosing a checkpoint (§6, "Choosing a checkpoint") --------------------------

C = TypeVar("C", bound=NodeRef)
"""The caller's checkpoint type: anything with an integer ``.node``."""


class CheckpointChoice(Protocol):
    """Which of the caller's checkpoints to resume from (§6): one of
    ``candidates``, the very object, or ``None`` when no complete candidate
    qualifies. ``head`` is resolved once, and a candidate is complete when its node
    is at or below the head's ``w``; the strategy reads nothing above it."""

    def choose(
        self, ch: Channel, candidates: Sequence[C], *, head: HeadStrategy
    ) -> C | None: ...


def _complete(candidates: Sequence[C], w: int) -> list[C]:
    """The candidates whose node is at or below ``w``, in the caller's order."""
    return [c for c in candidates if c.node <= w]


@dataclass(frozen=True)
class OnHeadLineage:
    """The complete candidate on the head's lineage nearest the head (§6): the
    suggested preset. It is consistent with the reads and the verdict, which read
    the same lineage, and respects a rewind, which leaves the abandoned branch's
    checkpoints off it. It walks once from the head, O(L + K) for a lineage of L
    nodes and K candidates. ``None`` on the empty lineage, or when no candidate is
    on the lineage. Of two candidates at one node, the first in the caller's
    order."""

    def choose(
        self, ch: Channel, candidates: Sequence[C], *, head: HeadStrategy
    ) -> C | None:
        h = head.resolve(ch)
        complete = _complete(candidates, h.w)
        if h.node is None or not complete:
            return None
        depth = {at.env.seq: i for i, at in enumerate(walk(ch, h.node).nodes)}
        on = [(depth[c.node], c) for c in complete if c.node in depth]
        if not on:
            return None
        deepest = max(i for i, _ in on)
        return next(c for i, c in on if i == deepest)


@dataclass(frozen=True)
class MostProgress:
    """The complete candidate with the largest ``count(stream, node)`` (§6),
    which may lie on a branch a rewind abandoned: it recomputes least, and ignores
    which lineage the head is on. A lookup in the count map (``PrefixLen``'s, one
    pass over the log up to the head's ``w``). Ties go to the higher node seq
    (plan, open question 10), then to the first in the caller's order. A candidate
    whose node is not a node raises ``ValueError``, and one whose lineage is
    malformed ``MalformedRecordError``, as ``prefix_len`` at it does."""

    stream: str

    def choose(
        self, ch: Channel, candidates: Sequence[C], *, head: HeadStrategy
    ) -> C | None:
        h = head.resolve(ch)
        complete = _complete(candidates, h.w)
        if not complete:
            return None
        counts = PrefixLen(self.stream)

        def progress(c: C) -> tuple[int, int]:
            return counts.at(ch, c.node, w=h.w), c.node

        return max(complete, key=progress)


if TYPE_CHECKING:

    def _proof(on: OnHeadLineage, most: MostProgress) -> list[CheckpointChoice]:
        """Checked by ``mypy --strict runstate``, never run: it fails when a choice
        strategy stops satisfying ``CheckpointChoice``, generic in ``C``."""
        return [on, most]
