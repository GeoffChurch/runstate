"""Crash-once worker for the killed-redrive caller pattern (examples/redrive/driver.py).

Resumes from a run_id-keyed checkpoint and drives a decaying loss to ``REDRIVE_UP_TO``.
On its FIRST attempt it exits non-zero partway via ``os._exit`` -- a *recordless*
death (no clean ``lifecycle.stopped``), the kind a cluster SIGKILL / OOM produces. On
the resume it continues from the checkpoint and completes. A marker file makes the
crash fire exactly once, so the demo is deterministic.
"""

import json
import math
import os
from pathlib import Path

import runstate


def main():
    up_to = int(os.environ["REDRIVE_UP_TO"])       # the step target, plumbed by the producer
    state = Path(os.environ["REDRIVE_STATE"])      # checkpoint + crash-marker dir
    rid = os.environ["RUNSTATE_RUN_ID"]
    ckpt = state / f"{rid}.ckpt"
    crashed = state / f"{rid}.crashed"

    start = json.loads(ckpt.read_text())["next"] if ckpt.exists() else 0
    with runstate.Worker(runstate.current_channel()) as w:
        for step in w.steps(start=start, total=up_to):
            w.set("loss", 5.0 * math.exp(-0.3 * step))
            if step % 3 == 2:                       # checkpoint the frontier every 3 steps
                ckpt.write_text(json.dumps({"next": step + 1}))
            if step == 4 and not crashed.exists():  # first attempt only: crash, leaving the ckpt behind the frontier
                crashed.write_text("1")
                os._exit(1)                         # recordless death -> ERRORED with error=None
        w.stopped(completed=True)                   # reached the target -> intrinsic done


if __name__ == "__main__":
    main()
