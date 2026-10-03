"""B3 crash and resume: kill -9 mid-run; later a new process resumes (via the layer's run()).

Rounds: kill at random instants, plus one deterministic kill in the value->checkpoint window.
Checks: resumes from the checkpoint step; values() continuous & duplicate-free; is there a record of
two attempts?
"""

import json
import random
import time

from common import *

UNTIL = 40


def one(i, window=False):
    root = fresh_root(f"b3{'w' if window else ''}{i}")
    import rs_dbos as r

    premigrate(root)
    rid = r.run_id(CONFIG)
    env = {"RS_STEP_SECONDS": "0.05"}
    if window:
        a = spawn(root, UNTIL, dict(env, RS_FREEZE_AT="13"), tag="A")
        with open(f"/proc/{a.pid}/stat") as f:
            pass
        assert wait_until(
            lambda: open(f"/proc/{a.pid}/stat").read().split(")")[1].split()[0] == "T",
            30,
        )
    else:
        a = spawn(root, UNTIL, env, tag="A")
        wait_until(lambda: len(ckpt_log(root, rid)) >= 1)
        time.sleep(random.uniform(0.2, 1.4))
    kill9(a)
    at_kill = ckpt(root, rid)
    rows_at_kill = len(stream_rows(root))
    dbos_status_dead = status_rows(root)
    time.sleep(r.STALE + 0.5)
    b = spawn(root, UNTIL, env, tag="B")
    b.wait(60)
    rows = [json.loads(v) for _, _, _, v in stream_rows(root)]
    b_rows = [v for v in rows if v["pid"] == b.pid]
    resumed_from = b_rows[0]["step"] if b_rows else None
    steps = [v["step"] for v in rows]
    dups = sorted({s for s in steps if steps.count(s) > 1})
    vals = r.values(rid)
    contiguous = [v["step"] for v in vals] == list(range(UNTIL))
    beacons = q(root, "SELECT wid, pid FROM rs_beacon")
    ops = q(
        root,
        "SELECT function_id, function_name, output, started_at_epoch_ms FROM operation_outputs"
        " WHERE workflow_uuid=? AND function_name='_chunk' ORDER BY function_id",
        (f"{rid}.0",),
    )
    ok = at_kill is not None and resumed_from == at_kill["step"] and contiguous
    ev = [
        f"round {i}{' (kill in value->ckpt window)' if window else ''}: A={a.pid} killed; ckpt at kill={at_kill}; stream rows at kill={rows_at_kill}",
        f"  DBOS status right after kill -9: {dbos_status_dead}",
        f"  B={b.pid} first trained step={resumed_from} (ckpt said {at_kill and at_kill['step']}); B result: {[l for l in log_of(b).splitlines() if 'RESULT' in l]}",
        f"  raw stream rows={len(rows)} duplicate steps in raw stream={dups}; values() contiguous 0..{UNTIL-1} after take-latest dedupe={contiguous}",
        f"  DBOS status after resume: {status_rows(root)}",
        f"  record of two attempts? DBOS: executor_id now B's only; recovery_attempts column above; _chunk step rows: {ops}",
        f"  layer: beacon rows {beacons}; writer pids in values: {sorted({v['pid'] for v in rows})}",
    ]
    return ok, bool(dups), ev


if __name__ == "__main__":
    random.seed(1)
    res = []
    for i in range(4):
        res.append(one(i))
    res.append(one(9, window=True))
    for _, _, ev in res:
        print("\n".join(ev), flush=True)
    resumed_ok = all(ok for ok, _, _ in res)
    raw_dups = sum(1 for _, d, _ in res if d)
    verdict_line(
        "B3 crash and resume",
        "PASS" if resumed_ok else "FAIL",
        [
            f"resumed from the checkpoint step and values() continuous in {sum(ok for ok, _, _ in res)}/{len(res)} rounds",
            f"raw stream had duplicate steps in {raw_dups}/{len(res)} rounds (deduped only by the layer's take-latest read)",
            "attempt history: see per-round lines (DBOS keeps a counter and overwrites executor_id; the layer's beacon/pids are the only per-attempt trace)",
        ],
    )
