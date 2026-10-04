"""B5 extend after completion: run to N=10 and complete; then ensure(config, 20)."""

import json
import os
import subprocess
import time

from common import *

root = fresh_root("b5")
import rs_dbos as r

premigrate(root)
rid = r.run_id(CONFIG)
os.environ["RS_STEP_SECONDS"] = "0.05"

first = r.ensure(CONFIG, 10, worker=WORKER)
st0 = status_rows(root)
ck0 = ckpt(root, rid)
print(
    f"ensure(config, 10) -> {len(first)} values; segments={[(s[0], s[1]) for s in st0]}; ckpt={ck0}"
)

# DBOS-native verbs on the finished workflow, with a raised target
p = subprocess.run(
    [PY, os.path.join(HERE, "b5_native_probe.py"), rid, "20"],
    env=dict(os.environ),
    cwd=PROTO,
    capture_output=True,
    text=True,
    timeout=120,
)
probe = json.loads([l for l in p.stdout.splitlines() if l.startswith("PROBE ")][0][6:])
for k, v in probe.items():
    print(f"  native {k}: {v}")
silently_ignored = probe["start_same_id_new_until"]["final_step"] == 10
print(
    f"  -> starting the finished id with until=20 returned final_step="
    f"{probe['start_same_id_new_until']['final_step']} with no error: silently ignored={silently_ignored}"
)
fork_ids = [s[0] for s in status_rows(root) if not s[0].startswith(rid + ".")]
before_log = len(ckpt_log(root, rid))
t0 = time.time()

got = r.ensure(CONFIG, 20, worker=WORKER)
new_writes = ckpt_log(root, rid)[before_log:]
new_steps = [e["step"] - 1 for e in new_writes]
pids = sorted({e["pid"] for e in new_writes})
steps = [v["step"] for v in got]
segs = [(s[0], s[1]) for s in status_rows(root) if s[0].startswith(rid + ".")]
per_seg = q(
    root,
    "SELECT workflow_uuid, min(json_extract(value,'$.step')), max(json_extract(value,'$.step')), count(*)"
    " FROM streams GROUP BY workflow_uuid",
)
print(
    f"ensure(config, 20) -> {len(got)} values, steps={steps[0]}..{steps[-1]} contiguous={steps == list(range(20))}"
)
print(
    f"  steps trained during the extend: {new_steps} by pids {pids}  (recomputed any of 0..9: {any(s < 10 for s in new_steps)})"
)
print(
    f"  segments (DBOS workflow ids) now: {segs}; fork rows left behind by the probe: {fork_ids}"
)
print(f"  stream rows per workflow id (min step, max step, n): {per_seg}")
ok = steps == list(range(20)) and new_steps == list(range(10, 20))
verdict_line(
    "B5 extend after completion",
    "PASS" if ok else "FAIL",
    [
        f"values 0..19 returned, only 10..19 trained, resumed from ckpt {ck0}: {ok}",
        f"identity: run = '{rid}' (layer); DBOS workflow ids = {[s[0] for s in segs]} -- a NEW workflow id per extension",
        f"DBOS-native extension: start(same id, until=20) silently returned the old result={silently_ignored}; "
        f"resume on SUCCESS = no-op; fork = new id with the ORIGINAL inputs",
    ],
)
