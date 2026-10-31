"""Minimal driver (log format 0.4.0).

Launches the worker as a subprocess via LocalLauncher, subscribes to its ``loss``
at every commit, streams the answers as they arrive, and would send a cooperative
stop if loss diverged (it doesn't, in this example). Then it waits for the
terminal RunResult.

runstate ships NO Orchestrator class -- this is application code composing the
substrate (create_channel / control.* sends) with the reference orchestration
helpers (LocalLauncher, Watcher, the held folds). Adapt it freely.
"""

import sys
import tempfile
import uuid
from pathlib import Path

import runstate


def main() -> None:
    root = tempfile.mkdtemp(prefix="runstate-minimal-")
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    worker = Path(__file__).parent / "worker.py"
    print(f"[driver] run_id={run_id} root={root}")

    with runstate.LocalLauncher(root=root) as launcher:
        # Subscribe BEFORE launch so the worker picks it up on its first drain:
        # sample `loss` at every commit, its answers named by request_id "driver".
        ch = launcher.create_channel(run_id)
        ch.send(
            {"every": {"commits": 1}},
            topic=runstate.Topic.CONTROL_SUBSCRIBE,
            name="loss",
            request_id="driver",
        )

        # A launch takes its launcher's target record: env={} is "the parent's
        # environment", said explicitly.
        cmd = runstate.Command([sys.executable, str(worker)], env={})
        handle = launcher.launch(run_id, cmd)
        print(f"[driver] launched {handle.handle}")

        # heartbeat_timeout is the per-workload staleness threshold: required,
        # since it changes a verdict. This worker commits every few ms.
        watcher = runstate.Watcher(heartbeat_timeout=30.0, poll_interval=0.02)
        watcher.add(handle)

        # The answers to "driver" are the samples at the nodes whose `fired`
        # names it: a held fold reads only the records since its last poll.
        # It returns the whole list along the head, so print what is new.
        heads, answers = runstate.HeadFold(), runstate.Answers("driver")
        printed: set[int] = set()
        stop_sent = False

        def on_event(rid: str, e: runstate.Envelope) -> None:
            nonlocal stop_sent
            if e.topic not in (
                runstate.Topic.LIFECYCLE_COMMIT,
                runstate.Topic.LIFECYCLE_STOPPED,
            ):
                return  # answers land with nodes: poll the fold only then
            for a in answers.poll(ch, heads.poll(ch)):
                if a.seq in printed:
                    continue
                printed.add(a.seq)
                print(f"[driver] node {a.node:>3} loss {a.value:.4f}")
                if a.value > 100 and not stop_sent:  # divergence preempt
                    ch.send(
                        {},  # no `from`: it holds as soon as the worker drains it
                        # the wire is just strings -- Topic.CONTROL_STOP and "control.stop" are interchangeable
                        topic="control.stop",
                        request_id=uuid.uuid4().hex,  # a stop id is never reused
                    )
                    stop_sent = True
                    print("[driver] divergence detected -> sent control.stop")

        # Block until the run is terminal, streaming records to on_event meanwhile.
        result = watcher.wait(run_id, on_event=on_event)
        # outcomes is a set (one element under the worker's own exit); tier names
        # the viewpoint the verdict rests on; node is the exit node.
        outcomes = ", ".join(sorted(result.outcomes))
        print(
            f"[driver] terminal: outcomes={{{outcomes}}} tier={result.tier} "
            f"node={result.node} ({len(printed)} answers)"
        )


if __name__ == "__main__":
    main()
