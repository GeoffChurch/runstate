"""MemoryChannel must serialize concurrent in-process writers.

ThreadLauncher runs the worker on a thread while the orchestrator sends control
from another thread — both write the *same* run's in-memory log through
distinct channel instances (shared via the open_channel registry). The seq
read-modify-write must be atomic across instances: no lost or duplicated seqs.
"""

import sys
import threading

from runstate.channel import open_channel


def test_concurrent_writers_produce_unique_contiguous_seqs(tmp_path):
    writers, n = 4, 3000
    # Force the GIL to switch as often as possible so the seq read-modify-write
    # window is reliably interleaved (otherwise the race hides behind the GIL).
    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-9)
    try:
        chans = [
            open_channel("race", root=tmp_path, backend="memory")  # all share one log
            for _ in range(writers)
        ]

        def hammer(ch):
            for i in range(n):
                ch.send({"i": i}, topic="value")

        threads = [threading.Thread(target=hammer, args=(ch,)) for ch in chans]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        sys.setswitchinterval(old)

    total = writers * n
    seqs = [e.seq for e in chans[0].read()]
    assert len(seqs) == total  # no lost writes
    assert sorted(seqs) == list(range(1, total + 1))  # unique + contiguous
