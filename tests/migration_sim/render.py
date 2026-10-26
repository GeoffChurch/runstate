"""A history's 0.3.0 log: the rows its record-writing events append.

The rows are ``runstate.migrations.Row``s, the shape
``tests/test_migration_v0_3_0_to_v0_4_0.py`` builds: compact JSON bodies, and
each record's ``created_at`` its own. The bodies are what the 0.3.0 schemas pin
(lifecycle-v0.5, value-v0.2, subscription-v0.3, launcher-v0.4), filled as the
history's writers fill them:

- **``t``** is the record's seq, and so is ``created_at``: no generic 0.3.0 rule
  reads either, so one clock serves every writer;
- **a heartbeat's ``consumed_seq``** is its ``Watermark``: the newest control
  record before it (the reference Worker's drain cursor), the newest since its
  claim, or the newest record of any topic before it;
- **a worker's ``honored``** is the stops it took in at its drains: each beat
  or beacon, and its exit under ``Drain.EXIT`` (the reference Worker's
  ``_die``), not under ``Drain.TICKS``. A drain considers each stop once, at the
  first drain after it, and takes it unless an answer already spent it: under
  ``Fold.AT_CLAIM`` an answer before its claim (the reference Worker folds the
  answers once, at its claim: worker.py:85-118), under ``Fold.AT_DRAIN`` one
  before that drain (design-v0.2.md §6's drain);
- **a third party's ``honored``**, under ``Honors.UNDISCHARGED``, is every stop
  no ``stopped`` has named yet (``undischarged_stops``, as mycooc's
  clear-staged-stop path reads it);
- **a log born in format 0.2.0** (``Born.V0_2_0``) carries the names the 0.2.0
  -> 0.3.0 step filled positionally, not its writers' (reference-by-name.md §6):
  every ``stopped``'s ``honored`` is every stop since the previous ``stopped``,
  blind to author and body, and every heartbeat's and ``stopped``'s
  ``claim_seq`` is the latest claim before it (none for a ``stopped`` before
  any claim);
- **a value's payload** is ``"<name>@<step>"``, or ``"<name>"`` when stepless; a
  user record's body is ``{}``;
- **a handle** is its process's, ``local://<host>/<pid>``.
"""

from __future__ import annotations

import json
from typing import Any, NamedTuple, assert_never

from runstate.migrations import Row

from .history import (
    Beacon,
    Beat,
    Born,
    Claim,
    Drain,
    Emit,
    Exit,
    Exited,
    Fold,
    History,
    Honors,
    Killed,
    Launched,
    Process,
    ProcessEnd,
    Reaped,
    StopRequest,
    Stray,
    ThirdPartyValue,
    ThirdPartyFinal,
    ThirdPartyStop,
    UserRecord,
    Walk,
    Watermark,
    walk,
)


def handle(process: Process) -> str:
    return f"local://{process.host}/{process.pid}"


class Rendered(NamedTuple):
    rows: tuple[Row, ...]
    seqs: tuple[int | None, ...]  # per event: the seq of the record it wrote, if any
    honored: tuple[
        tuple[str, ...], ...
    ]  # per event: the ids a stopped it wrote honours
    walk: Walk


