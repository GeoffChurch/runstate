"""Reuse + extend via the memoizer (v0.3, docs/specs/memoizer.md).

`ensure(producer, "loss", up_to=N)` serves the logged prefix when the run
already reached N (cache hit, no worker), else relaunches-to-extend and waits.
A content-addressed run_id is the cache key (the run_id recipe). In-process via
ThreadLauncher + the memory backend, so the whole demo is self-contained.
"""

import hashlib
import json
import math
from pathlib import Path

import runstate


def run_id(inputs: dict) -> str:
    canon = json.dumps(inputs, sort_keys=True, allow_nan=False)
    return hashlib.sha256(canon.encode()).hexdigest()[:16]


def train(channel, *, run_id, up_to, ckpt_dir, lr):
    """Resumable cell: continue the run-absolute loss curve from the checkpoint."""
    ckpt = Path(ckpt_dir) / f"{run_id}.json"
    start = json.loads(ckpt.read_text())["next"] if ckpt.exists() else 0
    with runstate.Worker(channel) as w:
        for step in w.steps(start=start, total=up_to):
            w.set("loss", 5.0 * math.exp(-lr * step))
    ckpt.write_text(json.dumps({"next": up_to}))


def producer_for(launcher, ckpt_dir, *, lr):
    rid = run_id({"lr": lr})                       # excludes up_to: the extend axis
    launcher.open_channel(rid).send(
        {"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="obs"
    )
    variant = runstate.Variant(
        rid, train, {"kwargs": {"run_id": rid, "ckpt_dir": ckpt_dir, "lr": lr}}
    )
    return runstate.launch_producer(launcher, variant)


def main():
    import tempfile

    launcher = runstate.ThreadLauncher()
    with tempfile.TemporaryDirectory() as ckpt_dir:
        p = producer_for(launcher, ckpt_dir, lr=0.3)

        s = runstate.ensure(p, "loss", up_to=8)
        print(f"cold:   asked 8, got {len(s)}; loss[0]={s[0]['value']:.3f}")

        s = runstate.ensure(p, "loss", up_to=8)         # hit -- no relaunch
        print(f"warm:   asked 8, got {len(s)} (served from the log)")

        s = runstate.ensure(p, "loss", up_to=20)        # extend: resume 8..19
        print(f"extend: asked 20, got {len(s)}; one series 0..{s[-1]['step']}")


if __name__ == "__main__":
    main()
