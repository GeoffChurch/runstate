"""The in-repo examples run to completion (plan Task 21;
docs/specs/commits-and-lineage.md §8, "The in-repo examples").

Nothing ran the examples before this test, so a release could break every one
of them in silence, as the 0.4.0 cutover did. Each case runs a driver exactly as
its docstring tells a reader to, ``python examples/<name>/driver.py`` from the
repo root, and asserts it exits 0. The drivers print what they show; the test
pins only that they run against the live tree.

``vqe`` needs qiskit and numpy, and ``submitit`` needs submitit, none of which
the dev env installs; each is skipped where its dependencies are absent, and
type-checked instead (``mypy --strict examples/<name>``). A driver's own "needs
X" exit 0 is never a pass: the skip is taken before the driver runs, and the
notice is refused if it is printed anyway.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# What each example needs beyond the dev env, skipped (not passed) without it.
NEEDS = {
    "minimal": (),
    "monitor": (),
    "reuse": (),
    "redrive": (),
    "vqe": ("qiskit", "numpy"),
    "submitit": ("submitit",),
}


@pytest.mark.parametrize("name", list(NEEDS))
def test_each_example_runs_to_completion(name, tmp_path):
    for module in NEEDS[name]:
        pytest.importorskip(module)
    # The example's own environment, as a reader's shell would give it: no
    # RUNSTATE_* variable of the suite's (the bootstrapped Postgres DSN among
    # them) reaches it, so each example uses the backend it chooses itself.
    # TMPDIR keeps every temp root it makes under the test's own directory.
    env = {k: v for k, v in os.environ.items() if not k.startswith("RUNSTATE_")}
    env |= {"TMPDIR": str(tmp_path), "VQE_ROOT": str(tmp_path / "vqe")}
    result = subprocess.run(
        [sys.executable, str(Path("examples") / name / "driver.py")],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"examples/{name} needs" not in result.stdout
