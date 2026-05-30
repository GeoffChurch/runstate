# webapp-viewer — live monitoring UI for active runs

A standalone webapp that lists active runstate runs, tails their progress
in real time, and provides per-run stop buttons. Equivalent in spirit to
the `--status` table in mycooc's `run_experiment.py`, but as a browser UI
that auto-updates.

## What it provides

- **Discovery** — scan a `run_root` directory for subdirectories
  (`<root>/<run_id>/channel.db`). Each is a run.
- **Live tail** — for each run, query the SQLite `messages` table for
  `to_orchestrator` events with `id > last_seen`, push to browser via
  WebSocket / SSE.
- **Per-run UI** — current phase / latest progress metrics / running time.
- **Stop button** — opens an orchestrator-role Channel and calls
  `control.send_stop(ch)`. Works even if the original launcher script
  is gone (the protocol is stateless from the channel's perspective).

## Constraints

- **SqliteChannel only.** FileChannel deletes messages on consumption, so
  a read-only tailer can't see what the original orchestrator already
  read. The webapp documentation must say "use sqlite backend for runs
  you want to monitor with the webapp."
- **Read-only history**, mostly. The webapp queries SQLite directly
  (bypassing the Channel API for reads) so it doesn't compete with the
  original orchestrator for messages. Writes (Stop button) go through a
  normal orchestrator-role Channel — multiple orchestrators on the same
  run is supported by the protocol.

## Sketch

```python
# FastAPI + SSE for the live update channel.
from fastapi import FastAPI, Path as PathParam
from fastapi.responses import EventSourceResponse
import sqlite3, json
from pathlib import Path
import runstate
from runstate import control

app = FastAPI()
RUNS_ROOT = Path(os.environ["RUNSTATE_RUNS_ROOT"])

@app.get("/runs")
def list_runs():
    return [d.name for d in RUNS_ROOT.iterdir()
            if (d / "channel.db").exists()]

@app.get("/runs/{run_id}/events")
async def events(run_id: str, since: int = 0):
    """SSE stream of new to_orchestrator events for the run."""
    db = sqlite3.connect(str(RUNS_ROOT / run_id / "channel.db"))
    async def gen():
        last_id = since
        while True:
            rows = db.execute(
                "SELECT id, payload FROM messages "
                "WHERE direction='to_orchestrator' AND id > ? ORDER BY id",
                (last_id,)
            ).fetchall()
            for row_id, payload in rows:
                yield {"data": payload}
                last_id = row_id
            await asyncio.sleep(0.5)
    return EventSourceResponse(gen())

@app.post("/runs/{run_id}/stop")
def stop(run_id: str):
    ch = runstate.open_channel(run_id, role="orchestrator",
                              root=str(RUNS_ROOT), backend="sqlite")
    cmd_id = control.send_stop(ch)
    ch.close()
    return {"command_id": cmd_id}
```

Plus a minimal frontend (vanilla JS + EventSource works).

## What it requires from the library

- `scan_runs(root) -> Iterator[str]` helper — convenience for discovery
  (currently the user does the directory iteration themselves)
- Maybe a `tail(run_id, root, since_id=0) -> Iterator[dict]` helper that
  queries SqliteChannel's `messages` table read-only (this exists in
  the SqliteChannel `iter_history()` method but needs a public,
  documented variant with a `since_id` cursor)

Neither is in v0.1; both are small additions.

## Status

Deferred from v0.1 implementation (2026-05-27). The architectural
constraints (SqliteChannel only; read-direct from `messages` table) are
in place; the webapp itself is a separate package that imports runstate.
