"""B2 displaced writer.

A trains; A freezes itself (SIGSTOP) right after writing the value for step FREEZE and BEFORE writing
its checkpoint for step FREEZE+1 (the deterministic version of a SIGSTOP at an unlucky instant).
B takes the run over the way DBOS offers for a presumed-dead owner; then A is SIGCONTed.

takeover modes:
  layer   -- unique executor ids; B's run() sees a stale beacon and calls
             DBOS._recover_pending_workflows([A's executor id]) (what the admin API's recovery does)
  local   -- DBOS default: A and B share executor id 'local'; B's DBOS.launch() recovers A's PENDING work
  resume  -- an operator calls DBOSClient.resume_workflow(wid) (public API), then B is started
"""

import json
import os
import signal
import sys
import time

from common import *

FREEZE, UNTIL = 5, 30


def proc_state(pid):
    with open(f"/proc/{pid}/stat") as f:
        return f.read().split(")")[1].split()[0]


def one(mode, cont_after_steps):
    root = fresh_root(f"b2{mode}")
    import rs_dbos as r

    premigrate(root)
    rid = r.run_id(CONFIG)
    env = {"RS_STEP_SECONDS": "0.1", "RS_LOG": "INFO"}
    if mode == "local":
        env["RS_EXECUTOR_ID"] = "local"
    a = spawn(root, UNTIL, dict(env, RS_FREEZE_AT=str(FREEZE)), tag="A")
    assert wait_until(lambda: proc_state(a.pid) == "T", 30), "A never froze"
    t_freeze = time.time()
    if mode == "layer":
        time.sleep(r.STALE + 0.5)  # let A's beacon go stale
    if mode == "resume":
        r._client().resume_workflow(f"{rid}.0")
    b = spawn(root, UNTIL, env, tag="B")
    # let B make progress (or finish), then wake A
    if cont_after_steps == "done":
        b.wait(60)
    else:
        assert wait_until(
            lambda: any(
                e["pid"] == b.pid and e["step"] >= FREEZE + 1 + cont_after_steps
                for e in ckpt_log(root, rid)
            ),
            30,
        ), "B made no progress"
    disk_before = ckpt(root, rid)
    t_cont = time.time()
    os.kill(a.pid, signal.SIGCONT)
    time.sleep(0.3)
    disk_after_cont = ckpt(root, rid)
    for p in (a, b):
        p.wait(60)
    log = ckpt_log(root, rid)
    a_writes_after = [e["step"] for e in log if e["pid"] == a.pid and e["t"] > t_cont]
    b_steps = [e["step"] for e in log if e["pid"] == b.pid]
    b_regress = [(x, y) for x, y in zip(b_steps, b_steps[1:]) if y <= x]
    rows = [json.loads(v) for _, _, _, v in stream_rows(root)]
    a_vals_after = [v["step"] for v in rows if v["pid"] == a.pid and v["t"] > t_cont]
    dup_steps = sorted(
        {v["step"] for v in rows if sum(1 for w in rows if w["step"] == v["step"]) > 1}
    )
    alog = log_of(a)
    learned = [
        l.strip()[30:150]
        for l in alog.splitlines()
        if "no longer owned" in l or "RESULT" in l
    ]
    final_vals = r.values(rid)
    ev = [
        f"mode={mode}: A={a.pid} B={b.pid}; A froze after value step {FREEZE}; SIGCONT when B's ckpt reached {disk_before}",
        f"artifact plane: ckpt.json just before SIGCONT={disk_before}, 0.3s after={disk_after_cont}",
        f"A's checkpoint writes after SIGCONT (unfenced): steps {a_writes_after}",
        f"B's checkpoint sequence: {b_steps}  B regressions/rewinds={b_regress}",
        f"A's stream values accepted after SIGCONT (fenced): {a_vals_after}",
        f"stream rows={len(rows)} duplicate steps={dup_steps}; values() steps contiguous 0..{UNTIL-1}: {[v['step'] for v in final_vals] == list(range(UNTIL))}",
        f"A learned it was displaced? log: {learned}",
        f"status={status_rows(root)}",
    ]
    if cont_after_steps == "done":
        ev.append(
            f"B finished before SIGCONT; FINAL ckpt.json now={ckpt(root, rid)} (B had reached {UNTIL})"
        )
        t0 = time.time()
        os.environ["RS_STEP_SECONDS"] = "0.02"
        got = r.ensure(CONFIG, UNTIL + 10, worker=WORKER)
        later = [e["step"] for e in ckpt_log(root, rid) if e["t"] > t0]
        ev.append(
            f"then ensure(config, {UNTIL + 10}): new ckpt writes started at step {later[:1]} -> recomputed steps "
            f"{later[0] - 1 if later else None}..{UNTIL - 1} that were already done; returned {len(got)} values; "
            f"segments={[s[0] for s in status_rows(root)]}"
        )
    fenced = not a_vals_after
    artifact_safe = not a_writes_after
    return fenced, artifact_safe, ev


if __name__ == "__main__":
    modes = sys.argv[1:] or ["layer", "local", "resume"]
    summary = []
    for mode in modes:
        for cont in ((3, 12, "done") if mode == "layer" else (3, 12)):
            fenced, safe, ev = one(mode, cont)
            print("\n".join(ev) + "\n", flush=True)
            summary.append((mode, cont, fenced, safe))
    print(
        "SUMMARY (mode, B steps before SIGCONT, A's DB writes rejected, artifact plane untouched by A):"
    )
    for s in summary:
        print("  ", s)
    all_fenced = all(s[2] for s in summary)
    all_safe = all(s[3] for s in summary)
    verdict_line(
        "B2 displaced writer",
        "PASS" if all_fenced and all_safe else ("PARTIAL" if all_fenced else "FAIL"),
        [
            f"DB writes fenced in every round: {all_fenced}",
            f"checkpoint file untouched by displaced A: {all_safe}",
        ],
    )
