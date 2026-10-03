"""B9(e) helper (venv): DBOS's own mid-step stop -- an ASYNC step with preemptible=True, cancelled mid-step.
Can the step catch it to checkpoint at a safe point, and what is recorded?"""

import asyncio
import json
import os
import sys
import time

from dbos import DBOS, SetWorkflowID

db, WRITE = sys.argv[1], sys.argv[2] == "write"
DBOS(
    config={
        "name": "preempt",
        "system_database_url": f"sqlite:///{db}",
        "application_version": "v1",
        "log_level": "WARNING",
    }
)
events = []


@DBOS.step(preemptible=True)
async def long_chunk(start: int, end: int) -> int:
    k = start
    try:
        while k < end:
            await asyncio.sleep(0.1)  # one training step
            if WRITE:
                await DBOS.write_stream_async("loss", {"step": k, "t": time.time()})
            k += 1
        return k
    except BaseException as e:  # a "checkpoint here" hook would go here
        events.append(
            {"step_interrupted_by": type(e).__name__, "at_k": k, "t": time.time()}
        )
        raise


@DBOS.workflow()
async def train(until: int) -> int:
    k = 0
    while k < until:
        k = await long_chunk(k, min(k + 50, until))
    return k


async def main():
    with SetWorkflowID("preempt.0"):
        h = await DBOS.start_workflow_async(train, 100)
    await asyncio.sleep(1.2)
    t_cancel = time.time()
    await DBOS.cancel_workflow_async("preempt.0")
    try:
        await h.get_result()
    except BaseException as e:  # noqa
        events.append({"get_result_raised": type(e).__name__})
    await asyncio.sleep(2.0)
    st = await DBOS.get_workflow_status_async("preempt.0")
    steps = await DBOS.list_workflow_steps_async("preempt.0")
    print(
        "PREEMPT "
        + json.dumps(
            {
                "t_cancel": t_cancel,
                "events": events,
                "status": st.status,
                "mode": "stream writes in step" if WRITE else "no DB writes in step",
                "step_stop_latency_s": [
                    round(e["t"] - t_cancel, 2) for e in events if "t" in e
                ],
                "step_rows": [
                    (s["function_name"], s["output"], str(s["error"])[:60])
                    for s in steps
                ],
            }
        ),
        flush=True,
    )


DBOS.launch()
asyncio.run(main())
os._exit(0)
