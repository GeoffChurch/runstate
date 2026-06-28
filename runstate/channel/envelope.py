"""The substrate's core record (v0.2).

``Envelope`` is the one shape every channel reads and writes. It lives in a leaf
module — it imports nothing else in the package — so both the backends and the
package facade can depend on it without a cycle. See docs/design-v0.2.md §4.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

Body = dict[str, Any]
"""An envelope's opaque body: a JSON object the substrate stores but never interprets."""


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
    body: Body
