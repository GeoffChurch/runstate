"""B8 helper (venv): write one run with DBOS's DEFAULT serializer, for comparison with the layer's JSON one."""

import os
import sys

from dbos import DBOS, SetWorkflowID

db = sys.argv[1]
DBOS(
    config={
        "name": "default-ser",
        "system_database_url": f"sqlite:///{db}",
        "application_version": "v1",
        "log_level": "WARNING",
    }
)


@DBOS.step()
def chunk(k: int) -> dict:
    DBOS.write_stream("loss", {"step": k, "loss": 1.0 / (k + 1)})
    return {"step": k + 1}


@DBOS.workflow()
def train(until: int) -> dict:
    k = 0
    while k < until:
        k = chunk(k)["step"]
    DBOS.set_event("final", {"final_step": k})
    return {"final_step": k}


DBOS.launch()
with SetWorkflowID("defaultrun.0"):
    DBOS.start_workflow(train, 3).get_result()
os._exit(0)
