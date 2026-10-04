"""B7 observer: a process that started nothing, given only the SQLite file. Prints DBOS's view + the layer's."""

import json
import os
import sys
import time

db, rids = sys.argv[1], sys.argv[2:]
os.environ["RS_ROOT"] = os.path.dirname(db)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import rs_dbos as r

c = r._client()
now = time.time()
out = {}
for rid in rids:
    s = r._segments(c, rid)[-1]
    out[rid] = {
        "dbos_status": s.status,
        "dbos_updated_at_age_s": round(now - s.updated_at / 1000, 2),
        "dbos_executor_id": s.executor_id,
        "dbos_steps_recorded": len(c.list_workflow_steps(s.workflow_id)),
        "layer_verdict": r.verdict(rid),
        "layer_is_alive": r.is_alive(rid),
    }
print(json.dumps(out))
