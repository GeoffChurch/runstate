"""Minimal driver (v0.2).

Launches the worker as a subprocess via LocalLauncher, subscribes to its ``loss``
each step, streams the value events as they arrive, and would send a cooperative
stop if loss diverged (it doesn't, in this example). Then it waits for the
terminal RunResult.

runstate ships NO Orchestrator class -- this is application code composing the
substrate (open_channel / control.* sends) with the reference orchestration
helpers (LocalLauncher, Watcher). Adapt it freely.
"""

import sys
import tempfile
import uuid
from pathlib import Path

import runstate


def main():
    root = tempfile.mkdtemp(prefix="runstate-minimal-")
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    worker = Path(__file__).parent / "worker.py"
    print(f"[driver] run_id={run_id} root={root}")

    with runstate.LocalLauncher(root=root) as launcher:
        # Subscribe BEFORE launch so the worker picks it up on its first tick:
        # report `loss` every step, correlated by request_id "driver".
        ch = launcher.open_channel(run_id)
        ch.send(
            {"every": {"step": 1}},
            topic="control.subscribe",
            name="loss",
            request_id="driver",
        )

        handle = launcher.launch(run_id, [sys.executable, str(worker)])
        print(f"[driver] launched {handle.handle}")

        watcher = runstate.Watcher(poll_interval=0.02)
        watcher.add(handle)

        stop_sent = False

        def on_event(rid, e):
            nonlocal stop_sent
            if e.topic == "value" and e.request_id == "driver":
                loss = e.body["value"]
                print(f"[driver] step {e.body['step']:>2} loss {loss:.4f}")
                if loss > 100 and not stop_sent:  # divergence preempt
                    ch.send(
                        {"from": {"step": 0}},
                        topic="control.stop",
                        request_id="driver-stop",
                    )
                    stop_sent = True
                    print("[driver] divergence detected -> sent control.stop")

        # Block until the run is terminal, streaming events to on_event meanwhile.
        result = watcher.wait(run_id, on_event=on_event)
        print(
            f"[driver] terminal: outcome={result.outcome} reason={result.reason!r} "
            f"final_step={result.final_step}"
        )


if __name__ == "__main__":
    main()
