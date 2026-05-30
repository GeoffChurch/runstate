"""Minimal driver example.

Launches the worker via plain subprocess.Popen, opens a runstate Channel
to observe it, prints progress, and sends StopNow if loss diverges.

Note: runstate does NOT ship an Orchestrator class. This driver script
is application code that uses the protocol. You can adapt it freely.
"""

import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import runstate
from runstate import control, events


def main():
    # Each run gets its own run_id + run_dir. The Channel is at <root>/<run_id>/.
    root = tempfile.mkdtemp(prefix="runstate-minimal-")
    run_id = f"run-{uuid.uuid4().hex[:8]}"

    print(f"[driver] root={root}")
    print(f"[driver] run_id={run_id}")

    # Spawn the worker. RUNSTATE_* env vars tell it where the Channel lives.
    worker_path = Path(__file__).parent / "worker.py"
    env = {
        **os.environ,
        "RUNSTATE_RUN_ID": run_id,
        "RUNSTATE_CHANNEL_ROOT": root,
        "RUNSTATE_CHANNEL_BACKEND": "sqlite",  # sqlite preserves history
    }
    proc = subprocess.Popen([sys.executable, str(worker_path)], env=env)
    print(f"[driver] launched worker pid={proc.pid}")

    # Open the orchestrator-role Channel to observe.
    ch = runstate.open_channel(run_id, role="orchestrator", root=root, backend="sqlite")

    # Track our side of the protocol (what we sent, what got ack'd).
    commands_sent: list[str] = []  # command_ids
    commands_acked: set[str] = set()
    stop_sent = False

    def handle_message(msg: dict) -> None:
        nonlocal stop_sent
        event = events.parse(msg)
        match event:
            case events.Progress(step=s, metrics=m):
                loss = m.get("loss", float("nan"))
                print(f"[driver] step {s} loss {loss:.4f}")
                # Divergence preempt: if loss spikes, stop.
                if loss > 100 and not stop_sent:
                    cmd_id = control.send_stop(ch)
                    commands_sent.append(cmd_id)
                    stop_sent = True
                    print(f"[driver] divergence detected; sent StopNow id={cmd_id}")

            case events.Stopped(reason=r, metadata=md):
                print(f"[driver] worker reported Stopped: reason={r!r} metadata={md}")

            case events.Ack(of=of, command_id=cid):
                commands_acked.add(cid)
                print(f"[driver] ack received for {of} id={cid}")

            case None:
                # Non-protocol dict; print verbatim.
                print(f"[driver] non-protocol message: {msg}")

    # Loop: read worker messages until the worker exits.
    while proc.poll() is None:
        msg = ch.recv(timeout=0.5)
        if msg is not None:
            handle_message(msg)

    # Worker exited. Drain any final messages still in the channel
    # (e.g., the Stopped event sent right before exit).
    while True:
        msg = ch.recv(timeout=0)
        if msg is None:
            break
        handle_message(msg)

    exit_code = proc.wait()
    ch.close()

    print(f"[driver] worker exited with code {exit_code}")
    print(f"[driver] commands sent: {len(commands_sent)}, acked: {len(commands_acked)}")
    unacked = [c for c in commands_sent if c not in commands_acked]
    if unacked:
        print(f"[driver] unacknowledged: {unacked}")


if __name__ == "__main__":
    main()
