"""The public surface stays importable without the optional ``psycopg`` extra.

These assertions used to be Python inlined into ``.github/workflows/tests.yml``, where no
tool in this repo could see them: black does not format YAML string bodies, mypy does not
check them, no test imports them, and a grep for a renamed symbol misses them. So when the
``attach_channel``/``create_channel`` split deleted ``open_channel``, the workflow kept the
dead name and the job raised ``ImportError`` before reaching a single assertion -- it
asserted nothing for six days while still reading as a gate. As ordinary test code a rename
breaks them in the commit that renames.

The extra's ABSENCE is simulated with a ``meta_path`` blocker, so these pass identically
whether or not psycopg is installed -- which is what lets them run in the normal suite on
every commit. The workflow keeps a no-extras job that runs this file against a genuinely
bare install: the blocker proves the import GRAPH is clean, but only a real
``pip install -e .`` proves the PACKAGING is.
"""

import subprocess
import sys
import textwrap

# Raising from find_spec (rather than returning None) makes the import fail the way a
# missing distribution does, without uninstalling anything.
_BLOCK_PSYCOPG = """
import sys


class _NoPsycopg:
    def find_spec(self, name, path=None, target=None):
        if name == "psycopg" or name.startswith("psycopg."):
            raise ImportError("No module named 'psycopg'")
        return None


sys.meta_path.insert(0, _NoPsycopg())
"""


def _without_psycopg(body: str) -> subprocess.CompletedProcess[str]:
    """Run ``body`` in a FRESH interpreter with psycopg unimportable.

    Fresh is load-bearing: this process may already have imported psycopg (the Postgres
    backend tests do), which would make the ``sys.modules`` assertion vacuous."""
    return subprocess.run(
        [sys.executable, "-c", _BLOCK_PSYCOPG + textwrap.dedent(body)],
        capture_output=True,
        text=True,
    )


def test_importing_runstate_does_not_pull_in_psycopg():
    proc = _without_psycopg("""
        import sys

        import runstate
        from runstate import Worker, Watcher
        from runstate.channel import Channel, EpisodeHolder, EpisodeProbe
        from runstate.channel import attach_channel, create_channel

        assert "psycopg" not in sys.modules, "importing runstate pulled in psycopg"
        """)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_both_locators_name_the_extra_when_psycopg_is_absent():
    # BOTH locators, because the psycopg import lives in the backend dispatch they share:
    # neither the attach path nor the birth path may reach Postgres without the extra, and
    # each must fail with an actionable message rather than a raw ImportError.
    proc = _without_psycopg("""
        from runstate.channel import attach_channel, create_channel

        for locate in (attach_channel, create_channel):
            try:
                locate("r", root="postgresql://x", backend="postgres")
            except ImportError as exc:
                assert "pip install runstate[postgres]" in str(exc), exc
            else:
                raise AssertionError("expected ImportError from " + locate.__name__)
        """)
    assert proc.returncode == 0, proc.stdout + proc.stderr
