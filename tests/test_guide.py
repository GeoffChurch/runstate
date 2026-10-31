"""The user guide's code, and the README's, runs against the live tree.

`docs/guide.md` is the how-to a newcomer copies from, and `README.md` the front
page they copy from first, so a block that no longer runs is a bug a reader finds
before we do: the 0.4.0 cutover left both speaking 0.3.0 with nothing to say so.
This is the same drift-guard as `tests/test_examples.py`, pointed at each
document's fenced code.

The convention (stated at the top of the guide, and in the README's Quickstart):

  * a block fenced ```python is RUNNABLE: it runs as written, on its own;
  * a block fenced ```python fragment is a FRAGMENT: a piece of a larger
    program (most often a runnable example under `examples/`, which
    `tests/test_examples.py` runs), shown and not run.

No other info string opens a Python block, so a block can be neither silently.

Each runnable block runs as a fresh process (its own namespace, a real timeout,
and no thread or channel of one block reaching the next), from a working
directory under ``tmp_path``. Its environment is a launcher's, for a
launcher-less direct run: ``RUNSTATE_RUN_ID``, and ``RUNSTATE_CHANNEL_ROOT``
under ``tmp_path``, so ``current_channel()`` births a SQLite log there. No other
``RUNSTATE_*`` variable of the suite's (the bootstrapped Postgres DSN among them)
reaches it, and ``TMPDIR`` keeps every temporary directory it makes under
``tmp_path`` too; with no bytecode written either, a block leaves no file
outside it.
"""

import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# Each document whose Python blocks run, and how few runnable blocks it may hold:
# the code in both is mostly real, so a convention that ran nothing would be
# hollow, and the floor pins that the runnable blocks are not all gone.
DOCS = {
    REPO / "docs" / "guide.md": 8,
    REPO / "README.md": 3,
}

# A fenced block with its whole info string, at any indentation (a block inside a
# list item is indented): a lazy body stops at the first closing fence line at
# the same indentation, and the body is dedented by it.
_FENCE = re.compile(
    r"^(?P<indent> *)```(?P<info>[^\n`]*)\n(?P<code>.*?)\n(?P=indent)```$",
    re.DOTALL | re.MULTILINE,
)

RUNNABLE = "python"
FRAGMENT = "python fragment"


def _python_blocks(doc: Path) -> list[tuple[int, str, str]]:
    """Every fenced Python block of ``doc``, as (line, info string, code)."""
    text = doc.read_text()
    return [
        (
            text.count("\n", 0, m.start()) + 1,
            m.group("info").strip(),
            textwrap.dedent(m.group("code")),
        )
        for m in _FENCE.finditer(text)
        if m.group("info").strip().startswith("py")
    ]


def _runnable(doc: Path) -> list[tuple[int, str]]:
    return [
        (line, code) for line, info, code in _python_blocks(doc) if info == RUNNABLE
    ]


# Every runnable block of every document, as (its id, its code).
CASES = [
    (f"{doc.relative_to(REPO)}:{line}", code)
    for doc in DOCS
    for line, code in _runnable(doc)
]


@pytest.mark.parametrize("doc", list(DOCS), ids=[d.name for d in DOCS])
def test_every_python_block_is_runnable_or_a_fragment(doc):
    blocks = _python_blocks(doc)
    assert blocks, f"no Python blocks found in {doc}"
    unknown = [
        (line, info) for line, info, _ in blocks if info not in (RUNNABLE, FRAGMENT)
    ]
    assert not unknown, (
        f"a Python block's info string is neither {RUNNABLE!r} nor {FRAGMENT!r}, "
        f"so it would be neither run nor declared a fragment: {unknown}"
    )
    assert len(_runnable(doc)) >= DOCS[doc]


@pytest.mark.parametrize("where, code", CASES, ids=[where for where, _ in CASES])
def test_runnable_block_runs(where, code, tmp_path):
    work, runs = tmp_path / "work", tmp_path / "runs"
    work.mkdir()
    runs.mkdir()  # a SQLite root must exist: create_channel births a run, not a root
    script = tmp_path / "block.py"
    script.write_text(code + "\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("RUNSTATE_")}
    env |= {
        "PYTHONPATH": str(REPO),  # import this checkout, whatever env runs the suite
        "PYTHONDONTWRITEBYTECODE": "1",  # no __pycache__ written outside tmp_path
        "TMPDIR": str(tmp_path),
        "RUNSTATE_RUN_ID": "guide",
        "RUNSTATE_CHANNEL_ROOT": str(runs),
        "RUNSTATE_CHANNEL_BACKEND": "sqlite",
    }
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert (
        result.returncode == 0
    ), f"the block at {where} failed:\n{result.stdout}{result.stderr}"
