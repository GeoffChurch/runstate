import os
from runstate.vocabulary.handle import local_handle, resolve


def test_resolve_live_and_dead_local_handle():
    assert resolve(local_handle()) is True            # our own pid is alive
    # a pid that (almost certainly) doesn't exist
    assert resolve("local://anyhost/2147483646") is False
    assert resolve("slurm://12345") is None            # unknown scheme -> not locally resolvable
