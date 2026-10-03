"""B6 ensure reads first: with values 0..19 present, ensure(config, 15) launches nothing."""

import os
import subprocess
import time

from common import *

root = fresh_root("b6")
import rs_dbos as r

premigrate(root)
rid = r.run_id(CONFIG)
w = spawn(root, 20, {"RS_STEP_SECONDS": "0.02"}, tag="setup")
w.wait(60)
print(
    f"setup: worker ran to 20; values present={len(r.values(rid))}; segments={[(s[0], s[1]) for s in status_rows(root)]}"
)

launches = []
real_run = subprocess.run


def spy(*a, **k):
    launches.append(a[0])
    return real_run(*a, **k)


r.subprocess.run = spy
rows_before, log_before = status_rows(root), len(ckpt_log(root, rid))
t0 = time.time()
got = r.ensure(CONFIG, 15, worker=WORKER)
dt = time.time() - t0
rows_after, log_after = status_rows(root), len(ckpt_log(root, rid))
ok = (
    not launches
    and rows_before == rows_after
    and log_before == log_after
    and [v["step"] for v in got] == list(range(15))
)
print(
    f"ensure(config, 15) -> {len(got)} values (steps {got[0]['step']}..{got[-1]['step']}) in {dt*1000:.0f} ms"
)
print(
    f"  worker launches={launches}; workflow_status rows changed={rows_before != rows_after}; new ckpt writes={log_after - log_before}"
)
verdict_line(
    "B6 ensure reads first",
    "PASS" if ok else "FAIL",
    [f"no launch, no DB write by DBOS, served from streams: {ok}"],
)