def render(history: History) -> Rendered:
    """The 0.3.0 log of ``history``; raises ``NotConforming`` for a history the
    model holds no conforming writer produces."""
    w = walk(history)
    rows: list[Row] = []
    seqs: list[int | None] = []
    honoured: list[tuple[str, ...]] = []
    claim_seqs: dict[int, int] = {}
    folds: dict[int, Fold] = {}
    spent_at_claim: dict[int, frozenset[str]] = {}
    considered: dict[int, set[str]] = {}  # claim -> the stops a drain of it has seen
    taken: dict[int, set[str]] = {}  # claim -> the stops it took in
    stops: dict[str, int] = {}  # stop id -> its seq
    named: set[str] = set()  # every id a stopped has named so far
    since: list[str] = []  # the stops since the newest stopped
    latest: int | None = None  # the newest started's seq
    positional = history.born is Born.V0_2_0
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

    def drain(claim: int) -> None:
        """A drain of claim ``claim``'s worker, before its record is written."""
        spent = spent_at_claim[claim] if folds[claim] is Fold.AT_CLAIM else named
        for x in stops:
            if x not in considered[claim]:
                considered[claim].add(x)
                if x not in spent:
                    taken[claim].add(x)

    def value(name: str, step: int | None) -> int:
        payload = name if step is None else f"{name}@{step}"
        body = {"value": payload, "step": step, "t": 0.0}
        return append("value", body, name=name, request_id=None)

    for i, ev in enumerate(history.events):
        seq: int | None = None
        ids: tuple[str, ...] = ()
        label = w.derived[i].label
        if isinstance(ev, Claim):
            seq = append(
                "lifecycle.started",
                {"handle": handle(ev.process), "t": 0.0},
                name=None,
                request_id=ev.launch,
            )
            claim_seqs[ev.claim] = seq
            latest = seq
            folds[ev.claim] = ev.fold
            spent_at_claim[ev.claim] = frozenset(named)
            considered[ev.claim], taken[ev.claim] = set(), set()
        elif isinstance(ev, Emit):
            seq = value(ev.name, label)
        elif isinstance(ev, (Stray, ThirdPartyValue)):
            seq = value(ev.name, ev.step)
        elif isinstance(ev, (Beat, Beacon)):
            drain(ev.claim)
            watermark = _watermark(
                ev.watermark, control, claim_seqs[ev.claim], len(rows)
            )
            seq = append(
                "lifecycle.heartbeat",
                {
                    "step": label,
                    "consumed_seq": watermark,
                    "claim_seq": latest if positional else claim_seqs[ev.claim],
                    "t": 0.0,
                },
                name=None,
                request_id=None,
            )
            if label is not None:
                progress = label
        elif isinstance(ev, Exit):
            if ev.honors is Drain.EXIT:
                drain(ev.claim)
            ids = tuple(sorted(since if positional else taken[ev.claim]))
            seq = append(
                "lifecycle.stopped",
                _stopped(
                    ev.completed,
                    ev.error,
                    w.derived[i].final_step,
                    latest if positional else claim_seqs[ev.claim],
                    ids,
                ),
                name=None,
                request_id=None,
            )
            named.update(ids)
            since = []
        elif isinstance(ev, StopRequest):
            seq = append("control.stop", {}, name=None, request_id=ev.request_id)
            stops[ev.request_id] = seq
            since.append(ev.request_id)
            control = seq
        elif isinstance(ev, ThirdPartyStop):
            if positional:
                ids = tuple(sorted(since))
            elif ev.honors is Honors.UNDISCHARGED:
                ids = tuple(sorted(set(stops) - named))
            final = progress if ev.final_step is ThirdPartyFinal.PROGRESS else None
            if positional:
                names = latest
            else:
                names = None if ev.names is None else claim_seqs[ev.names]
            seq = append(
                "lifecycle.stopped",
                _stopped(False, ev.error, final, names, ids),
                name=None,
                request_id=None,
            )
            named.update(ids)
            since = []
        elif isinstance(ev, Launched):
            seq = append(
                "launcher.launched",
                {"handle": handle(ev.process), "status": "running", "t": 0.0},
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
        elif isinstance(ev, UserRecord):
            seq = append(ev.topic, {}, name=None, request_id=None)
        seqs.append(seq)
        honoured.append(ids)
    return Rendered(tuple(rows), tuple(seqs), tuple(honoured), w)


def _watermark(convention: Watermark, control: int, claim_seq: int, tail: int) -> int:
    if convention is Watermark.CONTROL:
        return control
    if convention is Watermark.CLAIMED:
        return control if control > claim_seq else 0
    if convention is Watermark.TAIL:
        return tail
    assert_never(convention)


def _stopped(
    completed: bool,
    error: str | None,
    final_step: int | None,
    claim_seq: int | None,
    honored: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "completed": completed,
        "error": error,
        "final_step": final_step,
        "claim_seq": claim_seq,
        "honored": list(honored),
        "t": 0.0,
    }


def _terminated(how: ProcessEnd) -> dict[str, Any]:
    if isinstance(how, Killed):
        return {"reason": "killed", "exit_code": None, "signal": how.signal, "t": 0.0}
    if isinstance(how, Exited):
        return {"reason": "exited", "exit_code": how.code, "signal": None, "t": 0.0}
    assert_never(how)


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
