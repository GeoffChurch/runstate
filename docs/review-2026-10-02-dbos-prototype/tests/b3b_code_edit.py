"""B3b: the run is killed, the code changes (DBOS application_version differs), a new process resumes.
DBOS dequeues/recovers only workflows whose application_version equals the running one.
"""

import time

from common import *

root = fresh_root("b3b")
import rs_dbos as r

premigrate(root)
rid = r.run_id(CONFIG)
a = spawn(root, 40, {"RS_STEP_SECONDS": "0.05", "RS_APPVER": "v1"}, tag="A")
wait_until(lambda: len(ckpt_log(root, rid)) >= 12)
kill9(a)
time.sleep(r.STALE + 0.5)
b = spawn(root, 40, {"RS_STEP_SECONDS": "0.05", "RS_APPVER": "v2"}, tag="B")
try:
    b.wait(15)
    hung = False
except Exception:
    hung = True
    b.kill()
st = q(
    root,
    "SELECT workflow_uuid, status, application_version, executor_id FROM workflow_status",
)
trained_by_b = [e["step"] for e in ckpt_log(root, rid) if e["pid"] == b.pid]
print(
    f"A (v1) killed at ckpt {ckpt(root, rid)}; B (v2) after 15 s: hung={hung}, trained={trained_by_b}; rows={st}"
)
print("B log tail:", [l[:140] for l in log_of(b).splitlines()[-3:]])
verdict_line(
    "B3b resume after a code edit (application_version change)",
    "FAIL" if hung and not trained_by_b else "PASS",
    [
        "a run started under one application_version is not recovered/dequeued by a process running another;",
        "the layer avoids it only by pinning application_version, which makes DBOS replay recorded steps against edited code",
    ],
)
