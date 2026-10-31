"""The coordinate types of log format 0.4.0 (docs/specs/commits-and-lineage.md §2,
§4): ``Commits``, the lineage's own clock; ``Target``, "the first n of x"; and
``NodeRef``, a fixed node.

Every progress number is a count (§2, "The coordinate"): ``count(X, node)`` is the
number of X's elements on the lineage up to and including ``node``, where X is an
eager stream or ``Commits()``. Empty is 0, with no sentinel, and a target ``n`` is
reached if and only if the count is at least ``n``.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any, Protocol

# subscription-v0.4's ``Count``: a threshold the wire can carry is at most 2^53 - 1.
# Defined once, here; ``schedule.py`` takes it with ``Target``.
_MAX_COUNT = 2**53 - 1


@dataclass(frozen=True)
class Commits:
    """The lineage's own clock (§2): ``count(Commits(), node)`` is the number of
    ``lifecycle.commit`` nodes on the lineage up to and including ``node``, the
    number of iterations; an exit adds none. "Step" means this count everywhere.

    A singleton marker type: every instance is equal, and no stream name, a
    ``str``, can collide with it, so one ``str | Commits`` names either kind of X."""


@dataclass(frozen=True)
class Target:
    """The first ``n`` of ``x`` (§4): ``x`` an eager stream name or ``Commits()``,
    ``n >= 0``, checked once here, so every consumer (``steps(until)``, ``ensure``,
    the producer seam's ``extend``) takes it unconverted. A target of 0 is reached
    at once. It is also the condition algebra's lineage atom, ``{stream: x, n}`` or
    ``{commits: n}``, which ``schedule.parse_schedule`` builds.

    ``n`` is also at most 2^53 - 1, so that ``atom()`` is always an atom the wire
    admits (subscription-v0.4's ``Count``). Whether ``x`` names an eager stream
    rather than a source is a fact of a claim's declaration, not of the target:
    ``steps()`` and ``ensure`` check it against the claim (plan, open question 1)."""

    x: str | Commits
    n: int

    def __post_init__(self) -> None:
        if not (isinstance(self.x, Commits) or (isinstance(self.x, str) and self.x)):
            raise ValueError(
                f"x must be a non-empty stream name or Commits(), got {self.x!r}"
            )
        n = self.n
        if not (isinstance(n, int) and not isinstance(n, bool) and 0 <= n):
            raise ValueError(f"n must be an integer >= 0, got {n!r}")
        if n > _MAX_COUNT:
            raise ValueError(f"n must be at most 2^53 - 1, got {n!r}")

    def atom(self) -> dict[str, Any]:
        """The wire's spelling (§4): ``{"stream": x, "n": n}`` or ``{"commits": n}``,
        a progress atom of subscription-v0.4's condition algebra."""
        if isinstance(self.x, Commits):
            return {"commits": self.n}
        return {"stream": self.x, "n": self.n}

    @classmethod
    def from_atom(cls, atom: Mapping[str, object]) -> Target:
        """The inverse of ``atom()``: ``Target.from_atom(t.atom()) == t``. For a
        target that crossed a boundary as data (an environment variable, a job's
        arguments), so the caller converts it here, once, rather than by hand.

        ``ValueError`` on anything else: a mapping that is not exactly one of the
        two shapes, or a number or name the constructor refuses (a bool, a
        whole-number float, an n out of range, an empty stream name). A condition
        that is not a lone progress atom is ``parse_schedule``'s to read."""
        keys = set(atom)
        if keys == {"commits"}:
            x: object = Commits()
        elif keys == {"stream", "n"}:
            x = atom["stream"]
        else:
            raise ValueError(
                'a target atom is {"stream": x, "n": n} or {"commits": n}, '
                f"got keys {sorted(map(str, keys))}"
            )
        n = atom["commits"] if isinstance(x, Commits) else atom["n"]
        return cls(x, n)  # type: ignore[arg-type]  # __post_init__ checks both


class NodeRef(Protocol):
    """A fixed node (§4): its one member is ``.node``, the node's seq, named for the
    one thing the Worker reads. ``AtNode`` is one, and so is a checkpoint object
    (§6). A resolved ``Head`` is not, since its node may be ``None``. The type
    cannot tell a checkpoint from any other object with an integer ``.node``; the
    load assertion (§6) checks that the state loaded matches."""

    @property
    def node(self) -> int: ...
