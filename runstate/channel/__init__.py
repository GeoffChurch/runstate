"""Channel: per-run durable bidirectional message transport.

The Channel Protocol is the substrate. Two implementations ship in v0.1:
- FileChannel: zero-dependency, files on disk
- SqliteChannel: stdlib sqlite3, single DB per run

Both back the same Protocol; choice is a one-line factory swap.
"""

from __future__ import annotations

from typing import Protocol, Literal, Optional, runtime_checkable


@runtime_checkable
class Channel(Protocol):
    """A directional view of a per-run message channel.

    A Channel instance has a fixed direction determined by its role.
    Two Channel instances pointed at the same run_id with opposite roles
    form a bidirectional transport.

    Messages are JSON-serializable dicts. The Channel imposes no schema
    beyond serializability — the typed vocabulary in runstate.control /
    runstate.events is OPTIONAL; users may send arbitrary dicts.
    """

    role: Literal["worker", "orchestrator"]
    run_id: str

    def send(self, message: dict) -> None:
        """Send a message. Durable: survives process crash.

        Uses atomic write semantics. Returns when the message is
        committed to durable storage.
        """
        ...

    def recv(self, timeout: Optional[float] = None) -> Optional[dict]:
        """Receive the next unread message in this direction.

        timeout=0     → non-blocking, returns None if no message
        timeout=N>0   → poll up to N seconds; backend-defined latency floor
        timeout=None  → block indefinitely until a message arrives
        """
        ...

    def close(self) -> None:
        """Release resources (file handles, DB connections)."""
        ...


def open_channel(
    run_id: str,
    *,
    role: Literal["worker", "orchestrator"],
    root: str,
    backend: Literal["file", "sqlite"] = "file",
) -> Channel:
    """Open a Channel by run_id.

    Factory function returning a Channel of the requested backend.

    Args:
        run_id: identifier for the run; must be filesystem-safe.
        role: 'worker' or 'orchestrator'. Determines send/recv direction.
        root: base directory under which run state lives.
              The Channel uses <root>/<run_id>/ as its run directory.
        backend: 'file' (zero deps) or 'sqlite' (stdlib sqlite3).
    """
    if backend == "file":
        from .file import FileChannel
        return FileChannel(run_id=run_id, role=role, root=root)
    if backend == "sqlite":
        from .sqlite import SqliteChannel
        return SqliteChannel(run_id=run_id, role=role, root=root)
    raise ValueError(f"Unknown backend: {backend!r}. Expected 'file' or 'sqlite'.")


__all__ = ["Channel", "open_channel"]
