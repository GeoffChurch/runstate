"""Minimal worker (v0.2).

A fake training loop driven by runstate's reference Worker. It attaches to the
run it was launched into, reports a current ``loss`` each step, and stops
cooperatively when the orchestrator asks -- the Worker handles draining control,
servicing subscriptions, and emitting the lifecycle.* beacons for us.

Driven by examples/minimal/driver.py.
"""

import math

import runstate


def main():
    channel = runstate.current_channel()  # reads RUNSTATE_* set by the launcher
    with runstate.Worker(channel) as w:
        for step in w.steps(total=50):
            # "training step" -- just simulate a decaying loss.
            loss = max(0.01, 5.0 * (0.97**step) + math.sin(step * 0.2) * 0.1)
            w.set("loss", loss)
        w.stopped(completed=True)  # finished the full 50-step budget -> claim completion
    # Leaving the `with` without a prior claim emits lifecycle.stopped with
    # completed=False (default preempted). A commanded or budget-preempted stop
    # lets the context-manager __exit__ handle it -- no explicit call needed.


if __name__ == "__main__":
    main()
