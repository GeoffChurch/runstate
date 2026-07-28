"""Reuse + extend via the memoizer (v0.3, docs/specs/memoizer.md).

`ensure(producer, "loss", until={"step": N})` serves the logged prefix when the
run already reached N (cache hit, no worker), else relaunches-to-extend and waits.
A content-addressed run_id is the cache key (the run_id recipe). In-process via
ThreadLauncher + the memory backend, so the whole demo is self-contained.
"""

import hashlib
import json
import math
import os
from pathlib import Path

import runstate


def checkpoint(path, payload):
    """Publish the frontier ATOMICALLY: temp file, then ``os.replace`` into position.

    ``write_text`` truncates before it fills, so mid-write the file exists and is EMPTY.
    A worker that dies there leaves a checkpoint the resume cannot parse, and a resumed
    episode spawned in that instant reads it and dies before it even attaches -- which
    surfaces as a confusing "made no progress" rather than a parse error. ``os.replace``
    is atomic within a directory: readers see the old frontier or the new one.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload))
    os.replace(tmp, path)


def run_id(inputs: dict) -> str:
    canon = json.dumps(inputs, sort_keys=True, allow_nan=False)
    return hashlib.sha256(canon.encode()).hexdigest()[:16]


def train(channel, *, run_id, up_to, ckpt_dir, lr):
    """Resumable cell: continue the run-absolute loss curve from the checkpoint.

    The checkpoint records the FRONTIER (the work actually done), never the target:
    a cooperative ``control.stop`` can cut the loop short at any step, and a
    checkpoint written after the loop as ``{"next": up_to}`` would then claim work
    that never happened — the next episode would resume past the gap, do nothing,
    and ``ensure`` would raise NoProgressError. Checkpoint what you did, not what
    you were asked to do.
    """
    ckpt = Path(ckpt_dir) / f"{run_id}.json"
    start = json.loads(ckpt.read_text())["next"] if ckpt.exists() else 0
    with runstate.Worker(channel) as w:
        for step in w.steps(start=start, total=up_to):
            w.set("loss", 5.0 * math.exp(-lr * step))
            checkpoint(ckpt, {"next": step + 1})              # this step is done


def producer_for(launcher, ckpt_dir, *, lr):
    rid = run_id({"lr": lr})                       # excludes step target: the extend axis
    launcher.create_channel(rid).send(
        {"every": {"step": 1}}, topic=runstate.Topic.CONTROL_SUBSCRIBE, name="loss", request_id="obs"
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

        s = runstate.ensure(p, "loss", until={"step": 8})
        print(f"cold:   asked 8, got {len(s)}; loss[0]={s[0]['value']:.3f}")

        s = runstate.ensure(p, "loss", until={"step": 8})   # hit -- no relaunch
        print(f"warm:   asked 8, got {len(s)} (served from the log)")

        s = runstate.ensure(p, "loss", until={"step": 20})  # extend: resume 8..19
        print(f"extend: asked 20, got {len(s)}; one series 0..{s[-1]['step']}")


if __name__ == "__main__":
    main()
