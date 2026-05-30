"""Minimal worker example.

Runs a fake training loop, reports progress every step, exits cooperatively
on receipt of StopNow or StopAtStep.

Driven by examples/minimal/driver.py.
"""

import math
import sys
import time

import runstate
from runstate import control, events


def main():
    ch = runstate.attach()

    print(f"[worker] attached to run {ch.run_id}", flush=True)

    state = {"step": 0, "loss": 5.0}
    max_steps = 50

    for step in range(max_steps):
        # "training step" — just simulate
        state["step"] = step
        state["loss"] = max(0.01, state["loss"] * 0.97 + math.sin(step * 0.2) * 0.1)
        time.sleep(0.05)  # fake compute

        # Report progress
        events.progress(ch, step=step, metrics={"loss": state["loss"]})

        # Cooperative safe point
        cmd = control.check(ch, current_step=step)
        match cmd:
            case control.StopNow():
                print(f"[worker] StopNow at step {step}; checkpointing and exiting", flush=True)
                events.stopped(ch, reason="preempted",
                              metadata={"step": step, "loss": state["loss"]})
                return
            case control.StopAtStep(at=at):
                print(f"[worker] StopAtStep(at={at}) fired at step {step}; exiting", flush=True)
                events.stopped(ch, reason="preempted",
                              metadata={"step": step, "loss": state["loss"]})
                return
            case None:
                pass

    print(f"[worker] reached max_steps={max_steps}; finishing naturally", flush=True)
    events.stopped(ch, reason="natural", metadata={"final_step": max_steps - 1,
                                                    "final_loss": state["loss"]})


if __name__ == "__main__":
    main()
