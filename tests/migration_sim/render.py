"""A history's 0.3.0 log: the rows its record-writing events append.

The rows are ``runstate.migrations.Row``s, the shape
``tests/test_migration_v0_3_0_to_v0_4_0.py`` builds: compact JSON bodies, and
each record's ``created_at`` its own. The bodies are what the 0.3.0 schemas pin
(lifecycle-v0.5, value-v0.2, subscription-v0.3, launcher-v0.4), filled as the
reference Worker fills them (``runstate/worker.py``):

- **``t``** is the record's seq, and so is ``created_at``: no 0.3.0 rule reads
  either, so one clock serves every writer;
- **a heartbeat's ``consumed_seq``** is the seq of the newest ``control.*``
  record before it, or 0: the reference Worker's drain cursor, which reads every
  control record up to its tick;
- **a worker's ``honored``** is every stop before its exit that no ``stopped``
  named before its claim: the reference Worker folds the answers once, at its
  claim, and drains every stop up to its exit (``_die``);
- **a third party's ``honored``**, under ``Honors.UNDISCHARGED``, is every stop
  no ``stopped`` has named yet (``undischarged_stops``, as mycooc's
  clear-staged-stop path reads it);
- **a value's payload** is ``"<name>@<step>"``, or ``"<name>"`` when stepless;
- **handles** are ``local://sim/<pid>``: claim c's process is pid 1000 + c; a
  wrapper launch's own process is pid 2000 + k, and an unclaimed launch's 3000 +
  k, k counting launches.
"""

from __future__ import annotations

import json
from typing import Any, NamedTuple, assert_never

from runstate.migrations import Row

from .history import (
    Beacon,
    Beat,
    Claim,
    Emit,
    Exit,
    Exited,
    History,
    Honors,
    Killed,
    Launched,
    ProcessEnd,
    Reaped,
    StopRequest,
    ThirdPartyFinal,
    ThirdPartyStop,
    Walk,
    walk,
)

HOST = "sim"


def claim_handle(claim: int) -> str:
    return f"local://{HOST}/{1000 + claim}"


class Rendered(NamedTuple):
    rows: tuple[Row, ...]
    seqs: tuple[int | None, ...]  # per event: the seq of the record it wrote, if any
    walk: Walk


def render(history: History) -> Rendered:
    """The 0.3.0 log of ``history``; raises ``NotConforming`` for a history no
    conforming writer produces."""
    w = walk(history)
    claimant_launches = {
        ev.launch: ev.claim
        for ev in history.events
        if isinstance(ev, Claim) and ev.launch is not None
    }
    launch_handles: dict[str, str] = {}
    rows: list[Row] = []
    seqs: list[int | None] = []
    claim_seqs: dict[int, int] = {}
    spent_at_claim: dict[int, frozenset[str]] = {}
    stops: dict[str, int] = {}  # stop id -> its seq
    honored: set[str] = set()  # every id a stopped has named so far
    control = 0  # the newest control record's seq
    progress: int | None = None  # the newest integer beat's step

    def append(
        topic: str, body: dict[str, Any], *, name: str | None, request_id: str | None
    ) -> int:
        seq = len(rows) + 1
        if "t" in body:
            body["t"] = float(seq)
        text = json.dumps(body, separators=(",", ":"))
        rows.append(Row(seq, topic, name, request_id, text, float(seq)))
        return seq

    for i, ev in enumerate(history.events):
        seq: int | None = None
        if isinstance(ev, Claim):
            seq = append(
                "lifecycle.started",
                {"handle": claim_handle(ev.claim), "t": 0.0},
                name=None,
                request_id=ev.launch,
            )
            claim_seqs[ev.claim] = seq
            spent_at_claim[ev.claim] = frozenset(honored)
        elif isinstance(ev, Emit):
            step = w.labels[i]
            payload = ev.name if step is None else f"{ev.name}@{step}"
            seq = append(
                "value",
                {"value": payload, "step": step, "t": 0.0},
                name=ev.name,
                request_id=None,
            )
        elif isinstance(ev, (Beat, Beacon)):
            step = w.labels[i]
            seq = append(
                "lifecycle.heartbeat",
                {
                    "step": step,
                    "consumed_seq": control,
                    "claim_seq": claim_seqs[ev.claim],
                    "t": 0.0,
                },
                name=None,
                request_id=None,
            )
            if step is not None:
                progress = step
        elif isinstance(ev, Exit):
            ids = sorted(set(stops) - spent_at_claim[ev.claim])
            seq = append(
                "lifecycle.stopped",
                _stopped(
                    ev.completed,
                    ev.error,
                    w.final_steps[i],
                    claim_seqs[ev.claim],
                    ids,
                ),
                name=None,
                request_id=None,
            )
            honored.update(ids)
        elif isinstance(ev, StopRequest):
            seq = append("control.stop", {}, name=None, request_id=ev.request_id)
            stops[ev.request_id] = seq
            control = seq
        elif isinstance(ev, ThirdPartyStop):
            ids = (
                sorted(set(stops) - honored) if ev.honors is Honors.UNDISCHARGED else []
            )
            final = progress if ev.final_step is ThirdPartyFinal.PROGRESS else None
            names = None if ev.names is None else claim_seqs[ev.names]
            seq = append(
                "lifecycle.stopped",
                _stopped(False, ev.error, final, names, ids),
                name=None,
                request_id=None,
            )
            honored.update(ids)
        elif isinstance(ev, Launched):
            launch_handles[ev.launch] = _launch_handle(
                ev, claimant_launches, len(launch_handles)
            )
            seq = append(
                "launcher.launched",
                {"handle": launch_handles[ev.launch], "status": "running", "t": 0.0},
                name=None,
                request_id=ev.launch,
            )
        elif isinstance(ev, Reaped):
            seq = append(
                "launcher.terminated",
                _terminated(ev.how),
                name=None,
                request_id=ev.launch,
            )
        seqs.append(seq)
    return Rendered(tuple(rows), tuple(seqs), w)


def _stopped(
    completed: bool,
    error: str | None,
    final_step: int | None,
    claim_seq: int | None,
    honored: list[str],
) -> dict[str, Any]:
    return {
        "completed": completed,
        "error": error,
        "final_step": final_step,
        "claim_seq": claim_seq,
        "honored": honored,
        "t": 0.0,
    }


def _terminated(how: ProcessEnd) -> dict[str, Any]:
    if isinstance(how, Killed):
        return {"reason": "killed", "exit_code": None, "signal": how.signal, "t": 0.0}
    if isinstance(how, Exited):
        return {"reason": "exited", "exit_code": how.code, "signal": None, "t": 0.0}
    assert_never(how)


def _launch_handle(ev: Launched, claimants: dict[str, int], k: int) -> str:
    if ev.wrapper:
        return f"local://{HOST}/{2000 + k}"
    claim = claimants.get(ev.launch)
    if claim is None:
        return f"local://{HOST}/{3000 + k}"
    return claim_handle(claim)


def records(rendered: Rendered) -> list[dict[str, Any]]:
    """The rows as envelope records, ``{seq, topic, name, request_id, body}``, the
    shape the schemas validate."""
    return [
        {
            "seq": r.seq,
            "topic": r.topic,
            "name": r.name,
            "request_id": r.request_id,
            "body": json.loads(r.body),
        }
        for r in rendered.rows
    ]
