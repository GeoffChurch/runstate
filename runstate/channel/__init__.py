"""The runstate substrate: a per-run, append-only **topic log** (v0.2).

A channel is one ordered, retained, multi-reader log of *envelopes*
``{seq, topic, name?, request_id?, body}``. The substrate routes/indexes on the
envelope and never parses ``body``. See docs/design-v0.2.md §4.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Envelope:
    """One record in a channel's log.

    ``topic`` is the closed, protocol-owned routing key; ``name`` is the open,
    application-owned identifier (e.g. a metric name); ``request_id`` correlates
    a response to its request and scopes visibility; ``body`` is opaque to the
    substrate.
    """

    seq: int
    topic: str
    name: Optional[str]
    request_id: Optional[str]
    body: dict


__all__ = ["Envelope"]
