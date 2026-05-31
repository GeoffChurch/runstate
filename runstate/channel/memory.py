"""MemoryChannel: an in-memory topic log (zero dependencies).

The same Channel surface as SqliteChannel, backed by an append-only Python list
of Envelopes. Bodies are JSON-snapshotted on send (immutable + JSON-serializable),
matching the SQLite backend. Useful for tests and for validating that the Channel
surface is backend-agnostic.

A shared ``log`` list may be passed in so that several MemoryChannels act as
multiple readers/writers of the *same* run (the in-memory analogue of several
SqliteChannels on one file). Instances that share a log MUST also share a
``lock`` so the ``seq`` read-modify-write stays atomic across them — the
registry in ``open_channel`` co-locates one lock per shared log; a standalone
``MemoryChannel()`` gets its own.
"""

from __future__ import annotations

import json
import threading

from .envelope import Envelope


class MemoryChannel:
    def __init__(self, log: list | None = None, lock=None, *, json_default=None):
        self._log: list[Envelope] = log if log is not None else []
        self._lock = lock if lock is not None else threading.Lock()
        self._json_default = json_default

    def send(self, body: dict, *, topic: str, name=None, request_id=None) -> int:
        # The json round-trip both validates serializability and snapshots the
        # body to an independent, JSON-safe copy (json_default coerces exotic
        # types on the way out; the stored copy then needs no hook on read).
        snapshot = json.loads(json.dumps(body, default=self._json_default))
        with self._lock:
            seq = len(self._log) + 1
            self._log.append(Envelope(seq, topic, name, request_id, snapshot))
        return seq

    def read(
        self,
        after: int = 0,
        *,
        topics=None,
        name=None,
        request_ids=None,
        limit=None,
    ) -> list[Envelope]:
        with self._lock:
            log = list(self._log)
        out: list[Envelope] = []
        for e in log:
            if e.seq <= after:
                continue
            if topics is not None and not _topic_match(e.topic, topics):
                continue
            if name is not None and e.name != name:
                continue
            if request_ids is not None and not (
                e.request_id is None or e.request_id in request_ids
            ):
                continue
            out.append(_snapshot(e))
            if limit is not None and len(out) >= limit:
                break
        return out

    def latest(self, topic: str, name=None) -> Envelope | None:
        with self._lock:
            log = list(self._log)
        for e in reversed(log):
            if e.topic == topic and (name is None or e.name == name):
                return _snapshot(e)
        return None

    def close(self) -> None:
        pass


def _snapshot(e: Envelope) -> Envelope:
    """Return a copy with an independent body, so callers can't mutate the store."""
    return Envelope(e.seq, e.topic, e.name, e.request_id, json.loads(json.dumps(e.body)))


def _topic_match(topic: str, patterns) -> bool:
    for p in patterns:
        if p.endswith(".>"):
            if topic.startswith(p[:-1]):  # "control.>" -> prefix "control."
                return True
        elif topic == p:
            return True
    return False
