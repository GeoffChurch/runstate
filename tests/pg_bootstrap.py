"""A throwaway PostgreSQL server for the test session, so the Postgres tests run on every
local ``pytest`` instead of skipping.

Without a server about 280 tests skip: every test of the CAS under real cross-process
contention, the advisory-lock probe and the shared-table paths. A skip reads as green, so
a local run and the pre-commit hook used to pass while saying nothing about the backend
that arbitrates cross-host claims; CI was the first place those tests ran. A private
cluster costs about half a second (measured: initdb 0.30 s, start 0.10 s, createdb
0.03 s, stop 0.10 s), so there is no reason to skip.

``conftest.py`` calls ``start`` from ``pytest_configure``, before collection, and
``Cluster.stop`` from ``pytest_unconfigure``:

- An explicit ``RUNSTATE_TEST_PG_DSN`` wins (CI's service, or your own server): nothing
  starts.
- Otherwise the server binaries are looked for next to ``sys.executable`` (``./.conda/bin``
  when the hook runs) and then on ``PATH``. Missing, or older than runstate's floor of 16,
  nothing starts and the tests skip as before, with a session-end line saying why.
- Found, a cluster is made in a fresh 0700 directory under ``/tmp`` and the DSN is put in
  ``os.environ``, where collection-time readers and subprocess workers both see it. The
  directory is ``/tmp`` itself rather than a scratch path because it holds the socket, and
  a socket path is limited to 107 bytes (``sun_path``). Trust auth is safe: the only
  listener is that socket, inside that directory. ``RUNSTATE_TEST_PG_ALTER_SYSTEM=1`` is
  set too, unless you set it yourself, since ``ALTER SYSTEM`` on this cluster changes
  nothing outside it, and the cluster is deleted at session end.

``fsync`` stays on: the channel refuses a server with ``fsync = off``. Only initdb's own
writes skip it (``-N``), and they are the cluster's, which nobody keeps.

A failed step stops what it started, removes the directory and skips the tests with the
reason; the bootstrap never fails the session. An ``atexit`` fallback stops the server if
the session dies without ``pytest_unconfigure``. A SIGKILL or an unhandled SIGTERM runs
neither, and leaves a ``postgres -D /tmp/rspg*`` to kill by hand.
"""

from __future__ import annotations

import atexit
import getpass
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

DSN_VAR = "RUNSTATE_TEST_PG_DSN"
ALTER_SYSTEM_VAR = "RUNSTATE_TEST_PG_ALTER_SYSTEM"
DATABASE = "runstate_test"
MIN_MAJOR = 16  # the channel refuses an older server at open
# pg_ctl's own wait; the subprocess timeout sits just above it. Not a result-changing
# value: it only bounds how long a hung step takes to become a skip.
_WAIT_S = 60
_TOOLS = ("initdb", "pg_ctl", "createdb")
HOW_TO_GET = (
    "install them into the dev env with "
    "`conda install -p ./.conda -c conda-forge postgresql`, "
    f"or set {DSN_VAR} to a server of your own"
)


class Tools(NamedTuple):
    initdb: Path
    pg_ctl: Path
    createdb: Path


@dataclass(frozen=True)
class Skipped:
    """No server was started for the session; ``reason`` says why."""

    reason: str


class Cluster:
    """The running throwaway server. ``stop`` is idempotent, and a no-op in any process
    but the one that started it, so a forked child running ``atexit`` cannot stop the
    session's server."""

    def __init__(
        self, root: Path, port: int, tools: Tools, env_before: dict[str, str | None]
    ):
        self.root = root
        self.dsn = f"postgresql:///{DATABASE}?host={root}&port={port}"
        self._tools = tools
        self._identity = _identity(root)
        self._owner = os.getpid()
        self._env_before = env_before
        # Set once initdb has made the data directory: from then on a stop is owed,
        # even if the start itself fails part-way.
        self.server_started = False
        self._live = True
        atexit.register(self.stop)

    def stop(self) -> None:
        if not self._live or os.getpid() != self._owner:
            return
        self._live = False
        if self.server_started:
            try:
                subprocess.run(
                    [self._tools.pg_ctl, "-D", self.root / "data", "-m", "immediate"]
                    + ["-w", "-t", str(_WAIT_S), "stop"],
                    capture_output=True,
                    timeout=_WAIT_S + 5,
                )
            except (OSError, subprocess.SubprocessError):
                pass  # the directory still goes; a server left on it is in the log
        # Remove only the directory this bootstrap made: same path, same inode.
        if self.root.parent == Path("/tmp") and self.root.name.startswith("rspg"):
            if _identity(self.root) == self._identity:
                shutil.rmtree(self.root, ignore_errors=True)
        for var, value in self._env_before.items():
            if value is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = value


