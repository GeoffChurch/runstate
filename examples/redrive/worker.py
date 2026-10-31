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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import runstate


@dataclass(frozen=True)
class Checkpoint:
    """One saved state: its node (so it is a ``NodeRef``) and its file."""

    node: int
    path: Path

    def load(self) -> dict[str, Any]:
        state: dict[str, Any] = json.loads(self.path.read_text())
        return state


def write(path: Path, payload: dict[str, Any]) -> None:
    """Publish ATOMICALLY: temp file, then ``os.replace`` into position.

    ``write_text`` truncates before it fills, so a death mid-write -- exactly what this
    example simulates -- can leave a TRUNCATED checkpoint the resume cannot parse,
    turning a crash-once demo into a crash-forever one. ``os.replace`` is atomic within
    a directory: a reader sees a whole file or none, never a half-file.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload))
    os.replace(tmp, path)


def target_of(atom: dict[str, Any]) -> runstate.Target:
    """The inverse of ``Target.atom()``: ``{"stream": x, "n": n}`` or
    ``{"commits": n}``. The environment contract is this example's own: a
    subprocess has no defined way to receive a ``Target`` (``Command`` is not
    ``Drivable``)."""
    if "commits" in atom:
        return runstate.Target(runstate.Commits(), atom["commits"])
    return runstate.Target(atom["stream"], atom["n"])


def main() -> None:
    # The drive's target, plumbed by the producer.
    until = target_of(json.loads(os.environ["REDRIVE_UNTIL"]))
    state_dir = Path(os.environ["REDRIVE_STATE"])  # checkpoint + crash-marker dir
    rid = os.environ["RUNSTATE_RUN_ID"]
    crashed = state_dir / f"{rid}.crashed"
    ch = runstate.current_channel()

    # Choose BEFORE claiming, among the checkpoints the store holds: the newest on
    # the head's lineage. A crashed claim's newer commits have no checkpoint, so
    # the resume branches at the last one saved, and the commits after it stay on
    # the log, off the new lineage.
    candidates = [
        Checkpoint(int(p.name.split(".")[1]), p)
        for p in state_dir.glob(f"{rid}.*.ckpt")
    ]
    chosen = runstate.OnHeadLineage().choose(
        ch, candidates, head=runstate.LatestClaimHead()
    )
    state = {"loss": 5.0}

    def save(node: int) -> None:  # the Worker calls it right after `node` lands
        write(state_dir / f"{rid}.{node}.ckpt", {"node": node, "loss": state["loss"]})

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
