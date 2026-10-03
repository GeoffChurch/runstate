"""B9 stop at a safe point inside a long step.

DBOS step = 50 training steps x 0.1 s = 5 s. Stop is requested ~1 s into the first step.
  (a) layer stop() = DBOS message, read by recv between steps (native)
  (b) same + the layer's in-step peek (RS_PEEK=1, raw SQL on notifications)
  (c) chunk=1: every training step is its own DBOS step (native recv at every step)
  (d) DBOS-native cancel_workflow mid-step
  (e) DBOS-native preemptible ASYNC step, cancelled mid-step (standalone helper)
"""

import json
import os
import subprocess
import time

from common import *

UNTIL = 100


def scenario(tag, env, how):
    root = fresh_root(f"b9{tag}")
    import rs_dbos as r

    premigrate(root)
    rid = r.run_id(CONFIG)
    w = spawn(root, UNTIL, dict({"RS_STEP_SECONDS": "0.1"}, **env), tag="w")
    wait_until(lambda: (ckpt(root, rid) or {}).get("step", 0) >= 10)
    t_stop = time.time()
    if how == "stop":
        r.stop(rid)
    else:
        r._client().cancel_workflow(f"{rid}.0")
    status_at_stop = status_rows(root)[0][1]
    w.wait(60)
    log = ckpt_log(root, rid)
    last = max(e["t"] for e in log)
    res = [
        l
        for l in log_of(w).splitlines()
        if "RESULT" in l or "Error" in l or "rror:" in l
    ][-2:]
    after = [e["step"] for e in log if e["t"] > t_stop]
    nops = q(root, "SELECT count(*) FROM operation_outputs")[0][0]
    rows = [json.loads(v) for _, _, _, v in stream_rows(root)]
    ev = [
        f"({tag}) stop at ckpt step {after[0] - 1 if after else ckpt(root, rid)['step']}: training continued for "
        f"{len(after)} steps / {last - t_stop:.2f}s after the request; DBOS status right after the request={status_at_stop}",
        f"      worker: {res}; final ckpt={ckpt(root, rid)}; last value step={rows[-1]['step']}; verdict={r.verdict(rid)}",
        f"      operation_outputs rows={nops} for {len(rows)} training steps",
    ]
    return len(after), last - t_stop, ev


if __name__ == "__main__":
    out = {}
    for tag, env, how in (
        ("a", {"RS_CHUNK": "50"}, "stop"),
        ("b", {"RS_CHUNK": "50", "RS_PEEK": "1"}, "stop"),
        ("c", {"RS_CHUNK": "1"}, "stop"),
        ("d", {"RS_CHUNK": "50"}, "cancel"),
    ):
        n, dt, ev = scenario(tag, env, how)
        out[tag] = (n, round(dt, 2))
        print("\n".join(ev), flush=True)
    root = fresh_root("b9e")
    for mode in ("write", "nowrite"):
        p = subprocess.run(
            [
                PY,
                os.path.join(HERE, "b9_async_preemptible.py"),
                os.path.join(root, f"pre-{mode}.sqlite"),
                mode,
            ],
            capture_output=True,
            text=True,
            cwd=root,
            timeout=120,
        )
        pre = [l for l in p.stdout.splitlines() if l.startswith("PREEMPT ")]
        print(
            "(e) preemptible async step:",
            pre[0][8:] if pre else p.stdout[-800:] + p.stderr[-1500:],
        )
    verdict_line(
        "B9 stop inside a long DBOS step",
        "PARTIAL",
        [
            f"(a) native message+recv: {out['a'][0]} more steps, {out['a'][1]}s -- only at the step boundary",
            f"(b) + layer's in-step peek: {out['b'][0]} more steps, {out['b'][1]}s",
            f"(c) one DBOS step per training step: {out['c'][0]} more steps, {out['c'][1]}s",
            f"(d) cancel_workflow: {out['d'][0]} more steps, {out['d'][1]}s (abort at the next fenced write, verdict written first)",
            "(e) see the preemptible-async line above",
        ],
    )
