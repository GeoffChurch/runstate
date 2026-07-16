"""VQE worker: SPSA over quantum jobs, driven by runstate's reference Worker.

The runstate-specific pattern is the wait loop (``collect``): a queued QPU job
can sit for minutes, so the worker keeps calling ``w.tick(step)`` while it
polls -- the heartbeat stays fresh (no dead-vs-busy false positive at the
Watcher) and a cooperative ``control.stop`` lands MID-WAIT, in which case the
in-flight job is canceled before the remaining shot budget burns. Re-ticking
at one step is safe: a step-keyed subscription fires on step deltas, not per
tick.

Reporting uses both personas: ``emit("energy", ...)`` logs the canonical
series unconditionally (worker-chosen cadence -- what ``value_series`` /
``history`` replay), ``set("job_seconds", ...)`` updates a register that only
reaches the log when someone subscribed (observer-chosen cadence).

Resumable by checkpoint: the checkpoint records the FRONTIER (the iteration
done, and the params to continue from), never the target, and
``steps(start=...)`` keeps resumed steps run-absolute (the reuse example's
lesson). Budget exhaustion deliberately claims nothing: the run_id excludes
the budget (the extend axis), so "did what was asked" is a resumable pause --
``preempted`` -- not intrinsic completion (docs/specs/preempted-vs-completed.md).

Launched by driver.py; runnable by hand too (``attach()`` reads RUNSTATE_*).
"""

import json
import math
import os
import random
import time
from pathlib import Path

import runstate
import vqe

POLL_SECONDS = float(os.environ.get("VQE_POLL_SECONDS", "2.0"))
# SPSA gain schedules (Spall's standard exponents; A/C tuned on this landscape).
A_GAIN, C_GAIN, STABILITY, ALPHA, GAMMA = 2.0, 0.2, 8.0, 0.602, 0.101


def perturbation(seed, k, n):
    """The iteration's Rademacher direction, deterministic in (seed, k) so a
    resumed episode replays the same direction sequence."""
    rng = random.Random(f"{seed}:{k}")
    return [rng.choice((-1.0, 1.0)) for _ in range(n)]


def collect(jobs, handle, w, step):
    """Poll the job to terminal, ticking runstate between polls. Returns the
    per-circuit counts, or None if a commanded stop arrived mid-wait (the job
    is canceled -- stopping early is the point of cooperative control when
    every shot costs money)."""
    while True:
        status = jobs.poll(handle)
        if status == "DONE":
            return jobs.counts(handle)
        if status is not None:
            # The job died on the service side: self-diagnose fatal, so the
            # dying breath carries the error (outcome -> errored, no retry).
            raise RuntimeError(f"job finished {status}, not DONE")
        if w.tick(step):
            print(
                f"[worker] stop landed mid-job at step {step} -> canceling", flush=True
            )
            try:
                jobs.cancel(handle)
            # the cancel-vs-complete race: the job may just have finished
            except Exception as exc:
                print(f"[worker] cancel raced completion: {exc}", flush=True)
            return None
        time.sleep(POLL_SECONDS)


def main():
    cfg = vqe.config()
    jobs = vqe.jobs_backend(cfg)
    # The checkpoint lives next to the channel, keyed by the run.
    ckpt = Path(os.environ["RUNSTATE_CHANNEL_ROOT"]) / (
        os.environ["RUNSTATE_RUN_ID"] + ".ckpt.json"
    )
    with runstate.Worker(runstate.attach()) as w:
        if ckpt.exists():
            state = json.loads(ckpt.read_text())
            start, params = state["next"], state["params"]
            print(f"[worker] resuming at step {start}", flush=True)
        else:
            rng = random.Random(cfg["seed"])
            start, params = 0, [rng.uniform(-math.pi, math.pi) for _ in range(2)]
        for step in w.steps(start=start, total=cfg["budget"]):
            a_k = A_GAIN / (step + 1 + STABILITY) ** ALPHA
            c_k = C_GAIN / (step + 1) ** GAMMA
            delta = perturbation(cfg["seed"], step, len(params))
            plus = [t + c_k * d for t, d in zip(params, delta)]
            minus = [t - c_k * d for t, d in zip(params, delta)]
            began = time.monotonic()
            handle = jobs.submit(vqe.circuits(plus) + vqe.circuits(minus))
            counts = collect(jobs, handle, w, step)
            if counts is None:
                break  # commanded stop; leaving the block emits preempted
            e_plus = vqe.energy(counts[0], counts[1], j=cfg["j"], h=cfg["h"])
            e_minus = vqe.energy(counts[2], counts[3], j=cfg["j"], h=cfg["h"])
            grad = (e_plus - e_minus) / (2.0 * c_k)
            params = [t - a_k * grad * d for t, d in zip(params, delta)]
            w.emit("energy", float((e_plus + e_minus) / 2.0))  # the cached series
            w.set("job_seconds", time.monotonic() - began)  # demand-sampled register
            ckpt.write_text(json.dumps({"next": step + 1, "params": params}))
        # No completed claim: budget-done is a pause on the extend axis (see
        # module docstring); leaving the block emits the preempted dying breath.


if __name__ == "__main__":
    main()
