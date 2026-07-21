"""Drive the on-demand monitor (docs/specs/service-worker.md +
docs/specs/lazy-launch.md).

The whole service story, end to end, twice:

1. DEMAND FIRST: a keepalive-leased subscription lands on the durable channel
   before any worker exists (the log buffers demand).
2. WAKE: ``ensure_served`` — live demand, no live episode → launch. The
   service's first tick drains the subscribe that justified the launch
   (register-before-reap by loop order) and it serves while pinned.
3. READ: ``latest("value", name)`` — a stepless service's emissions are a
   register, deliberately outside ``value_series``'s step-indexed domain.
4. LAPSE: no refresh is sent; the worker writes the expiry counter-record
   (``control.unsubscribe`` — the worker completing the pair) and retires via
   the careful death (the CAS'd dying breath).
5. RE-DEMAND, RE-WAKE: a fresh subscribe is live for the next episode (no
   boundary follows it yet — specs/time-lease-boundary.md), and the same
   ``ensure_served`` call starts episode 2 on the same run. Note: never gate
   wake logic on ``peek_terminal`` — between the launch and the child's
   claim it still shows the previous episode's terminal (the wake-gap).
"""

import sys
import tempfile
import time
from pathlib import Path

from runstate import (
    LocalLauncher,
    Topic,
    ensure_served,
    live_demand,
    create_channel,
    peek_terminal,
)

RUN = "monitor"
LEASE = 2.0          # seconds: the keepalive lease each subscribe grants
SERVICE = Path(__file__).parent / "service.py"


def demand(ch, rid):
    return ch.send({"every": {"time_seconds": 0.5}, "until": {"time_seconds": LEASE}},
                   topic=Topic.CONTROL_SUBSCRIBE, name="load1", request_id=rid)


def watch_until_idle(ch, launcher):
    """Read the register while the episode serves; return when it retires."""
    deadline = time.time() + LEASE * 5
    while time.time() < deadline:
        launcher.reap()                      # the activator discipline
        if not live_demand(ch) and peek_terminal(ch) is not None:
            return
        e = ch.latest(Topic.VALUE, "load1")
        if e is not None:
            print(f"[driver] load1 = {e.body['value']:.2f}")
        time.sleep(0.4)


def main():
    root = tempfile.mkdtemp(prefix="runstate-monitor-")   # off the repo tree (was ./.runs)
    launcher = LocalLauncher(root=root)
    with create_channel(RUN, root=root) as ch, launcher:    # the channel is a context manager
        # ----- episode 1 -----
        demand(ch, "dash-1")
        print("[driver] demand pre-staged; live:",
              [e.request_id for e in live_demand(ch)])
        assert ensure_served(launcher, RUN, [sys.executable, str(SERVICE)]) is not None
        watch_until_idle(ch, launcher)       # lease lapses -> careful death
        print(f"[driver] episode 1 over: terminal={peek_terminal(ch).outcome}, "
              f"expiry records="
              f"{[u.request_id for u in ch.read(topics=[Topic.CONTROL_UNSUBSCRIBE])]}")

        # ----- episode 2: re-demand re-wakes the SAME run -----
        demand(ch, "dash-2")
        h = ensure_served(launcher, RUN, [sys.executable, str(SERVICE)])
        print(f"[driver] re-wake: {'launched' if h else 'already served'}")
        watch_until_idle(ch, launcher)
        starteds = ch.read(topics=[Topic.LIFECYCLE_STARTED])
        print(f"[driver] episodes on one run: {len(starteds)}; "
              f"live demand now: {live_demand(ch)}")
        print(f"[driver] terminal: {peek_terminal(ch)}")


if __name__ == "__main__":
    main()