def _identity(path: Path) -> tuple[int, int] | None:
    try:
        st = path.lstat()
    except OSError:
        return None
    return (st.st_dev, st.st_ino)


def find_tools() -> Tools | None:
    """The first directory holding all three binaries: ``sys.executable``'s, then
    ``PATH``'s in order. One directory, so the three are one installation."""
    dirs = [Path(sys.executable).parent]
    dirs += [Path(d) for d in os.environ.get("PATH", "").split(os.pathsep) if d]
    for d in dirs:
        paths = [d / name for name in _TOOLS]
        if all(p.is_file() and os.access(p, os.X_OK) for p in paths):
            return Tools(*paths)
    return None


def _free_port() -> int:
    # Only the socket file's name carries the port (there is no TCP listener), so a race
    # for it is harmless; a free one keeps the name unremarkable.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


def _run(argv: list[str | Path], what: str) -> str:
    proc = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=_WAIT_S + 5,
        stdin=subprocess.DEVNULL,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-3:]
        raise RuntimeError(f"{what} failed: " + " | ".join(tail))
    return proc.stdout


def start() -> Cluster | Skipped | None:
    """Start the session's server and export its DSN. ``None``: an explicit DSN was
    given, so nothing was started. ``Skipped``: nothing is running, and the Postgres
    tests skip for the reason it carries."""
    if os.environ.get(DSN_VAR):
        return None
    tools = find_tools()
    if tools is None:
        return Skipped(
            f"PostgreSQL server binaries ({', '.join(_TOOLS)}) not found next to "
            f"{sys.executable} or on PATH; {HOW_TO_GET}"
        )
    try:
        version = _run([tools.initdb, "--version"], "initdb --version")
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        return Skipped(f"{tools.initdb}: {exc}")
    found = re.search(r"(\d+)(?:\.\d+)?", version)
    if found is None or int(found.group(1)) < MIN_MAJOR:
        return Skipped(
            f"{tools.initdb} is {version.strip()!r}; runstate needs PostgreSQL "
            f">= {MIN_MAJOR}; {HOW_TO_GET}"
        )

    try:
        port = _free_port()
    except OSError as exc:
        return Skipped(f"no free port for the throwaway PostgreSQL: {exc}")
    root = Path(tempfile.mkdtemp(prefix="rspg", dir="/tmp"))  # mkdtemp makes it 0700
    env_before = {var: os.environ.get(var) for var in (DSN_VAR, ALTER_SYSTEM_VAR)}
    cluster = Cluster(root, port, tools, env_before)
    try:
        data, user = root / "data", getpass.getuser()
        _run(
            [tools.initdb, "-D", data, "-U", user, "--auth-local=trust", "-N"]
            + ["-E", "UTF8", "--no-locale"],
            "initdb",
        )
        cluster.server_started = True
        _run(
            [tools.pg_ctl, "-w", "-t", str(_WAIT_S), "-D", data, "-l", root / "log"]
            + ["-o", f"-k {root} -p {port} -c listen_addresses=''", "start"],
            "pg_ctl start",
        )
        _run(
            [tools.createdb, "-h", root, "-p", str(port), "-U", user, DATABASE],
            "createdb",
        )
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        log = root / "log"
        detail = log.read_text().strip().splitlines()[-3:] if log.exists() else []
        cluster.stop()
        return Skipped(
            f"the throwaway PostgreSQL failed to start ({exc}"
            + (f"; server log: {' | '.join(detail)}" if detail else "")
            + ")"
        )
    os.environ[DSN_VAR] = cluster.dsn
    os.environ.setdefault(ALTER_SYSTEM_VAR, "1")
    return cluster
