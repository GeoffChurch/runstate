"""MemoryChannel: an in-memory topic log (zero dependencies).

The same Channel surface as SqliteChannel, backed by a ``MemoryLog``: an
append-only Python list of Envelopes. Bodies are JSON-snapshotted on send
(immutable + JSON-serializable), matching the SQLite backend. Useful for tests
and for validating that the Channel surface is backend-agnostic.

Several MemoryChannels on one ``MemoryLog`` act as multiple readers/writers of
the *same* run (the in-memory analogue of several SqliteChannels on one file).
The log carries its own lock, so the ``seq`` read-modify-write stays atomic
across every handle on it; the registry in ``_locate`` keeps one log per run,
and a standalone ``MemoryChannel()`` gets a fresh one.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Sequence

from .base import Channel
from .envelope import Body, Envelope


class MemoryLog:
    """One run's in-memory log, shared by every handle on it: the envelopes, contiguous
    from seq 1, a per-topic index of their positions, and one lock.

    Contiguity is what makes the log its own index: the envelope at seq s sits at
    position s - 1, so a tail is a slice and ``last_seq`` is the length. A log handed
    in that is not contiguous from 1 would read wrong silently, one short or one over,
    so it is refused here rather than at a read.
    """

    def __init__(self, envelopes: Sequence[Envelope]) -> None:
        seqs = [e.seq for e in envelopes]
        if seqs != list(range(1, len(seqs) + 1)):
            raise ValueError(
                f"a memory log's seqs must be contiguous from 1, got {seqs[:10]}"
                + ("..." if len(seqs) > 10 else "")
            )
        # snapshotted as a send snapshots, so the caller's envelopes stay its own
        self._envelopes: list[Envelope] = [_snapshot(e) for e in envelopes]
        # topic -> the positions of its envelopes, ascending: ``latest`` walks one
        # topic's records instead of the whole log.
        self._by_topic: dict[str, list[int]] = {}
        for i, e in enumerate(self._envelopes):
            self._by_topic.setdefault(e.topic, []).append(i)
        self._lock = threading.Lock()

    def append(
        self,
        body: Body,
        *,
        topic: str,
        name: str | None,
        request_id: str | None,
        expected_seq: int | None,
    ) -> int | None:
        """Append at the tail; with ``expected_seq``, only if the tail is still there."""
        with self._lock:
            last = len(self._envelopes)
            if expected_seq is not None and last != expected_seq:
                return None
            seq = last + 1
            self._by_topic.setdefault(topic, []).append(last)
            self._envelopes.append(Envelope(seq, topic, name, request_id, body))
        return seq

    def tail(self, after: int) -> list[Envelope]:
        """The envelopes with seq > ``after``: the slice from position ``after``. A
        negative ``after`` reads from the start, as ``seq > after`` does on the SQL
        backends (a negative slice index would count from the end instead)."""
        with self._lock:
            return self._envelopes[max(after, 0) :]

    def latest(self, topic: str, name: str | None) -> Envelope | None:
        """The last envelope of ``topic`` (and ``name``, when given), walking only that
        topic's positions backwards."""
        with self._lock:
            for i in reversed(self._by_topic.get(topic, [])):
                e = self._envelopes[i]
                if name is None or e.name == name:
                    return e
        return None

    def last_seq(self) -> int:
        with self._lock:
            return len(self._envelopes)  # contiguous from 1, so last == count


class MemoryChannel(Channel):
    def __init__(
        self,
        log: MemoryLog | None = None,
        *,
        json_default: Callable[[object], object] | None = None,
    ) -> None:
        # ``log=None`` is an exempt default: it makes a fresh, private log (where the
        # records live), never a different answer about the records.
        self._log = log if log is not None else MemoryLog([])
        self._json_default = json_default

    def send(
        self,
        body: Body,
        *,
        topic: str,
        name: str | None = None,
        request_id: str | None = None,
        expected_seq: int | None = None,
    ) -> int | None:
        # The json round-trip both validates serializability and snapshots the
        # body to an independent, JSON-safe copy (json_default coerces exotic
        # types on the way out; the stored copy then needs no hook on read).
        snapshot = json.loads(json.dumps(body, default=self._json_default))
        return self._log.append(
            snapshot,
            topic=topic,
            name=name,
            request_id=request_id,
            expected_seq=expected_seq,
        )

    def read(
        self,
        after: int = 0,
        *,
        topics: list[str] | None = None,
        name: str | None = None,
        request_ids: list[str] | None = None,
        limit: int | None = None,
    ) -> list[Envelope]:
        out: list[Envelope] = []
        for e in self._log.tail(after):
            if topics is not None and not _topic_match(e.topic, topics):
                continue
            if name is not None and e.name != name:
                continue
            if request_ids is not None and not (
                e.request_id is None or e.request_id in request_ids
            ):
                continue
            if limit is not None and len(out) >= limit:
                break  # checked before the append, so limit=0 returns nothing
            out.append(_snapshot(e))
        return out

    def latest(self, topic: str, name: str | None = None) -> Envelope | None:
        e = self._log.latest(topic, name)
        return None if e is None else _snapshot(e)

    def last_seq(self) -> int:
        return self._log.last_seq()

    def close(self) -> None:
        pass


def _snapshot(e: Envelope) -> Envelope:
    """Return a copy with an independent body, so callers can't mutate the store."""
    return Envelope(
        e.seq, e.topic, e.name, e.request_id, json.loads(json.dumps(e.body))
    )


def _topic_match(topic: str, patterns: list[str]) -> bool:
    for p in patterns:
        if p.endswith(".>"):
            if topic.startswith(p[:-1]):  # "control.>" -> prefix "control."
                return True
        elif topic == p:
            return True
    return False
