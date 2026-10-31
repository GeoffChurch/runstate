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
   demand-sampled ``job_seconds`` source every 10 commits, and streams the
   eager ``energy`` series live, through held folds.
4. When the energy stream plateaus, sends a cooperative ``control.stop`` --
   the worker's next heartbeat drains it mid-queue-wait, cancels the
   in-flight job instead of burning the remaining budget, and exits
   ``preempted``. Whether "plateaued" counts as success is deliberately the
   driver's policy, not the worker's claim -- the same consumer-owned line as
   RunResult's missing ``success`` bool.

Run it twice: the second invocation is a cache hit. Kill the worker mid-run
and re-run: it resumes from the checkpoint. VQE_BACKEND=simulator (or
qpu.aria-1, ...) switches to IonQ Cloud via qiskit-ionq ($IONQ_API_KEY);
see vqe.py for all VQE_* knobs.
"""

import math
import os
import sys
import tempfile
import uuid
from pathlib import Path

import runstate
import vqe

WORKER = Path(__file__).parent / "worker.py"
PLATEAU_STEPS = int(os.environ.get("VQE_PLATEAU_STEPS", "25"))
PLATEAU_TOL = float(os.environ.get("VQE_PLATEAU_TOL", "0.02"))


def plateaued(energies: list[float]) -> bool:
    """The driver's convergence policy: the last PLATEAU_STEPS iterations
    produced no new best (beyond PLATEAU_TOL). ``energies`` is the ``energy``
    series in lineage order, one element per iteration."""
    if len(energies) <= PLATEAU_STEPS:
        return False
    return min(energies[-PLATEAU_STEPS:]) > min(energies[:-PLATEAU_STEPS]) - PLATEAU_TOL


def main() -> None:
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
        head = runstate.LatestClaimHead()
        energies = [e.value for e in runstate.series(ch, "energy", head=head)]
        satisfied = plateaued(energies) or len(energies) >= cfg["budget"]
        if runstate.peek_terminal(ch) is not None and satisfied:
            best = min(energies)
            print(
                f"[driver] cache hit -- {len(energies)} iterations already on the log, "
                f"best {best:+.4f}; no jobs submitted"
            )
            return

        # Demand-sampled source: ask for the job wall time every 10 commits.
        sub_seq = ch.send(
            {"every": {"commits": 10}},
            topic=runstate.Topic.CONTROL_SUBSCRIBE,
            name="job_seconds",
            request_id="driver",
        )

        # heartbeat_timeout arms the staleness tier; safe even under a long
        # queue wait because the worker heartbeats every VQE_POLL_SECONDS while
        # it polls -- keep the timeout comfortably above that cadence.
        watcher = runstate.Watcher(poll_interval=0.1, heartbeat_timeout=60.0)
        cmd = runstate.Command([sys.executable, str(WORKER)], env={})
        handle = runstate.relaunch_if_needed(launcher, rid, cmd)
        if handle is not None:
            resumed = f" (resuming past {len(energies)} iterations)" if energies else ""
            print(f"[driver] launched {handle.handle}{resumed}")
            watcher.add(handle)
        else:
            print("[driver] a held claim already serves this run -- observing it")
            watcher.observe(rid, ch)

        # A watcher holding the launch's handle reads that launch's verdict, so a
        # relaunch never reads the previous claim's exit while the fresh worker is
        # still importing qiskit. await_consumed is the blessed "did my command
        # land?" read: it resolves once a living worker's watermark passes the
        # subscribe (accepted -> None), or hands back a nak / a terminal that
        # followed the request (refused-by-death).
        answer = runstate.await_consumed(ch, sub_seq, timeout=120.0)
        if answer is not None:
            raise SystemExit(f"[driver] subscribe was not serviced: {answer}")

        # Held folds, polled at each node: each reads only the records since its
        # last poll. The worker emits one energy per node, so the newest element
        # is the iteration's; the answers to "driver" are the job_seconds samples
        # at the nodes it fired, and the fold returns them all, so diff by seq.
        # The first poll catches up on what the log already held: fold it silently.
        heads = runstate.HeadFold()
        last_energy = runstate.LastElement("energy")
        job_seconds = runstate.Answers("driver")
        h0 = heads.poll(ch)
        caught_up = runstate.series(ch, "energy", head=h0)
        energies = [e.value for e in caught_up]
        seen = {e.seq for e in caught_up} | {a.seq for a in job_seconds.poll(ch, h0)}
        stop_sent = False

        def on_event(_rid: str, e: runstate.Envelope) -> None:
            nonlocal stop_sent
            if e.topic not in (
                runstate.Topic.LIFECYCLE_COMMIT,
                runstate.Topic.LIFECYCLE_STOPPED,
            ):
                return
            h = heads.poll(ch)
            last = last_energy.poll(ch, h)
            if last is not None and last.seq not in seen:
                seen.add(last.seq)
                energies.append(last.value)
                print(
                    f"[driver] iteration {len(energies):>3} energy {last.value:+.4f} "
                    f"(best {min(energies):+.4f})"
                )
                if not stop_sent and plateaued(energies):
                    ch.send(
                        {},  # no `from`: it holds once drained
                        topic=runstate.Topic.CONTROL_STOP,
                        request_id=uuid.uuid4().hex,  # a stop id is never reused
                    )
                    stop_sent = True
                    print(
                        f"[driver] no gain > {PLATEAU_TOL} in {PLATEAU_STEPS} iterations "
                        "-> control.stop (the worker cancels any in-flight job)"
                    )
            for a in job_seconds.poll(ch, h):
                if a.seq in seen:  # answered before: printed, or caught up on
                    continue
                seen.add(a.seq)
                # A source has no value at a new position until the worker's
                # first set() -- the sample is honestly None; tolerate it.
                if a.value is not None:
                    print(f"[driver] node {a.node:>4} job wall time {a.value:.2f}s")

        result = watcher.wait(rid, on_event=on_event)
        best = min(energies, default=math.nan)
        outcomes = ", ".join(sorted(result.outcomes))
        why = "converged (plateau-stopped)" if stop_sent else outcomes
        print(
            f"[driver] terminal: outcomes={{{outcomes}}} tier={result.tier} "
            f"node={result.node} -- {why}"
        )
        print(
            f"[driver] best {best:+.4f} vs exact {exact:+.4f} "
            f"(|err| {abs(best - exact):.4f}) in {len(energies)} iterations"
        )
        print("[driver] the log is the cache: re-run me and this becomes a hit")


if __name__ == "__main__":
    main()
