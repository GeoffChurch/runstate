"""Crash-once worker for the killed-redrive caller pattern (examples/redrive/driver.py).

Drives a decaying loss to the target the producer plumbs in ``REDRIVE_UNTIL``
(``Target.atom()`` JSON), checkpointing by the recipe (commits-and-lineage.md
§6). On its FIRST attempt it exits non-zero partway via ``os._exit`` -- a
*recordless* death (no ``lifecycle.stopped``), the kind a cluster SIGKILL / OOM
produces. On the resume it continues from the newest checkpoint on the head's
lineage and completes. A marker file makes the crash fire exactly once, so the
demo is deterministic.
"""

import json
import math
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import runstate


def node_of(path: Path) -> int:
    """The node a checkpoint file is named by: ``<rid>.<node>.ckpt``."""
    return int(path.name.split(".")[1])


@dataclass(frozen=True)
class Checkpoint:
    """One saved state: its node (so it is a ``NodeRef``) and its file."""

    node: int
    path: Path

    def load(self) -> dict[str, Any]:
        """The state, and the node the file it was read from is named by."""
        state: dict[str, Any] = json.loads(self.path.read_text())
        return {**state, "node": node_of(self.path)}


def write(state_dir: Path, rid: str, payload: dict[str, Any]) -> Callable[[int], None]:
    """Save in two phases (commits-and-lineage.md §6): write the bytes now, to a temp
    file in the checkpoint directory that no listing matches, and return the bind,
    which ``os.replace``s it to ``<rid>.<node>.ckpt`` once the node has landed.

    ``write_text`` in place truncates before it fills, so a death mid-write -- exactly
    what this example simulates -- could leave a TRUNCATED checkpoint the resume cannot
    parse, turning a crash-once demo into a crash-forever one. Here a death before the
    bind leaves only a temp file nothing names, and ``os.replace`` is atomic within a
    directory: a reader sees a whole checkpoint or none.
    """
    fd, tmp = tempfile.mkstemp(dir=state_dir, prefix=f".{rid}.", suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(payload, f)

    def bind(node: int) -> None:
        os.replace(tmp, state_dir / f"{rid}.{node}.ckpt")

    return bind


def main() -> None:
    # The drive's target, plumbed by the producer. The environment contract is this
    # example's own: a subprocess has no defined way to receive a ``Target``
    # (``Command`` is not ``Drivable``), so it crosses as its atom.
    until = runstate.Target.from_atom(json.loads(os.environ["REDRIVE_UNTIL"]))
    state_dir = Path(os.environ["REDRIVE_STATE"])  # checkpoint + crash-marker dir
    rid = os.environ["RUNSTATE_RUN_ID"]
    crashed = state_dir / f"{rid}.crashed"
    ch = runstate.current_channel()

    # Choose BEFORE claiming, among the checkpoints the store holds: the newest on
    # the head's lineage. A crashed claim's newer commits have no checkpoint, so
    # the resume branches at the last one saved, and the commits after it stay on
    # the log, off the new lineage.
    candidates = [Checkpoint(node_of(p), p) for p in state_dir.glob(f"{rid}.*.ckpt")]
    chosen = runstate.OnHeadLineage().choose(
        ch, candidates, head=runstate.LatestClaimHead()
    )
    state = {"loss": 5.0}

    def save() -> Callable[[int], None]:  # just before a node is written
        return write(state_dir, rid, {"loss": state["loss"]})

    # Checkpoint every 3 commits (and at every extendable exit).
    with runstate.Worker(
        ch, resume_from=chosen, sources=[], save=runstate.Every(3, save)
    ) as w:
        if chosen is not None:  # load after claiming; assert it is the node claimed
            loaded = chosen.load()
            assert loaded["node"] == w.resume_node
            state["loss"] = loaded["loss"]
        for step in w.steps(until):
            w.emit("loss", state["loss"])  # before the commit: this node's value
            if step == 4 and not crashed.exists():  # first attempt only: crash
                crashed.write_text("1")
                os._exit(1)  # recordless death: the reap reports it errored, error=None
            state["loss"] *= math.exp(-0.3)
        if not w.stop_pending:  # reached the target -> intrinsic done
            w.stopped(completed=True, error=None)


if __name__ == "__main__":
    main()
