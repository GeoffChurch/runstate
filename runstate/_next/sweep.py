"""The sequential sweep of log format 0.4.0, staged (docs/specs/commits-and-lineage.md
§5; plan, open question 18): ``Variant``, the run a launcher's target is launched
into.

STAGED. Task 19 of docs/plans/2026-10-28-layer2-live.md moves this module over
``runstate/sweep.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class Variant(Generic[T]):
    """One run of a sweep, or the run a producer drives (``launch_producer``):
    ``run_id``, the run's address, and ``target``, the record its launcher takes
    (``Launcher[T]``). The record carries everything that changes the worker's
    result, its arguments or its environment, so there are no launch keywords
    beside it (plan, open question 18)."""

    run_id: str
    target: T
