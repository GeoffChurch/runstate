"""Minimal worker (log format 0.4.0).

A fake training loop driven by runstate's reference Worker. It attaches to the
run it was launched into, exposes a current ``loss`` that the orchestrator may
sample, and stops cooperatively when asked -- the Worker drains control, answers
subscriptions, and writes the lifecycle.* records for us.

Driven by examples/minimal/driver.py.
"""

import math

import runstate


def main() -> None:
    channel = runstate.current_channel()  # reads RUNSTATE_* set by the launcher
    # The Worker's three choices are explicit, so no reader re-derives them:
    # - resume_from=None: a fresh start (this example never resumes);
    # - sources=["loss"]: loss is a register, written by set() and sampled only
    #   when a subscription fires; every other name would be an eager stream;
    # - save=NoSave(): no checkpoints (see examples/reuse for the recipe).
    with runstate.Worker(
        channel, resume_from=None, sources=["loss"], save=runstate.NoSave()
    ) as w:
        # steps() yields each iteration's index, the count of commits before it,
        # and commits after the body; it ends at 50 commits or on a stop.
        for step in w.steps(runstate.Target(runstate.Commits(), 50)):
            # "training step" -- just simulate a decaying loss.
            loss = max(0.01, 5.0 * (0.97**step) + math.sin(step * 0.2) * 0.1)
            w.set("loss", loss)
        if not w.stop_pending:  # ran the whole budget, rather than being stopped
            w.stopped(completed=True, error=None)
    # A commanded stop ends steps() early; leaving the `with` then writes the
    # preempted exit, which honors the stop -- no explicit call needed.


if __name__ == "__main__":
    main()
