import os
from runstate.vocabulary.handle import handle_pid, local_handle, resolve


def test_resolve_live_and_dead_local_handle():
    assert resolve(local_handle()) is True            # our own pid is alive
    # a pid that (almost certainly) doesn't exist
    assert resolve("local://anyhost/2147483646") is False
    assert resolve("slurm://12345") is None            # unknown scheme -> not locally resolvable


def test_handle_pid_parses_the_local_grammar():
    # the ONE place the local-handle grammar is parsed (audit F8): the deferred
    # ?start= pid-reuse disambiguator lands here, not in every consumer's rsplit.
    assert handle_pid("local://somehost/4242") == 4242
    assert handle_pid(local_handle()) == os.getpid()


def test_handle_pid_none_for_foreign_scheme_or_garbage():
    assert handle_pid("slurm://12345") is None         # not the local grammar
    assert handle_pid("local://host/notanint") is None
    assert handle_pid("local://") is None
