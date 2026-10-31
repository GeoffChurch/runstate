"""VQE worker: SPSA over quantum jobs, driven by runstate's reference Worker.

The runstate-specific pattern is the wait loop (``collect``): a queued QPU job
can sit for minutes, so the worker keeps calling ``w.heartbeat()`` while it
polls -- the liveness record stays fresh (no dead-vs-busy false positive at the
Watcher) and a cooperative ``control.stop`` lands MID-WAIT, in which case the
in-flight job is canceled before the remaining shot budget burns. A heartbeat
writes no node, so waiting moves no count and fires no subscription. The local
backend defers each result by ``VQE_LOCAL_LATENCY`` (vqe.py), so this loop --
heartbeats, mid-job stop, cancel -- actually executes on the credential-free
default path, not only against a real queue.

Reporting uses both kinds of name: ``emit("energy", ...)`` logs the canonical
series unconditionally (worker-chosen cadence -- what ``series`` / ``history``
replay), ``set("job_seconds", ...)`` updates a declared source, which reaches
the log only when someone's subscription fires (observer-chosen cadence).
Both are written after the wait, so a stop mid-wait leaves no value pending:
the exit is not torn. A variant that emits before the wait calls
``w.discard()`` on the way out instead.

Resumable by the checkpoint recipe (commits-and-lineage.md §6): the Worker saves
right after each node lands, when ``params`` is exactly the state the node
commits; a resume chooses the newest checkpoint on the head's lineage, claims
from it, loads it, and asserts the two agree. The position is the log's, so
``steps()`` needs no start index. Budget exhaustion deliberately claims nothing:
the run_id excludes the budget (the extend axis), so "did what was asked" is a
resumable pause -- ``preempted`` -- not intrinsic completion
(docs/specs/preempted-vs-completed.md).

Launched by driver.py; runnable by hand too (``current_channel()`` reads RUNSTATE_*).
"""

import json
import math
import os
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import runstate
import vqe

# SPSA gain schedules (Spall's standard exponents; A/C tuned on this landscape).
A_GAIN, C_GAIN, STABILITY, ALPHA, GAMMA = 2.0, 0.2, 8.0, 0.602, 0.101


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

    ``write_text`` truncates before it fills, so mid-write the file exists and is EMPTY.
    This worker can be preempted between QPU polls, and losing the params to a torn
    write costs the whole optimisation run, not just one iteration. ``os.replace`` is
    atomic within a directory: a resume reads a whole file or none.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload))
    os.replace(tmp, path)


def perturbation(seed: int, k: int, n: int) -> list[float]:
    """The iteration's Rademacher direction, deterministic in (seed, k) so a
    resumed claim replays the same direction sequence."""
    rng = random.Random(f"{seed}:{k}")
    return [rng.choice((-1.0, 1.0)) for _ in range(n)]


def collect(
    jobs: vqe.LocalJobs | vqe.IonQJobs,
    handle: Any,
    w: runstate.Worker,
    step: int,
    poll_seconds: float,
) -> list[dict[str, int]] | None:
    """Poll the job to terminal, heartbeating between polls. Returns the
    per-circuit counts, or None if a commanded stop arrived mid-wait (the job
    is canceled -- stopping early is the point of cooperative control when
    every shot costs money)."""
    while True:
        status = jobs.poll(handle)
        if status == "DONE":
            return jobs.counts(handle)
        if status is not None:
            # The job died on the service side: self-diagnose fatal, so the
            # exit carries the error (outcome -> errored, no retry).
            raise RuntimeError(f"job finished {status}, not DONE")
        if w.heartbeat():  # drains control; True once a stop holds
            print(
                f"[worker] stop landed mid-job at step {step} -> canceling", flush=True
            )
            try:
                jobs.cancel(handle)
            # the cancel-vs-complete race: the job may just have finished
            except Exception as exc:
                print(f"[worker] cancel raced completion: {exc}", flush=True)
            return None
        time.sleep(poll_seconds)


def main() -> None:
    cfg = vqe.config()
    jobs = vqe.jobs_backend(cfg)
    ch = runstate.current_channel()
    # The checkpoints live next to the channel, keyed by the run and the node.
    store = Path(os.environ["RUNSTATE_CHANNEL_ROOT"])
    rid = os.environ["RUNSTATE_RUN_ID"]
    candidates = [
        Checkpoint(int(p.name.split(".")[1]), p)
        for p in store.glob(f"{rid}.*.ckpt.json")
    ]
    # Choose before claiming: the newest checkpoint on the head's lineage.
    chosen = runstate.OnHeadLineage().choose(
        ch, candidates, head=runstate.LatestClaimHead()
    )
    rng = random.Random(cfg["seed"])
    params = [rng.uniform(-math.pi, math.pi) for _ in range(2)]  # a fresh start's

    def save(node: int) -> None:  # the Worker calls it right after `node` lands
        write(store / f"{rid}.{node}.ckpt.json", {"node": node, "params": params})

    with runstate.Worker(
        ch,
        resume_from=chosen,
        sources=["job_seconds"],
        save=runstate.Every(1, save),
    ) as w:
        if chosen is not None:  # load after claiming; assert it is the node claimed
            loaded = chosen.load()
            assert loaded["node"] == w.resume_node
            params = loaded["params"]
            print(f"[worker] resuming at node {w.resume_node}", flush=True)
        for step in w.steps(runstate.Target(runstate.Commits(), cfg["budget"])):
            a_k = A_GAIN / (step + 1 + STABILITY) ** ALPHA
            c_k = C_GAIN / (step + 1) ** GAMMA
            delta = perturbation(cfg["seed"], step, len(params))
            plus = [t + c_k * d for t, d in zip(params, delta)]
            minus = [t - c_k * d for t, d in zip(params, delta)]
            began = time.monotonic()
            handle = jobs.submit(vqe.circuits(plus) + vqe.circuits(minus))
            counts = collect(jobs, handle, w, step, cfg["poll_seconds"])
            if counts is None:
                break  # commanded stop; leaving the block writes the preempted exit
            e_plus = vqe.energy(counts[0], counts[1], j=cfg["j"], h=cfg["h"])
            e_minus = vqe.energy(counts[2], counts[3], j=cfg["j"], h=cfg["h"])
            grad = (e_plus - e_minus) / (2.0 * c_k)
            params = [t - a_k * grad * d for t, d in zip(params, delta)]
            # Emit before the commit, so the values land in this iteration's node.
            w.emit("energy", float((e_plus + e_minus) / 2.0))  # the cached series
            w.set("job_seconds", time.monotonic() - began)  # sampled on demand
        # No completed claim: budget-done is a pause on the extend axis (see
        # module docstring); leaving the block writes the preempted exit.


if __name__ == "__main__":
    main()
