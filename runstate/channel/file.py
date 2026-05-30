"""FileChannel: zero-dependency Channel backend using files on disk.

Layout (per run):
    <root>/<run_id>/
        to_worker/<seq>.json          # messages from orchestrator to worker
        to_orchestrator/<seq>.json    # messages from worker to orchestrator

Sequence numbers are assigned monotonically per direction, protected by
fcntl.flock on the per-direction directory. Atomic write via tempfile +
rename within the same directory (POSIX atomic).

Messages are deleted after read; FileChannel does not preserve history.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from pathlib import Path
from typing import Literal, Optional


_POLL_INTERVAL_S = 0.050  # 50ms; documented latency floor


class FileChannel:
    """File-based Channel: messages as JSON files in per-direction subdirectories."""

    def __init__(
        self,
        *,
        run_id: str,
        role: Literal["worker", "orchestrator"],
        root: str,
    ):
        self.run_id = run_id
        self.role = role
        self._root = Path(root)
        self._run_dir = self._root / run_id
        self._to_worker = self._run_dir / "to_worker"
        self._to_orchestrator = self._run_dir / "to_orchestrator"

        # Worker reads from to_worker, sends to to_orchestrator. Vice versa.
        if role == "worker":
            self._send_dir = self._to_orchestrator
            self._recv_dir = self._to_worker
        elif role == "orchestrator":
            self._send_dir = self._to_worker
            self._recv_dir = self._to_orchestrator
        else:
            raise ValueError(f"role must be 'worker' or 'orchestrator', got {role!r}")

        self._send_dir.mkdir(parents=True, exist_ok=True)
        self._recv_dir.mkdir(parents=True, exist_ok=True)

    def send(self, message: dict) -> None:
        # Allocate sequence number under flock to avoid races between
        # multiple senders on the same direction (e.g., multiple orchestrators).
        seq = self._allocate_seq(self._send_dir)
        payload = json.dumps(message, sort_keys=True, separators=(",", ":"))

        target = self._send_dir / f"{seq:020d}.json"
        # Atomic write: write to tempfile in the same directory, then rename.
        tmp = self._send_dir / f".{seq:020d}.json.tmp"
        tmp.write_text(payload, encoding="utf-8")
        os.rename(tmp, target)

    def recv(self, timeout: Optional[float] = None) -> Optional[dict]:
        deadline = None if timeout is None else time.monotonic() + timeout

        while True:
            msg = self._try_recv_one()
            if msg is not None:
                return msg
            if timeout == 0:
                return None
            if deadline is not None and time.monotonic() >= deadline:
                return None
            time.sleep(_POLL_INTERVAL_S)

    def close(self) -> None:
        # FileChannel holds no persistent handles; nothing to close.
        pass

    # ----- internals -----

    def _allocate_seq(self, direction_dir: Path) -> int:
        """Allocate the next sequence number for sends in this direction.

        Held under fcntl.flock on a per-direction lock file to serialize
        concurrent senders. Finds the max existing seq number and returns
        max + 1.
        """
        lock_path = direction_dir / ".lock"
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                max_seq = -1
                for entry in direction_dir.iterdir():
                    if entry.name.startswith("."):
                        continue
                    if not entry.name.endswith(".json"):
                        continue
                    try:
                        n = int(entry.name[: -len(".json")])
                    except ValueError:
                        continue
                    if n > max_seq:
                        max_seq = n
                return max_seq + 1
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def _try_recv_one(self) -> Optional[dict]:
        """Attempt one non-blocking receive. Returns the message or None."""
        # Hold an exclusive lock on the per-direction lockfile while we
        # look for the smallest unconsumed message and atomically claim
        # it via unlink. Multiple receivers on the same direction would
        # race; the unlink makes the contention safe.
        lock_path = self._recv_dir / ".lock"
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                candidates = []
                for entry in self._recv_dir.iterdir():
                    if entry.name.startswith("."):
                        continue
                    if not entry.name.endswith(".json"):
                        continue
                    try:
                        n = int(entry.name[: -len(".json")])
                    except ValueError:
                        continue
                    candidates.append((n, entry))
                if not candidates:
                    return None
                candidates.sort(key=lambda t: t[0])
                _, path = candidates[0]
                try:
                    payload = path.read_text(encoding="utf-8")
                except FileNotFoundError:
                    return None
                # Consume by unlinking.
                try:
                    path.unlink()
                except FileNotFoundError:
                    # Another reader beat us; just return None and retry.
                    return None
                return json.loads(payload)
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
