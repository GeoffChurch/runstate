"""B1 single-spawn: two OS processes start the same run concurrently. Exactly one trains.

(a) fresh run, layer config (unique executor ids), both spawned at the same instant
(b) take-over race: the run's owner was kill -9'd; two processes race to take it over
(c) DBOS default config (executor_id 'local' for every process): P2 starts while P1 trains
"""

import os
import sys
import time

from common import *

UNTIL, ROUNDS = 40, int(os.environ.get("ROUNDS", "5"))


def trainers(root, rid):
    return sorted({e["pid"] for e in ckpt_log(root, rid)})


def round_a(i, cold=False):
    root = fresh_root(f"b1a{i}{'cold' if cold else ''}")
    import rs_dbos as r

    if not cold:
        premigrate(root)
    rid = r.run_id(CONFIG)
    ps = [spawn(root, UNTIL, tag="p1"), spawn(root, UNTIL, tag="p2")]
    for p in ps:
        p.wait(60)
    tr = trainers(root, rid)
    res = [l for p in ps for l in log_of(p).splitlines() if "RESULT" in l]
    steps = stream_steps(root)
    crashed = [p.tag for p in ps if "already exists" in log_of(p)]
    ok = (
        len(tr) == 1
        and sorted(set(steps)) == list(range(UNTIL))
        and len(steps) == UNTIL
        and len(res) == 2
    )
    return ok, [
        f"(a{i}{' COLD DB' if cold else ''}) migration-race crashes={crashed}",
        f"      trainers(pids writing ckpt)={tr} stream rows={len(steps)} results={res}",
        f"      status={status_rows(root)}",
    ]


def round_b(i):
    root = fresh_root(f"b1b{i}")
    import rs_dbos as r

    premigrate(root)
    rid = r.run_id(CONFIG)
    p0 = spawn(root, UNTIL, tag="p0")
    wait_until(lambda: len(ckpt_log(root, rid)) >= 8)
    kill9(p0)
    time.sleep(r.STALE + 0.5)
    before = len(ckpt_log(root, rid))
    ps = [spawn(root, UNTIL, tag="p1"), spawn(root, UNTIL, tag="p2")]
    for p in ps:
        p.wait(60)
    tr = sorted({e["pid"] for e in ckpt_log(root, rid)[before:]})
    res = [l for p in ps for l in log_of(p).splitlines() if "RESULT" in l]
    steps = stream_steps(root)
    ok = len(tr) == 1 and sorted(set(steps)) == list(range(UNTIL))
    return ok, [
        f"(b{i}) takeover trainers={tr} (dead p0={p0.pid}) stream rows={len(steps)} uniq={len(set(steps))} results={res}",
        f"      status={status_rows(root)}",
    ]


def round_c(i):
    root = fresh_root(f"b1c{i}")
    import rs_dbos as r

    premigrate(root)
    rid = r.run_id(CONFIG)
    env = {"RS_EXECUTOR_ID": "local", "RS_LOG": "INFO"}
    p1 = spawn(root, UNTIL, env, tag="p1")
    wait_until(lambda: len(ckpt_log(root, rid)) >= 5)
    p2 = spawn(root, UNTIL, env, tag="p2")
    for p in (p1, p2):
        p.wait(60)
    log = ckpt_log(root, rid)
    tr = sorted({e["pid"] for e in log})
    # interleaving: consecutive ckpt writes by different pids, and regressions of the step
    regress = [
        (a["pid"], a["step"], b["pid"], b["step"])
        for a, b in zip(log, log[1:])
        if b["step"] < a["step"]
    ]
    warn = [
        l.strip()[:160]
        for l in log_of(p1).splitlines()
        if "no longer owned" in l or "Recovering" in l
    ]
    warn2 = [
        l.strip()[:160]
        for l in log_of(p2).splitlines()
        if "Recovering" in l or "RESULT" in l
    ]
    steps = stream_steps(root)
    ok = len(tr) == 1
    return ok, [
        f"(c{i}) trainers={tr} p1={p1.pid} p2={p2.pid} ckpt regressions={regress[:3]} stream rows={len(steps)} uniq={len(set(steps))}",
        f"      p1 log: {warn[:3]}",
        f"      p2 log: {warn2[:3]}",
        f"      status={status_rows(root)}",
    ]


def round_d(i):
    """Two DIFFERENT runs, default executor id: does starting run 2's worker disturb live run 1?"""
    root = fresh_root(f"b1d{i}")
    import rs_dbos as r

    premigrate(root)
    c2 = dict(CONFIG, seed=99)
    rid1, rid2 = r.run_id(CONFIG), r.run_id(c2)
    env = {"RS_EXECUTOR_ID": "local", "RS_LOG": "INFO", "RS_STEP_SECONDS": "0.1"}
    p1 = spawn(root, UNTIL, env, tag="p1")
    wait_until(lambda: len(ckpt_log(root, rid1)) >= 5)
    p2 = spawn(root, UNTIL, env, tag="p2", config=c2)
    for p in (p1, p2):
        p.wait(90)
    run1_pids = [e["pid"] for e in ckpt_log(root, rid1)]
    switches = [(a, b) for a, b in zip(run1_pids, run1_pids[1:]) if a != b]
    ok = len(set(run1_pids)) == 1 and "no longer owned" not in log_of(p1)
    return ok, [
        f"(d{i}) run1 trained by pids={sorted(set(run1_pids))} (p1={p1.pid}, p2={p2.pid}) owner switches={switches}",
        f"      p1 displaced warning: {'no longer owned' in log_of(p1)}; p2 'Recovering': {[l[30:90] for l in log_of(p2).splitlines() if 'Recovering' in l]}",
    ]


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "0abcd"
    results = {}
    if "0" in which:
        cold = [round_a(i, cold=True) for i in range(ROUNDS)]
        for _, ev in cold:
            print("\n".join(ev), flush=True)
        results["cold"] = (
            f"{sum(1 for _, ev in cold if 'crashes=[]' not in ev[0])}/{ROUNDS} cold-DB rounds had a launch crash"
        )
    for v, fn in (("a", round_a), ("b", round_b), ("c", round_c), ("d", round_d)):
        if v not in which:
            continue
        oks = []
        for i in range(ROUNDS if v != "c" else 3):
            ok, ev = fn(i)
            oks.append(ok)
            print("\n".join(ev), flush=True)
        results[v] = f"{sum(oks)}/{len(oks)} rounds " + (
            "run1 undisturbed" if v == "d" else "exactly one trainer"
        )
    print("\nSUMMARY", results)
    a = results.get("a", "")
    b = results.get("b", "")
    verdict = (
        "PASS" if a.startswith(f"{ROUNDS}/") and b.startswith(f"{ROUNDS}/") else "FAIL"
    )
    verdict_line(
        "B1 single-spawn (layer config)",
        verdict,
        [
            f"(cold DB) {results.get('cold')}",
            f"(a) {a}",
            f"(b) {b}",
            f"(c, DBOS default executor id) {results.get('c')}",
            f"(d, default id, different run) {results.get('d')}",
        ],
    )
