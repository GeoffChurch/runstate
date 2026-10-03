"""runstate-over-DBOS: a thin layer over DBOS Transact with a SQLite system database (prototype).

Worker:      run(config, until)        -- the worker entry point (one process per call)
Third party: stop / values / verdict / is_alive (rid)   -- DBOSClient only, never DBOS.launch()
Consumer:    ensure(config, until)     -- read first, produce on miss (spawns a worker process)

Identity: rid = content hash of config (target excluded).  DBOS workflow id = "<rid>.<k>", one
SEGMENT per DBOS workflow, because a finished DBOS workflow cannot take new inputs.  The run is the
union of its segments; values() stitches their streams.
"""

import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from typing import Any, Optional

from dbos import (
    DBOS,
    DBOSClient,
    DBOSPortableJSONSerializer,
    SetWorkflowID,
    WorkflowStatus,
)
from dbos._error import DBOSStreamTimeoutError

ROOT = os.environ.get("RS_ROOT", "/tmp/rs-default")
DB_PATH = os.path.join(ROOT, "sys.sqlite")
DB_URL = f"sqlite:///{DB_PATH}"
HB_EVERY = 0.5  # beacon period (s)
STALE = 3.0  # a PENDING segment whose newest beacon/claim is older than this is presumed dead
TRAINER: Any = (
    None  # the user's trainer (resume_step / train_step / after_value / checkpoint)
)
SERIALIZER = (
    DBOSPortableJSONSerializer()
)  # JSON in every column, so a reader needs no DBOS


def run_id(config: dict) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:12]


def _seg(rid: str, k: int) -> str:
    return f"{rid}.{k}"


def _segments(api: Any, rid: str) -> list[WorkflowStatus]:
    ws = api.list_workflows(workflow_id_prefix=rid + ".", load_input=False)
    return sorted(ws, key=lambda w: int(w.workflow_id.rsplit(".", 1)[1]))


# ---- liveness: DBOS has no heartbeat, so the layer keeps a beacon table in the same file ----------
def _db() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.execute(
        "CREATE TABLE IF NOT EXISTS rs_beacon (wid TEXT, pid INTEGER, host TEXT, t REAL,"
        " PRIMARY KEY (wid, pid))"
    )
    return c


class _Beacon(threading.Thread):
    def __init__(self, wid: str) -> None:
        super().__init__(daemon=True)
        self.wid, self.halt = wid, threading.Event()

    def run(self) -> None:
        c = _db()
        while not self.halt.is_set():
            c.execute(
                "INSERT OR REPLACE INTO rs_beacon VALUES (?,?,?,?)",
                (self.wid, os.getpid(), socket.gethostname(), time.time()),
            )
            c.commit()
            self.halt.wait(HB_EVERY)


def _alive(st: WorkflowStatus) -> bool:
    if st.status != "PENDING":
        return False
    try:
        c = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=30)
        t = c.execute(
            "SELECT max(t) FROM rs_beacon WHERE wid=?", (st.workflow_id,)
        ).fetchone()[0]
    except sqlite3.OperationalError:  # no beacon table yet
        t = None
    claimed = (
        st.updated_at or 0
    ) / 1000  # DBOS dates the claim (insert / dequeue): grace window
    return time.time() - max(t or 0.0, claimed) < STALE


# ---- stop: a durable message; DBOS has no API to see or consume another workflow's messages -------
def _stops(wid: str, consume: bool) -> int:
    c, where = _db(), "WHERE destination_uuid=? AND topic='stop' AND consumed=0"
    n = c.execute(f"SELECT count(*) FROM notifications {where}", (wid,)).fetchone()[0]
    if (
        consume and n
    ):  # mark them answered (-1: consumed by the layer, not by a DBOS step)
        c.execute(
            f"UPDATE notifications SET consumed=1, consumed_by_function_id=-1 {where}",
            (wid,),
        )
        c.commit()
    return n


def stop_requested() -> bool:
    """For use INSIDE a long step: peek (not consume) at this workflow's pending stops."""
    return _stops(DBOS.workflow_id, consume=False) > 0


# ---- the workflow skeleton (deterministic) and its steps (the user's training code runs inside) ---
@DBOS.step()
def _resume_step(rid: str) -> int:
    return TRAINER.resume_step(rid)


@DBOS.step()
def _chunk(rid: str, end: int, peek: bool) -> dict:
    k = TRAINER.resume_step(rid)  # the worker's own checkpoint is the truth
    while k < end:
        if peek and stop_requested():
            return {"step": k}
        v = TRAINER.train_step(rid, k)
        DBOS.write_stream(
            "loss", {"step": k, "loss": v, "t": time.time(), "pid": os.getpid()}
        )  # fenced
        TRAINER.after_value(rid, k)
        TRAINER.checkpoint(rid, k + 1)
        k += 1
    return {"step": k}


def _drain() -> int:
    """Consume every pending stop at a safe point: one halt answers them all."""
    n = 0
    while DBOS.recv("stop", timeout_seconds=0) is not None:
        n += 1
    return n


