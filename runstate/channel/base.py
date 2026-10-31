"""The Channel surface: the abstract base every substrate backend implements.

A Channel is a **handle on a run's shared topic log — not the log itself.** The
log is the durable, shared thing (a SQLite file; the process-global in-memory
registry); a Channel is one open handle onto it. Closing a handle releases *that
handle's* resources (a SQLite connection + its WAL sidecar fds; nothing for the
in-memory backend) — it does **not** delete the log, stop the worker, or affect
any other handle on the same run. Many handles coexist on one run (the worker, an
observer, a Watcher), each opened and closed independently; the log and every
other handle outlive any one of them.

So ``with create_channel(...) as ch:`` scopes the *handle*, not the *run*: the
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
from typing import NamedTuple, Protocol, overload, runtime_checkable

from .envelope import Body, Envelope


class RunNotFound(LookupError):
    """A run with **no records** was asked to be attached to.

    The uniform absence signal across backends: a run *is* its records (an
    append-only log has no existence independent of them), so a nonexistent run
    and an empty run are the same miss. Raised by ``attach_channel`` (the
    non-creating locator) — never by ``create_channel`` (which births). A
    ``LookupError`` because that is exactly what this is: the lookup found no
    run. Callers that treat "no run yet" as "proceed" (the launch deciders)
    catch it; a birth site that leaks it fails loudly rather than manufacturing
    a phantom empty run (docs/specs/channel-locators.md)."""


class Channel(ABC):
    """A handle on one run's append-only topic log (see the module docstring)."""

    # Typed by its mode: an unconditional append always lands or raises, so it is
    # ``int``, and only a compare-and-append can return ``None``. Every backend
    # repeats these two overloads.
    @overload
    def send(
        self,
        body: Body,
        *,
        topic: str,
        name: str | None = None,
        request_id: str | None = None,
    ) -> int: ...

    @overload
    def send(
        self,
        body: Body,
        *,
        topic: str,
        name: str | None = None,
        request_id: str | None = None,
        expected_seq: int | None,
    ) -> int | None: ...

    @abstractmethod
    def send(
        self,
        body: Body,
        *,
        topic: str,
        name: str | None = None,
        request_id: str | None = None,
        expected_seq: int | None = None,
    ) -> int | None:
        """Append ``body`` under ``topic`` (+ optional ``name`` / ``request_id``),
        returning the new ``seq``. With ``expected_seq`` it is a compare-and-append:
        the record lands iff the log's max seq still equals ``expected_seq``; ``None``
        means the claim was provably lost (the log moved), a raise means the outcome
        was indeterminate (a backend fault) — never a silent loss. Without it the
        append always lands or raises, so its type is ``int``."""

    @abstractmethod
    def read(
        self,
        after: int = 0,
        *,
        topics: list[str] | None = None,
        name: str | None = None,
        request_ids: list[str] | None = None,
        limit: int | None = None,
    ) -> list[Envelope]:
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


# --- optional session capability: evidence for a death report ---
# A backend whose every connection holds a lock of its own from before its first
# write, and stamps every row with it, can say of a record whether the session that
# wrote it has ended (commits-and-lineage.md §3; channel-postgres.md, "Liveness").
# Its answer is EVIDENCE that the claim can write nothing more -- a channel never
# reconnects, and every record naming a claim travels on the claim's session -- which
# report_if_dead turns into a record. It never arbitrates a claim: the CAS stays the
# only arbiter. Opt-in and backend-specific (PostgresChannel), isinstance-dispatched.


class Session(NamedTuple):
    """A writer connection's identity, as stamped on the rows it wrote."""

    server: str  # the writing server: its system identifier and the row's WAL timeline
    key: int  # the connection's advisory-lock key, a signed 64-bit integer


@runtime_checkable
class SessionProbe(Protocol):
    def writer_session(self, seq: int) -> Session | None:
        """The stamp on the record at ``seq``, or None when it has none (a migrated row).
        Raises ``LookupError`` when the run has no record at ``seq``."""
        ...

    def session_ended(self, session: Session) -> bool | None:
        """True when no connection holds ``session.key``; False when one does; None when
        this connection cannot tell: another server's stamp, or a standby."""
        ...
