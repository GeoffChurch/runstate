"""VQE driver: steer a real experiment, then reuse its log.

The quantum-workload example: a step that is long, expensive, and CANCELABLE
(a queued QPU job), where the existing examples' steps are fast and free.
What it does:

1. Names the run by content -- ``vqe.run_id(config)`` -- so the same
   experiment always meets the same log (log-as-cache).
2. If the log already satisfies the policy (plateaued, or the budget's worth
   of iterations), serves the answer FROM THE LOG: zero jobs submitted.
3. Else launches the worker (``relaunch_if_needed``: no double-spawn; a
   killed or short run RESUMES from its checkpoint), subscribes to the
   demand-sampled ``job_seconds`` register every 10 steps, and streams the
   broadcast ``energy`` series live.
4. When the energy stream plateaus, sends a cooperative ``control.stop`` --
   the worker cancels its in-flight job mid-queue-wait instead of burning
   the remaining budget, and exits ``preempted``. Whether "plateaued" counts
   as success is deliberately the driver's policy, not the worker's claim --
   the same consumer-owned line as RunResult's missing ``success`` bool.

Run it twice: the second invocation is a cache hit. Kill the worker mid-run
and re-run: it resumes from the checkpoint. VQE_BACKEND=simulator (or
qpu.aria-1, ...) switches to IonQ Cloud via qiskit-ionq ($IONQ_API_KEY);
see vqe.py for all VQE_* knobs.
"""

import math
import os
import sys
import tempfile
from pathlib import Path

import runstate
import vqe

WORKER = Path(__file__).parent / "worker.py"
PLATEAU_STEPS = int(os.environ.get("VQE_PLATEAU_STEPS", "25"))
PLATEAU_TOL = float(os.environ.get("VQE_PLATEAU_TOL", "0.02"))


def plateaued(energies):
    """The driver's convergence policy: the last PLATEAU_STEPS iterations
    produced no new best (beyond PLATEAU_TOL). ``energies`` is the
    ``value_series`` projection {step: value}."""
    if len(energies) <= PLATEAU_STEPS:
        return False
    vals = [energies[s] for s in sorted(energies)]
    return min(vals[-PLATEAU_STEPS:]) > min(vals[:-PLATEAU_STEPS]) - PLATEAU_TOL


def main():
    cfg = vqe.config()
    rid = vqe.run_id(cfg)
    root = Path(
        os.environ.get("VQE_ROOT", Path(tempfile.gettempdir()) / "runstate-vqe")
    )
    root.mkdir(parents=True, exist_ok=True)
    exact = vqe.exact_ground(cfg["j"], cfg["h"])
    print(f"[driver] {rid} backend={cfg['backend']} budget={cfg['budget']} root={root}")
    print(f"[driver] exact ground energy {exact:.4f}")

    with runstate.LocalLauncher(root=root) as launcher:
        ch = launcher.create_channel(rid)
        series = runstate.value_series(ch)
        energies = dict(series.get("energy", {}))
        # Replay guard for the register too: on resume, the worker's first
        # drain replays the historical job_seconds points -- fold them
        # silently, exactly like the energy branch below.
        job_steps_seen = set(series.get("job_seconds", {}))
        satisfied = plateaued(energies) or len(energies) >= cfg["budget"]
        if runstate.peek_terminal(ch) is not None and satisfied:
            best = min(energies.values())
            print(
                f"[driver] cache hit -- {len(energies)} iterations already on the log, "
                f"best {best:+.4f}; no jobs submitted"
            )
            return

        # Demand-sampled register: ask for the job wall time every 10 steps.
        sub_seq = ch.send(
            {"every": {"step": 10}},
            topic=runstate.Topic.CONTROL_SUBSCRIBE,
            name="job_seconds",
            request_id="driver",
        )

        # heartbeat_timeout arms the staleness tier; safe even under a long
        # queue wait because the worker ticks (and beacons) every
        # VQE_POLL_SECONDS while it polls -- keep the timeout comfortably
        # above that cadence.
        watcher = runstate.Watcher(poll_interval=0.1, heartbeat_timeout=60.0)
        handle = runstate.relaunch_if_needed(
            launcher, rid, [sys.executable, str(WORKER)]
        )
        if handle is not None:
            resumed = f" (resuming past step {max(energies)})" if energies else ""
            print(f"[driver] launched {handle.handle}{resumed}")
            watcher.add(handle)
        else:
            print("[driver] a live episode already serves this run -- observing it")
            watcher.observe(rid, ch)

        # The episode rule: a dead run's verdict STANDS until a new episode
        # claims -- so on a relaunch, watching immediately would hand back the
        # stale verdict while the fresh worker is still importing qiskit.
        # await_consumed is the blessed "did my command land?" read: it
        # resolves once a LIVING worker's watermark passes the subscribe
        # (accepted -> None), or hands back a nak / a terminal that followed
        # the request (refused-by-death).
        answer = runstate.await_consumed(
            ch, sub_seq, request_id="driver", timeout=120.0
        )
        if answer is not None:
            raise SystemExit(f"[driver] subscribe was not serviced: {answer}")

        state = {"stop_sent": False}

        def on_event(_rid, e):
            if e.topic != runstate.Topic.VALUE:
                return
            step, val = e.body.get("step"), e.body.get("value")
            if e.name == "energy":
                if step in energies:  # the first drain replays history: fold silently
                    energies[step] = val
                    return
                energies[step] = val
                print(
                    f"[driver] step {step:>3} energy {val:+.4f} (best {min(energies.values()):+.4f})"
                )
                if not state["stop_sent"] and plateaued(energies):
                    ch.send(
                        {}, topic=runstate.Topic.CONTROL_STOP, request_id="driver-stop"
                    )  # {} = now
                    state["stop_sent"] = True
                    print(
                        f"[driver] no gain > {PLATEAU_TOL} in {PLATEAU_STEPS} steps "
                        "-> control.stop (the worker cancels any in-flight job)"
                    )
            elif e.name == "job_seconds" and e.request_id == "driver":
                if step in job_steps_seen:  # replayed history: fold silently
                    return
                job_steps_seen.add(step)
                # A demand-sampled register can fire before the worker's first
                # set() (the first mid-wait tick of an episode) -- the sample
                # is honestly None; tolerate it.
                if val is not None:
                    print(f"[driver] step {step:>3} job wall time {val:.2f}s")

        result = watcher.wait(rid, on_event=on_event)
        best = min(energies.values(), default=math.nan)
        why = (
            "converged (plateau-stopped)" if state["stop_sent"] else str(result.outcome)
        )
        print(
            f"[driver] terminal: outcome={result.outcome} final_step={result.final_step} -- {why}"
        )
        print(
            f"[driver] best {best:+.4f} vs exact {exact:+.4f} "
            f"(|err| {abs(best - exact):.4f}) in {len(energies)} iterations"
        )
        print("[driver] the log is the cache: re-run me and this becomes a hit")


if __name__ == "__main__":
    main()