@DBOS.workflow()
def _train(rid: str, until: int, chunk: int, stop_first: bool, peek: bool) -> dict:
    beacon = _Beacon(DBOS.workflow_id)
    beacon.start()
    try:
        k = _resume_step(rid)
        while k < until:
            if stop_first or _drain():
                return {"final_step": k, "stopped": True}
            k = _chunk(rid, min(k + chunk, until), peek)["step"]
        return {"final_step": k, "stopped": _drain() > 0}
    finally:
        beacon.halt.set()


# ---- worker entry point --------------------------------------------------------------------------
def _launch() -> None:
    os.makedirs(ROOT, exist_ok=True)
    exec_id = (
        os.environ.get("RS_EXECUTOR_ID")
        or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    )
    DBOS(
        config={
            "name": "mycooc-sim",
            "system_database_url": DB_URL,
            "application_version": os.environ.get("RS_APPVER", "v1"),  # pinned: see B3b
            "executor_id": exec_id,  # unique: DBOS's same-id startup recovery would steal live runs
            "serializer": SERIALIZER,
            "log_level": os.environ.get("RS_LOG", "WARNING"),
            "notification_listener_polling_interval_sec": 0.1,
        }
    )
    DBOS.launch()


def run(config: dict, until: int, chunk: int = 10, peek: bool = False) -> dict:
    rid = run_id(config)
    _launch()
    while True:
        segs = _segments(DBOS, rid)
        last = segs[-1] if segs else None
        if last is not None and last.status in ("PENDING", "ENQUEUED", "CANCELLED"):
            if last.status == "PENDING" and _alive(last):
                return {
                    "lost": True,
                    "segment": last.workflow_id,
                }  # a live owner holds the run
            if (
                last.status == "PENDING"
            ):  # presumed dead: re-enqueue iff still on that executor
                DBOS._recover_pending_workflows([last.executor_id])
            else:
                DBOS.resume_workflow(last.workflow_id)
            out = DBOS.retrieve_workflow(last.workflow_id).get_result()
        else:
            if (
                last is not None
                and last.status == "SUCCESS"
                and last.output["final_step"] >= until
            ):
                return last.output
            k = 0 if last is None else int(last.workflow_id.rsplit(".", 1)[1]) + 1
            fwd = (
                0 if last is None else _stops(last.workflow_id, consume=True)
            )  # stop sent while down
            with SetWorkflowID(_seg(rid, k)):
                out = DBOS.start_workflow(
                    _train, rid, until, chunk, fwd > 0, peek
                ).get_result()
        if out["stopped"] or out["final_step"] >= until:
            return out


# ---- third party: DBOSClient only (it never dequeues work) ----------------------------------------
def _client() -> DBOSClient:
    return DBOSClient(system_database_url=DB_URL, serializer=SERIALIZER)


def stop(rid: str) -> bool:
    segs = _segments(c := _client(), rid)
    if not segs:
        return False  # a DBOS message needs an existing destination workflow
    c.send(segs[-1].workflow_id, {"t": time.time()}, topic="stop")
    return True


def values(rid: str) -> list[dict]:
    c, by_step = _client(), {}
    for s in _segments(c, rid):
        try:  # read_stream blocks on a non-terminal workflow: a short timeout makes it a snapshot
            for v in c.read_stream(s.workflow_id, "loss", timeout_seconds=0.05):
                by_step[v["step"]] = (
                    v  # take-the-latest per step: steps are at-least-once
                )
        except DBOSStreamTimeoutError:
            pass
    return [by_step[k] for k in sorted(by_step)]


def verdict(rid: str) -> Optional[str]:
    segs = _segments(_client(), rid)
    if not segs:
        return None
    s = segs[-1]
    if s.status == "PENDING":
        return "running" if _alive(s) else "presumed_dead"
    if s.status in ("ERROR", "MAX_RECOVERY_ATTEMPTS_EXCEEDED"):
        return "errored"
    return "preempted"  # SUCCESS (target reached or stopped), CANCELLED, ENQUEUED: clean, resumable


def is_alive(rid: str) -> bool:
    return verdict(rid) == "running"


# ---- consumer --------------------------------------------------------------------------------------
def _progress(vals: list[dict]) -> int:
    k = 0
    while k < len(vals) and vals[k]["step"] == k:
        k += 1
    return k  # dense emitter: contiguous steps from 0


def ensure(
    config: dict, until: int, worker: str = "worker.py", poll: float = 0.2
) -> list[dict]:
    rid = run_id(config)
    while True:
        vals = values(rid)
        if _progress(vals) >= until:
            return vals[:until]
        v = verdict(rid)
        if v == "running":
            time.sleep(poll)
            continue
        if v == "errored":
            raise RuntimeError(f"run {rid} errored")
        before = _progress(vals)
        subprocess.run(
            [sys.executable, worker, json.dumps(config), str(until)], check=False
        )
        if _progress(values(rid)) <= before and not is_alive(rid):
            raise RuntimeError(f"run {rid}: no progress")
