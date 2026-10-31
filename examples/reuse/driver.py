"""Reuse + extend via the memoizer (log format 0.4.0, docs/specs/memoizer.md).

``ensure(producer, Target("loss", N))`` returns at once when the run's lineage
already holds N ``loss`` elements (cache hit, no worker), else relaunches to
extend and waits. Either way it returns ``Reached(node)``, a node the caller
reads at with ``AtNode(node)``, which is permanent. A content-addressed run_id
is the cache key (the run_id recipe). In-process via ThreadLauncher + the
memory backend, so the whole demo is self-contained.

The worker checkpoints by the recipe (commits-and-lineage.md §6): the Worker
saves right after each node lands, when the state is exactly what the node
commits; the next claim chooses a checkpoint on the head's lineage, claims
from it, loads it, and asserts the two agree.
"""

import hashlib
import json
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import runstate


@dataclass(frozen=True)
class Checkpoint:
    """One saved state: the node it was saved at, which makes it a ``NodeRef``
    the Worker can resume from, and the file that holds the state."""

    node: int
    path: Path

    def load(self) -> dict[str, float | int]:
        state: dict[str, float | int] = json.loads(self.path.read_text())
        return state


def saved(ckpt_dir: Path, rid: str) -> list[Checkpoint]:
    """The store's listing: every checkpoint of the run, by its file name."""
    return [
        Checkpoint(int(p.name.split(".")[1]), p) for p in ckpt_dir.glob(f"{rid}.*.json")
    ]


def write(path: Path, payload: dict[str, float | int]) -> None:
    """Publish ATOMICALLY: temp file, then ``os.replace`` into position.

    ``write_text`` truncates before it fills, so mid-write the file exists and is
    EMPTY: a worker that dies there leaves a checkpoint the resume cannot parse.
    ``os.replace`` is atomic within a directory: readers see a whole file or none.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload))
    os.replace(tmp, path)


def run_id(inputs: dict[str, float]) -> str:
    canon = json.dumps(inputs, sort_keys=True, allow_nan=False)
    return hashlib.sha256(canon.encode()).hexdigest()[:16]


def train(
    channel: runstate.Channel,
    *,
    until: runstate.Target,
    run_id: str,
    ckpt_dir: str,
    lr: float,
) -> None:
    """Resumable cell: continue the loss curve from the chosen checkpoint.

    ``until`` is the drive's target, which ``launch_producer`` passes in; the
    position is the log's, so the loop needs no start index. The state is the
    loss the next iteration emits; a checkpoint holds it, and names its node."""
    store = Path(ckpt_dir)
    chosen = runstate.OnHeadLineage().choose(
        channel, saved(store, run_id), head=runstate.LatestClaimHead()
    )
    state = {"loss": 5.0}

    def save(node: int) -> None:  # called by the Worker, right after `node` lands
        write(store / f"{run_id}.{node}.json", {"node": node, "loss": state["loss"]})

    with runstate.Worker(
        channel, resume_from=chosen, sources=[], save=runstate.Every(1, save)
    ) as w:
        if chosen is not None:  # load AFTER claiming, then check it is the node claimed
            loaded = chosen.load()
            assert loaded["node"] == w.resume_node
            state["loss"] = float(loaded["loss"])
        for _ in w.steps(until):
            w.emit("loss", state["loss"])  # emit before the commit: this node's value
            state["loss"] *= math.exp(-lr)
    # No completed claim: reaching the drive's target is a pause on the extend
    # axis, so leaving the block writes a preempted exit the next drive extends.


def producer_for(
    launcher: runstate.ThreadLauncher, ckpt_dir: str, *, lr: float
) -> runstate.Producer:
    rid = run_id({"lr": lr})  # excludes the target: the extend axis
    target = runstate.ThreadTarget(
        train, args=(), kwargs={"run_id": rid, "ckpt_dir": ckpt_dir, "lr": lr}
    )
    return runstate.launch_producer(launcher, rid, target)


def losses(p: runstate.Producer, n: int) -> list[runstate.Element]:
    """The first ``n`` losses, produced if the log lacks them, read at the node
    ``ensure`` names."""
    r = runstate.ensure(p, runstate.Target("loss", n))
    if not isinstance(r, runstate.Reached) or r.node is None:
        raise SystemExit(f"loss[:{n}] is out of reach: {r}")
    return runstate.series(p.channel, "loss", head=runstate.AtNode(r.node))


def main() -> None:
    launcher = runstate.ThreadLauncher()
    with tempfile.TemporaryDirectory() as ckpt_dir:
        p = producer_for(launcher, ckpt_dir, lr=0.3)

        s = losses(p, 8)
        print(f"cold:   asked 8, got {len(s)}; loss[0]={s[0].value:.3f}")

        s = losses(p, 8)  # hit -- no relaunch
        print(f"warm:   asked 8, got {len(s)} (served from the log)")

        s = losses(p, 20)  # extend: resume from the checkpoint at the last exit
        print(
            f"extend: asked 20, got {len(s)}; one lineage, loss[19]={s[-1].value:.3f}"
        )


if __name__ == "__main__":
    main()
