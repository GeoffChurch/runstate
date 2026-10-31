"""On-demand host-metrics monitor — the service-worker dogfood
(docs/specs/service-worker.md).

A stepless service driven by ``Worker.serve()``: it reports host load and
memory while anyone holds a live (keepalive-leased) subscription, and retires
via the careful death the moment demand drains to zero. Launched on demand by
examples/monitor/driver.py.

Cadence note (the spec's guidance): the body's sleep must be well under both
the clients' lease period and any observer's staleness threshold — one
iteration's cadence serves the commit, the lease-expiry check, and
refresh-ack latency all at once.
"""

import os
import time

import runstate


def _mem_available_frac() -> float | None:
    """MemAvailable/MemTotal from /proc/meminfo (Linux), else None."""
    try:
        fields = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                fields[k] = int(v.strip().split()[0])
        return fields["MemAvailable"] / fields["MemTotal"]
    except (OSError, KeyError, ValueError):
        return None


def main() -> None:
    channel = runstate.current_channel()  # reads RUNSTATE_* set by the launcher
    # A woken service holds no state, so it continues the previous claim's head
    # (LatestClaimHead): an older branch's expiries stay expired. Both metrics
    # are sources, sampled only when a subscription fires.
    with runstate.Worker(
        channel,
        resume_from=runstate.LatestClaimHead(),
        sources=["load1", "mem_available"],
        save=runstate.NoSave(),
    ) as w:
        for _ in w.serve():  # one commit per iteration; retires at zero demand
            w.set("load1", os.getloadavg()[0])
            mem = _mem_available_frac()
            if mem is not None:
                w.set("mem_available", mem)
            time.sleep(0.2)  # the body paces the commit cadence
    # serve() already wrote the exit, by the careful death's CAS, when demand
    # drained; the context manager's __exit__ is the idempotent no-op here.


if __name__ == "__main__":
    main()
