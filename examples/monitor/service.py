"""On-demand host-metrics monitor — the service-worker dogfood
(docs/specs/service-worker.md).

A stepless service driven by ``Worker.serve()``: it reports host load and
memory while anyone holds a live (keepalive-leased) subscription, and retires
via the careful death the moment demand drains to zero. Launched on demand by
examples/monitor/driver.py.

Cadence note (the spec's guidance): the body's sleep must be well under both
the clients' lease period and any observer's staleness threshold — one tick
cadence serves the heartbeat beacon, the lease-expiry check, and refresh-ack
latency all at once.
"""

import os
import time

import runstate


def _mem_available_frac():
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


def main():
    channel = runstate.attach()  # reads RUNSTATE_* set by the launcher
    with runstate.Worker(channel) as w:
        for _ in w.serve():      # stepless ticks; exits at zero demand
            w.set("load1", os.getloadavg()[0])
            mem = _mem_available_frac()
            if mem is not None:
                w.set("mem_available", mem)
            time.sleep(0.2)      # the body paces the tick cadence
    # serve() already emitted the dying breath via the death-CAS when demand
    # drained; the context manager's __exit__ is the idempotent no-op here.


if __name__ == "__main__":
    main()
