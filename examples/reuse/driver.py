"""Reuse by content-addressed run_id (v0.2).

A small "grid" of training cells, each addressed by a content hash of its config
(the run_id recipe -- docs/specs/run-id-recipe.md). Re-running the grid *reuses*
completed cells: the run_id IS the address, so "already ran?" is just "does that
run's log show a completed terminal record?" (peek_terminal).

Subscription-driven: the cell emits `loss` only when asked, so the orchestrator
subscribes per-step to build the trace. (Eager logging is the degenerate case --
a cell that emits unconditionally, equivalently a per-step subscriber.)

In-process via ThreadLauncher + the memory backend, so reuse is self-contained:
we run the grid twice in one process and the second pass reuses. (Reuse across
*separate* invocations just needs a durable backend -- ThreadLauncher(backend=
"sqlite", root=...) or LocalLauncher.)

runstate ships NO Orchestrator -- this is application code composing the
substrate with the reference helpers. `sweep(resume=True)` automates exactly the
run-or-reuse loop below.
"""

import hashlib
import json
import math

from runstate import ThreadLauncher, Watcher, Worker, peek_terminal


# --- the run_id recipe (a pattern, not shipped code; ~one line) ----------------

def run_id(inputs: dict) -> str:
    """Content fingerprint of the inputs that determine the run's output. Use it
    as the run's identity; reuse = it already exists and finished. Own the
    partition: include exactly what determines your result (here, the config)."""
    canon = json.dumps(inputs, sort_keys=True, allow_nan=False)
    return hashlib.sha256(canon.encode()).hexdigest()[:16]


# --- the training cell (your code C) -------------------------------------------

def train(channel, *, config):
    """One cell: a deterministic `loss` curve from the config. Subscription-
    driven (set() each step; the Worker emits a `value` when a subscription is
    due) and cooperatively stoppable (the Worker drains control.stop at each safe
    point). Deterministic in (config, step) -- which is what makes reuse valid."""
    lr = config["lr"]
    with Worker(channel) as w:
        for step in w.steps(total=config["steps"]):
            w.set("loss", 5.0 * math.exp(-lr * step))


# --- the driver (run-or-reuse, content-addressed) ------------------------------

GRID = [{"lr": 0.1, "steps": 12}, {"lr": 0.3, "steps": 12}, {"lr": 0.9, "steps": 12}]


def run_or_reuse(config, launcher, watcher) -> str:
    rid = run_id(config)
    ch = launcher.open_channel(rid)
    prior = peek_terminal(ch)
    if prior is not None and prior.outcome == "completed":
        print(f"  {rid}  reused                      (lr={config['lr']})")
        return rid

    # pre-stage the loss subscription so the worker picks it up on its first tick
    ch.send({"every": {"step": 1}}, topic="control.subscribe", name="loss", request_id="obs")
    watcher.add(launcher.launch(rid, train, kwargs={"config": config}))

    n = 0  # count the streamed `loss` values, to show the live value plane

    def on_event(_rid, e):
        nonlocal n
        if e.topic == "value" and e.request_id == "obs":
            n += 1

    result = watcher.wait(rid, on_event=on_event)
    print(f"  {rid}  ran -> {result.outcome}, {n} loss values   (lr={config['lr']})")
    return rid


def main():
    launcher = ThreadLauncher()  # in-process thread + memory backend
    watcher = Watcher(poll_interval=0.005)

    print("first pass (cold -- everything runs):")
    rids = [run_or_reuse(c, launcher, watcher) for c in GRID]

    print("second pass (warm -- same configs, all reused):")
    for c in GRID:
        run_or_reuse(c, launcher, watcher)

    # the log IS the data plane: read a cell's loss trace straight back from it,
    # worker-independent (this would work even after the run is long over).
    ch = launcher.open_channel(rids[0])
    series = [(e.body["step"], e.body["value"]) for e in ch.read(topics=["value"]) if e.name == "loss"]
    head = ", ".join(f"{s}:{v:.3f}" for s, v in series[:4])
    print(f"\nhistory read of {rids[0]}: {len(series)} steps; loss[0:4] = {head} ...")


if __name__ == "__main__":
    main()
