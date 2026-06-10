"""Drive the on-demand monitor (docs/specs/service-worker.md).

The service-half lifecycle, end to end:

1. PRE-STAGE demand: a keepalive-leased subscription lands on the durable
   channel before any worker exists (the log buffers demand).
2. Launch the service; its first tick drains the subscribe that justified the
   launch (register-before-reap by loop order), and it serves while pinned.
3. Read the values as a register: ``latest("value", name)`` — a stepless
   service's emissions are deliberately outside ``value_series``'s
   step-indexed domain.
4. Let the lease lapse: the worker writes the expiry counter-record
   (``control.unsubscribe``, the worker completing the pair) and retires via
   the careful death. ``live_demand`` and ``peek_terminal`` read the whole
   story back off the log.
"""

import sys
import time
from pathlib import Path

from runstate import LocalLauncher, live_demand, open_channel, peek_terminal

RUN = "monitor"
ROOT = Path(__file__).parent / ".runs"
LEASE = 2.0          # seconds: the keepalive lease each subscribe grants
SERVICE = Path(__file__).parent / "service.py"


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    ch = open_channel(RUN, root=ROOT)

    # 1. demand first (pre-staged: the channel is durable and addressable
    #    before any worker exists), with a time-bounded keepalive lease.
    seq = ch.send({"every": {"time_seconds": 0.5}, "until": {"time_seconds": LEASE}},
                  topic="control.subscribe", name="load1", request_id="dash-1")
    print(f"[driver] demand pre-staged (seq {seq}); live_demand:",
          [e.request_id for e in live_demand(ch)])

    # 2. launch the service into that demand.
    launcher = LocalLauncher(root=ROOT)
    with launcher:
        handle = launcher.launch(RUN, [sys.executable, str(SERVICE)])
        deadline = time.time() + LEASE * 4
        while time.time() < deadline and peek_terminal(ch) is None:
            e = ch.latest("value", "load1")
            if e is not None:
                print(f"[driver] load1 = {e.body['value']:.2f}")
            time.sleep(0.4)
            # 3. no refresh sent: the lease lapses and the service retires.
        handle.wait(timeout=10)

    # 4. the log tells the whole story.
    expiry = ch.read(topics=["control.unsubscribe"])
    print(f"[driver] expiry records: {[u.request_id for u in expiry]}")
    print(f"[driver] live demand now: {live_demand(ch)}")
    print(f"[driver] terminal: {peek_terminal(ch)}")


if __name__ == "__main__":
    main()
