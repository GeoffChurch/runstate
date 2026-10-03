"""Shared test plumbing: a fresh RS_ROOT per test, worker spawning, DB dumps."""

import json
import os
import signal
import sqlite3
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROTO = os.path.dirname(HERE)
PY = os.path.join(PROTO, "venv", "bin", "python")
WORKER = os.path.join(PROTO, "worker.py")
CONFIG = {"lr": 0.1, "width": 64, "seed": 7}


def fresh_root(name: str) -> str:
    root = os.path.join(
        PROTO, "runs", f"{name}-{time.strftime('%H%M%S')}-{os.getpid()}"
    )
    os.makedirs(root, exist_ok=True)
    os.environ["RS_ROOT"] = root
    if PROTO not in sys.path:
        sys.path.insert(0, PROTO)
    import rs_dbos  # re-point the (already imported) layer at this root

    rs_dbos.ROOT, rs_dbos.DB_PATH = root, os.path.join(root, "sys.sqlite")
    rs_dbos.DB_URL = f"sqlite:///{rs_dbos.DB_PATH}"
    return root


def spawn(root, until, extra_env=None, tag="w", config=None):
    env = dict(os.environ, RS_ROOT=root, PYTHONUNBUFFERED="1", **(extra_env or {}))
    log = open(os.path.join(root, f"{tag}.log"), "w")
    p = subprocess.Popen(
        [PY, WORKER, json.dumps(config or CONFIG), str(until)],
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        cwd=PROTO,
    )
    p.tag, p.logpath = tag, log.name
    return p


def log_of(p) -> str:
    with open(p.logpath) as f:
        return f.read()


def q(root, sql, args=()):
    c = sqlite3.connect(os.path.join(root, "sys.sqlite"), timeout=30)
    try:
        return c.execute(sql, args).fetchall()
    finally:
        c.close()


def status_rows(root):
    return q(
        root,
        "SELECT workflow_uuid, status, executor_id, owner_xid IS NOT NULL, recovery_attempts,"
        " created_at, updated_at FROM workflow_status ORDER BY created_at",
    )


def stream_rows(root):
    return q(
        root,
        'SELECT workflow_uuid, "offset", function_id, value FROM streams ORDER BY workflow_uuid, "offset"',
    )


def ckpt(root, rid):
    try:
        with open(os.path.join(root, "ckpt", rid, "ckpt.json")) as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def ckpt_log(root, rid):
    try:
        with open(os.path.join(root, "ckpt", rid, "ckpt_log.jsonl")) as f:
            return [json.loads(l) for l in f]
    except FileNotFoundError:
        return []


def wait_until(pred, timeout=30, every=0.05):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return True
        time.sleep(every)
    return False


def stream_steps(root):
    return [json.loads(v)["step"] for _, _, _, v in stream_rows(root)]


def kill9(p):
    os.kill(p.pid, signal.SIGKILL)
    p.wait()


def verdict_line(name, result, evidence):
    print(f"\n=== {name}: {result} ===")
    for line in evidence:
        print("  " + line)


def premigrate(root):
    """Create + migrate the DBOS system DB once (concurrent first launches race on DDL)."""
    env = dict(os.environ, RS_ROOT=root)
    subprocess.run(
        [PY, "-c", "import rs_dbos, os; rs_dbos._launch(); os._exit(0)"],
        env=env,
        cwd=PROTO,
        check=True,
        capture_output=True,
    )
