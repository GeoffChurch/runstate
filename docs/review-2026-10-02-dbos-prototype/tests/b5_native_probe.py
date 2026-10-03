"""Helper for B5 (run as a subprocess): what do DBOS's own verbs do to a FINISHED workflow + a new target?"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import rs_dbos as r
from dbos import DBOS, SetWorkflowID
from sim import SimTrainer

rid, new_until = sys.argv[1], int(sys.argv[2])
wid = f"{rid}.0"
r.TRAINER = SimTrainer(r.ROOT)
r._launch()
out = {}
with SetWorkflowID(wid):
    out["start_same_id_new_until"] = DBOS.start_workflow(
        _t := r._train, rid, new_until, 10, False, False
    ).get_result()
out["stored_inputs_after_start"] = DBOS.get_workflow_status(wid).input
DBOS.resume_workflow(wid)
st = DBOS.get_workflow_status(wid)
out["resume_on_SUCCESS"] = {
    "status": st.status,
    "result": DBOS.retrieve_workflow(wid).get_result(),
}
h = DBOS.fork_workflow(wid, 1)
out["fork"] = {
    "new_id": h.get_workflow_id(),
    "inputs": DBOS.get_workflow_status(h.get_workflow_id()).input,
    "result": h.get_result(),
}
print("PROBE " + json.dumps(out, default=str), flush=True)
os._exit(0)
