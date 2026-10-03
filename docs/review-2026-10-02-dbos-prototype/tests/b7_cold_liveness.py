"""B7 cold liveness: tell a live run from a kill -9'd one, from the SQLite file alone."""

import json
import os
import subprocess
import time

from common import *

root = fresh_root("b7")
import rs_dbos as r

premigrate(root)
c_live, c_dead = dict(CONFIG, seed=1), dict(CONFIG, seed=2)
live, dead = r.run_id(c_live), r.run_id(c_dead)
env = {
    "RS_STEP_SECONDS": "0.05",
    "RS_CHUNK": "100",
}  # long DBOS steps: no step row while one runs
pl = spawn(root, 600, env, tag="live", config=c_live)
pd = spawn(root, 600, env, tag="dead", config=c_dead)
wait_until(lambda: len(ckpt_log(root, dead)) >= 20 and len(ckpt_log(root, live)) >= 20)
kill9(pd)
t_kill = time.time()


def observe():
    p = subprocess.run(
        [
            PY,
            os.path.join(HERE, "b7_observer.py"),
            os.path.join(root, "sys.sqlite"),
            live,
            dead,
        ],
        capture_output=True,
        text=True,
        cwd=HERE,
        timeout=60,
    )
    return json.loads(p.stdout.strip().splitlines()[-1])


timeline, detected_at = [], None
while time.time() - t_kill < r.STALE + 3:
    o = observe()
    dt = round(time.time() - t_kill, 2)
    timeline.append((dt, o))
    if detected_at is None and not o[dead]["layer_is_alive"]:
        detected_at = dt
    time.sleep(0.4)
pl.kill()
for dt, o in timeline[:1] + timeline[-1:]:
    print(f"t_kill+{dt}s:")
    for rid, tag in ((live, "LIVE"), (dead, "KILLED")):
        print(f"   {tag:6} {o[rid]}")
live_always = all(o[live]["layer_is_alive"] for _, o in timeline)
dbos_same = all(
    o[live]["dbos_status"] == o[dead]["dbos_status"] == "PENDING" for _, o in timeline
)
print(
    f"DBOS status identical (PENDING) for live and killed at every observation: {dbos_same}"
)
print(
    f"layer: killed run first reported not-alive at t_kill+{detected_at}s (STALE={r.STALE}, beacon every {r.HB_EVERY}s); "
    f"live run reported alive at all {len(timeline)} observations: {live_always}"
)
ok = (
    live_always
    and detected_at is not None
    and timeline[-1][1][dead]["layer_verdict"] == "presumed_dead"
)
verdict_line(
    "B7 cold liveness",
    "PASS" if ok else "FAIL",
    [
        f"DBOS alone cannot tell them apart: {dbos_same}",
        f"layer (beacon table + staleness rule) tells them apart after <= {detected_at}s",
    ],
)
