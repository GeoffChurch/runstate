"""The Channel surface: the abstract base every substrate backend implements.

A Channel is a **handle on a run's shared topic log — not the log itself.** The
log is the durable, shared thing (a SQLite file; the process-global in-memory
registry); a Channel is one open handle onto it. Closing a handle releases *that
handle's* resources (a SQLite connection + its WAL sidecar fds; nothing for the
in-memory backend) — it does **not** delete the log, stop the worker, or affect
any other handle on the same run. Many handles coexist on one run (the worker, an
observer, a Watcher), each opened and closed independently; the log and every
other handle outlive any one of them.

So ``with open_channel(...) as ch:`` scopes the *handle*, not the *run*: the
context manager closes this handle at block exit, while the run's log persists.
Use-after-close is a backend error (a closed SQLite connection raises), not
defined behavior.

The surface is declared here (the abstract methods) and pinned *behaviorally* by
the backend-parametrized conformance suite in ``tests/test_channel.py`` — every
backend must pass it independently. ``send(expected_seq=)`` is the substrate's
compare-and-append and ``last_seq()`` is its read half; see the package docstring
and design-v0.2.md §4.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol, runtime_checkable

from .envelope import Body, Envelope


class Channel(ABC):
    """A handle on one run's append-only topic log (see the module docstring)."""

    @abstractmethod
    def send(self, body: Body, *, topic: str, name: str | None = None,
             request_id: str | None = None, expected_seq: int | None = None) -> int | None:
        """Append ``body`` under ``topic`` (+ optional ``name`` / ``request_id``),
        returning the new ``seq``. With ``expected_seq`` it is a compare-and-append:
        the record lands iff the log's max seq still equals ``expected_seq``; ``None``
        means the claim was provably lost (the log moved), a raise means the outcome
        was indeterminate (a backend fault) — never a silent loss."""

    @abstractmethod
    def read(self, after: int = 0, *, topics: list[str] | None = None,
             name: str | None = None, request_ids: list[str] | None = None,
             limit: int | None = None) -> list[Envelope]:
        """Envelopes with ``seq > after``, in order, filtered by ``topics`` (exact or
        a ``"prefix.>"`` wildcard), ``name``, and ``request_ids`` (which also admits
        unaddressed broadcasts). Non-destructive — cursors are caller-owned."""

    @abstractmethod
    def latest(self, topic: str, name: str | None = None) -> Envelope | None:
        """The most recent envelope for ``topic`` (and ``name`` if given), or None."""

    @abstractmethod
    def last_seq(self) -> int:
        """The log's last ``seq`` (``0`` = empty) — the CAS's read half: exactly the
        value ``send(expected_seq=...)`` requires callers to assert, and (with seq
        contiguous, §4) the record count. O(1) on every backend. Also the cheap
        has-anything-new watermark for an incremental reader."""

    @abstractmethod
    def close(self) -> None:
        """Release this handle's backend resources. Does not touch the log or any
        other handle on the run (see the module docstring)."""

    def __enter__(self) -> Channel:
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.close()


# --- optional liveness capability (off the base ABC; the collections.abc base-plus-
# mixins shape) ---
# A backend whose handle is bound to a connection that auto-releases on death can
# offer a *definitive* cross-host liveness signal -- where a bare-handle probe
# (os.kill on a foreign host) must abstain. It is exposed as two capability
# Protocols, isinstance-dispatched and split by VIEWPOINT so a pure observer's
# channel type never advertises a method it must not call (the worker holds; the
# observer probes). This is a liveness SIGNAL the Watcher consumes -- never a claim
# arbiter: the claim stays the uniform CAS. The substrate ABC stays the five pure
# data ops; this is opt-in and backend-specific (PostgresChannel implements both).


@runtime_checkable
class EpisodeHolder(Protocol):
    """Worker-side capability: pin THIS episode's liveness after winning the claim.
    The signal is held for the episode's life and auto-releases when the holding
    connection dies (clean stop or crash) -- no explicit release call."""

    def hold_episode(self, started_seq: int) -> None: ...


@runtime_checkable
class EpisodeProbe(Protocol):
    """Observer-side capability: read whether an episode is still live -- a definitive
    cross-host signal where a bare-handle probe abstains (a foreign host's pid table
    isn't ours to read). ``started_seq`` identifies the episode on the run's log."""

    def episode_alive(self, started_seq: int) -> bool: ...
