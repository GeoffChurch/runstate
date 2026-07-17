"""Integration test: autonomous-extend — relaunch a run_id with a higher target.

The worker resumes from a checkpoint and continues from the run-absolute step
where it left off. The log should read back as one continuous series with no
gaps or duplicates, and the final peek_terminal should be "preempted" —
an autonomous-extend run is resumable-by-design, never self-completed.
"""

import json
import math

import runstate


def _cell(channel, *, run_id, target, ckpt_dir):
    from pathlib import Path

    ckpt = Path(ckpt_dir) / f"{run_id}.json"
    start = json.loads(ckpt.read_text())["next_step"] if ckpt.exists() else 0
    with runstate.Worker(channel, now=lambda: 0.0) as w:
        for step in w.steps(start=start, total=target):
            w.set("loss", 5.0 * math.exp(-0.1 * step))
    ckpt.write_text(json.dumps({"next_step": target}))


def test_relaunch_extends_one_series(tmp_path):
    launcher = runstate.ThreadLauncher()  # memory backend, in-process
    rid = "exp"

    # Pre-stage the loss subscription before episode 1. The subscribe lives at a
    # low seq on the shared log; both episodes' workers start with _cursor=0 and
    # will drain it on their first tick, re-registering a fresh Subscription.
    ch0 = launcher.open_channel(rid)
    ch0.send(
        {"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="obs"
    )

    # episode 1: run to step 5
    launcher.launch(
        rid, _cell, kwargs={"run_id": rid, "target": 5, "ckpt_dir": str(tmp_path)}
    ).wait()

    # episode 2: extend to step 10 (resumes from the checkpoint at step 5)
    launcher.launch(
        rid, _cell, kwargs={"run_id": rid, "target": 10, "ckpt_dir": str(tmp_path)}
    ).wait()

    ch = launcher.open_channel(rid)
    steps = [v.body["step"] for v in ch.read(topics=["value"])]
    assert steps == list(
        range(10)
    )  #        one continuous run-absolute series, no gaps/dups
    assert runstate.peek_terminal(ch).outcome == "preempted"
