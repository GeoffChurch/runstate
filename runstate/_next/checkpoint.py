"""The checkpoint recipe's save policies, staged (docs/specs/commits-and-lineage.md
§6, "Save").

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

STAGED. Task 19 of docs/plans/2026-10-28-layer2-live.md moves this module to
``runstate/checkpoint.py``; Task 13 adds the choice strategies.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


class SavePolicy(Protocol):
    """Whether to save at a moment the Worker chose (§6): ``node`` is the seq of
    the node that just landed, ``commits`` its ``count(Commits(), node)``, and
    ``exit`` True when it is an extendable exit rather than a commit."""

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
