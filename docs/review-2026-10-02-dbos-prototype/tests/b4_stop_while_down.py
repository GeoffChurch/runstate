"""B4 stop while down.

  crash      -- run kill -9'd mid-chunk (segment PENDING, dead); stop(); start worker; start again
  crash+peek -- same, worker uses the layer's in-step stop peek (RS_PEEK=1)
  clean      -- run halted cleanly at its target (segment SUCCESS); stop(); start worker to a higher
                target; start again
  never      -- stop() before the run ever existed
  cancel     -- DBOS-native alternative: DBOSClient.cancel_workflow on the dead segment, then start
Pass: the first start after the stop halts at its first safe point (no new training), verdict is
resumable (preempted), and a later start is NOT stopped by the same request.
"""

import json
import time

from common import *

UNTIL = 40


def notif(root):
    return q(
        root,
        "SELECT destination_uuid, topic, consumed, consumed_by_function_id FROM notifications",
    )


def trained_by(root, rid, pid):
    return [e["step"] - 1 for e in ckpt_log(root, rid) if e["pid"] == pid]


def scenario(kind):
    root = fresh_root(f"b4{kind.replace('+', '')}")
    import rs_dbos as r

    premigrate(root)
    rid = r.run_id(CONFIG)
    env = {"RS_STEP_SECONDS": "0.05", "RS_CHUNK": "10"}
    if kind.endswith("peek"):
        env["RS_PEEK"] = "1"
    ev = []
    if kind == "never":
        ok = r.stop(rid)
        return False, [
            f"stop() before the run existed -> {ok} (DBOS send needs an existing destination "
            f"workflow; FK on notifications.destination_uuid)"
        ]
    if kind == "clean":
        a = spawn(root, 15, env, tag="A")
        a.wait(60)
    else:
        a = spawn(root, UNTIL, env, tag="A")
        wait_until(lambda: (ckpt(root, rid) or {}).get("step", 0) >= 14)
        kill9(a)
    ev.append(
        f"{kind}: A={a.pid} gone; ckpt={ckpt(root, rid)}; DBOS status={[(s[0], s[1]) for s in status_rows(root)]}; verdict={r.verdict(rid)}"
    )
    if kind == "cancel":
        r._client().cancel_workflow(f"{rid}.0")
        ev.append(
            f"  DBOSClient.cancel_workflow -> status={[(s[0], s[1]) for s in status_rows(root)]} verdict={r.verdict(rid)} (written as a verdict while nothing runs)"
        )
    else:
        ev.append(f"  stop({rid}) -> {r.stop(rid)}; notifications={notif(root)}")
    time.sleep(r.STALE + 0.5)
    ck0 = ckpt(root, rid)["step"]
    b = spawn(root, UNTIL, env, tag="B")
    b.wait(60)
    b_trained = trained_by(root, rid, b.pid)
    b_res = [l for l in log_of(b).splitlines() if "RESULT" in l]
    v_after_b = r.verdict(rid)
    ev.append(
        f"  B={b.pid} (worker started after the stop): result={b_res}; trained steps {b_trained} "
        f"(ckpt was {ck0}); verdict={v_after_b}; notifications={notif(root)}"
    )
    c = spawn(root, UNTIL, env, tag="C")
    c.wait(60)
    c_res = [l for l in log_of(c).splitlines() if "RESULT" in l]
    c_trained = trained_by(root, rid, c.pid)
    ev.append(
        f"  C={c.pid} (a later start): result={c_res}; trained {len(c_trained)} steps "
        f"({c_trained[:1]}..{c_trained[-1:]}); segments={[(s[0], s[1]) for s in status_rows(root)]}"
    )
    b_stopped = any('"stopped": true' in l for l in b_res)
    c_not_stopped = any(
        '"stopped": false' in l and '"final_step": 40' in l for l in c_res
    )
    first_safe_point = b_stopped and len(b_trained) == 0
    ok = first_safe_point and v_after_b == "preempted" and c_not_stopped
    ev.append(
        f"  -> B halted on the stop={b_stopped}, with no new training={len(b_trained) == 0} "
        f"({len(b_trained)} steps trained first); verdict preempted={v_after_b == 'preempted'}; later start not re-stopped={c_not_stopped}"
    )
    return ok, ev


if __name__ == "__main__":
    out = {}
    for kind in ("crash", "crash+peek", "clean", "never", "cancel"):
        ok, ev = scenario(kind)
        out[kind] = ok
        print("\n".join(ev) + "\n", flush=True)
    verdict_line(
        "B4 stop while down",
        (
            "PARTIAL"
            if any(out.values()) and not all(out.values())
            else ("PASS" if all(out.values()) else "FAIL")
        ),
        [f"{k}: {'PASS' if v else 'FAIL'}" for k, v in out.items()],
    )
