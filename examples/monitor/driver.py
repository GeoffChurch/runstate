"""Drive the on-demand monitor (docs/specs/service-worker.md +
docs/specs/lazy-launch.md).

The whole service story, end to end, twice:

1. DEMAND FIRST: a keepalive-leased subscription lands on the durable channel
   before any worker exists (the log buffers demand).
2. WAKE: ``ensure_served`` — live demand, no held claim → launch. The
   service's first drain registers the subscribe that justified the launch
   (binding the lease to its claim) and it serves while pinned.
3. READ: the ``Answers`` fold — each sample a fired subscription takes is
   committed by the node that fired it, so a lease's answers are a lineage
   read like any other (``load1`` is a source, so it is no eager stream:
   ``series`` and ``LastElement`` count none of it).
4. LAPSE: no refresh is sent; the node at which the lease runs out names it
   in its ``expired``, which spends it in the answer fold (``LiveDemand``),
   and the worker retires via the careful death (the CAS'd exit).
5. RE-DEMAND, RE-WAKE: a subscribe under a fresh id is live for the next
   claim (no claim has bound it yet — specs/time-lease-boundary.md; the
   lapsed id is spent by its expiry), and the same ``ensure_served`` call
   starts claim 2 on the same run. Note: never gate wake logic on
   ``peek_terminal`` — between the launch and the child's claim it still
   shows the previous claim's verdict (the wake-gap).
"""

import sys
import tempfile
import time
from pathlib import Path

from runstate import (
    Channel,
    Command,
    Commit,
    Answers,
    HeadFold,
    LiveDemand,
    LocalLauncher,
    Stopped,
    Topic,
    create_channel,
    ensure_served,
    peek_terminal,
)

RUN = "monitor"
LEASE = 2.0  # seconds: the keepalive lease each subscribe grants
SERVICE = Command([sys.executable, str(Path(__file__).parent / "service.py")], env={})


def demand(ch: Channel, rid: str) -> None:
    ch.send(
        {"every": {"time_seconds": 0.5}, "until": {"time_seconds": LEASE}},
        topic=Topic.CONTROL_SUBSCRIBE,
        name="load1",
        request_id=rid,
    )


def verdict(ch: Channel) -> str:
    r = peek_terminal(ch)
    return "none" if r is None else f"{', '.join(sorted(r.outcomes))} ({r.tier})"


def expiries(ch: Channel) -> list[tuple[str, int]]:
    """Each lapsed lease and the node that expired it: the nodes' ``expired``,
    the field the answer fold spends a lease by. (A one-shot scan, for the
    printout; the fold itself reads only what is new.)"""
    out: list[tuple[str, int]] = []
    for e in ch.read(topics=[Topic.LIFECYCLE_COMMIT, Topic.LIFECYCLE_STOPPED]):
        node = (
            Commit(**e.body) if e.topic == Topic.LIFECYCLE_COMMIT else Stopped(**e.body)
        )
        out += [(rid, e.seq) for rid in node.expired]
    return out


def watch_until_idle(
    ch: Channel, launcher: LocalLauncher, live: LiveDemand, rid: str
) -> None:
    """Read the lease's answers while the claim serves; return when it retires."""
    heads, load = HeadFold(), Answers(rid)
    deadline = time.time() + LEASE * 5
    while time.time() < deadline:
        launcher.reap()  # the activator discipline
        if not live.poll(ch) and peek_terminal(ch) is not None:
            return
        answered = load.poll(ch, heads.poll(ch))
        if answered:
            e = answered[-1]
            print(f"[driver] load1 = {e.value:.2f} ({len(answered)} answers to {rid})")
        time.sleep(0.4)


def main() -> None:
    root = tempfile.mkdtemp(prefix="runstate-monitor-")  # off the repo tree
    launcher = LocalLauncher(root=root)
    # One answer fold for the run, held across every call: each poll reads only
    # the records since the last.
    live = LiveDemand()
    # the channel is a context manager, as the launcher is
    with create_channel(RUN, root=root) as ch, launcher:
        # ----- claim 1 -----
        demand(ch, "dash-1")
        print(
            "[driver] demand pre-staged; live:", [e.request_id for e in live.poll(ch)]
        )
        assert ensure_served(launcher, RUN, SERVICE, demand=live) is not None
        watch_until_idle(ch, launcher, live, "dash-1")  # lease lapses -> careful death
        print(
            f"[driver] claim 1 over: terminal={verdict(ch)}, "
            f"live demand={[e.request_id for e in live.poll(ch)]}, "
            f"expired (lease, node)={expiries(ch)}"
        )

        # ----- claim 2: re-demand re-wakes the SAME run -----
        demand(ch, "dash-2")
        h = ensure_served(launcher, RUN, SERVICE, demand=live)
        print(f"[driver] re-wake: {'launched' if h else 'already served'}")
        watch_until_idle(ch, launcher, live, "dash-2")
        starteds = ch.read(topics=[Topic.LIFECYCLE_STARTED])
        print(
            f"[driver] claims on one run: {len(starteds)}; "
            f"live demand now: {[e.request_id for e in live.poll(ch)]}"
        )
        print(f"[driver] terminal: {verdict(ch)}; expired: {expiries(ch)}")


if __name__ == "__main__":
    main()
