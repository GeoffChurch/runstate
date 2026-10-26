"""The migration round's corpus runner (scripts/migration_round.py; the plan's
Task 11; commits-and-lineage.md §7, P1-P4 and "Also").

The runner is run over temporary roots holding Stage 2's golden logs as 0.3.0
files, plus one log per P3 and P4 class the goldens do not hold, and each
difference is checked against the class it was built for. A deliberately
corrupted output reads unattributed, a symlinked duplicate is counted once, and
the runner streams: one run in memory at a time, each run's line written before
the next run is read."""

import gc
import importlib.util
import json
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import weakref
from pathlib import Path

import pytest

pytest.importorskip("jsonschema")

from runstate.channel.sqlite import _SCHEMA  # noqa: E402
from runstate.migrations import MigrationError, Row  # noqa: E402
from test_migration_v0_3_0_to_v0_4_0 import (  # noqa: E402
    BACKSTOP,
    DEAD,
    BACKSTOP_AT_5,
    BEATEN_AFTER,
    GOLDEN,
    NO_END,
    _beat,
    _crashed_in_step_1,
    _dated,
    _release,
    _started,
    _stopped,
    _value,
    r,
)

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "migration_round.py"


def _load():
    spec = importlib.util.spec_from_file_location("migration_round", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["migration_round"] = mod  # dataclasses resolve their module by name
    spec.loader.exec_module(mod)
    return mod


mr = _load()
P3 = mr.P3Class
P4 = mr.P4Class


def _write(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(_SCHEMA)
        conn.executemany(
            "INSERT INTO log (seq, topic, name, request_id, body, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def _outputs(out: Path):
    runs = [json.loads(x) for x in (out / "runs.jsonl").read_text().splitlines()]
    return {Path(x["path"]).stem: x for x in runs}, json.loads(
        (out / "summary.json").read_text()
    )


MAX_BYTES = 10**9


def _main(root, out, *, max_rows, max_bytes=MAX_BYTES):
    """The runner's CLI, as Task 12 will call it."""
    argv = [str(root), "--out", str(out), "--max-rows", str(max_rows)]
    return mr.main(argv + ["--max-bytes", str(max_bytes)])


def _run(tmp_path, logs, *, max_rows=10_000):
    """Each log as a 0.3.0 file under one root; the runner's outputs, by run."""
    root = tmp_path / "root"
    for rid, rows in logs.items():
        _write(root / "v0.3.0" / f"{rid}.db", rows)
    out = tmp_path / "out"
    assert _main(root, out, max_rows=max_rows) == 0
    return _outputs(out)


def _cells(run):
    """Each differing cell's classes, by (name, step)."""
    return {
        (c["name"], c["step"]): set(c["classes"]) for c in run["p3"]["differing_cells"]
    }


# ----- one log per P3 class the goldens do not hold ---------------------------------

# A beat 0 to 3 and was preempted; B resumed from A's step 1 and completed at 2.
# The merge keeps A's step 3, which the head's lineage never reached.
BEYOND = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "value", _value(0.9, 0, 2.0), name="loss"),
        r(3, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(4, "value", _value(0.8, 1, 3.0), name="loss"),
        r(5, "lifecycle.heartbeat", _beat(1, 0, 1, 3.0)),
        r(6, "value", _value(0.7, 2, 4.0), name="loss"),
        r(7, "lifecycle.heartbeat", _beat(2, 0, 1, 4.0)),
        r(8, "value", _value(0.6, 3, 5.0), name="loss"),
        r(9, "lifecycle.heartbeat", _beat(3, 0, 1, 5.0)),
        r(10, "lifecycle.stopped", _stopped(False, None, 3, 1, [], 6.0)),
        r(11, "lifecycle.started", _started("local://n1/12", 7.0)),
        r(12, "value", _value(0.71, 2, 8.0), name="loss"),
        r(13, "lifecycle.heartbeat", _beat(2, 0, 11, 8.0)),
        r(14, "lifecycle.stopped", _stopped(True, None, 2, 11, [], 9.0)),
    ]
)

# A wrote an eval at step 2; B resumed from A's step 1 and recomputed step 2
# without one. The merge keeps A's eval at a step the lineage revisited.
REVISITED = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "value", _value(0.9, 0, 2.0), name="loss"),
        r(3, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(4, "value", _value(0.8, 1, 3.0), name="loss"),
        r(5, "lifecycle.heartbeat", _beat(1, 0, 1, 3.0)),
        r(6, "value", _value(0.7, 2, 4.0), name="loss"),
        r(7, "value", _value(0.95, 2, 4.0), name="eval"),
        r(8, "lifecycle.heartbeat", _beat(2, 0, 1, 4.0)),
        r(9, "lifecycle.stopped", _stopped(False, None, 2, 1, [], 5.0)),
        r(10, "lifecycle.started", _started("local://n1/12", 6.0)),
        r(11, "value", _value(0.71, 2, 7.0), name="loss"),
        r(12, "lifecycle.heartbeat", _beat(2, 0, 10, 7.0)),
        r(13, "value", _value(0.6, 3, 8.0), name="loss"),
        r(14, "lifecycle.heartbeat", _beat(3, 0, 10, 8.0)),
        r(15, "lifecycle.stopped", _stopped(True, None, 3, 10, [], 9.0)),
    ]
)

# One step's metric written twice: its commit holds both.
SEVERAL = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "value", _value(0.9, 0, 2.0), name="loss"),
        r(3, "value", _value(0.8, 0, 2.5), name="loss"),
        r(4, "lifecycle.heartbeat", _beat(0, 0, 1, 3.0)),
        r(5, "lifecycle.stopped", _stopped(True, None, 0, 1, [], 4.0)),
    ]
)

LOADING_STATUS = {"phase": "LOADING"}
RUNNING_STATUS = {"phase": "RUNNING"}

# Two classes claim one cell: B resumed from A's step 1, wrote its loading status
# at step 0, a leading value, and its running status at step 2. Its first commit
# holds both, so the cell (status, 2) is a node holding several values, one of
# them a leading value.
SEVERAL_AND_LEADING = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "value", _value(LOADING_STATUS, 0, 1.5), name="status"),
        r(3, "value", _value(0.9, 0, 2.0), name="loss"),
        r(4, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(5, "value", _value(0.8, 1, 3.0), name="loss"),
        r(6, "lifecycle.heartbeat", _beat(1, 0, 1, 3.0)),
        r(7, "lifecycle.stopped", _stopped(False, None, 1, 1, [], 4.0)),
        r(8, "lifecycle.started", _started("local://n1/12", 5.0)),
        r(9, "value", _value(LOADING_STATUS, 0, 5.5), name="status"),
        r(10, "value", _value(RUNNING_STATUS, 2, 6.0), name="status"),
        r(11, "value", _value(0.7, 2, 6.0), name="loss"),
        r(12, "lifecycle.heartbeat", _beat(2, 0, 8, 6.0)),
        r(13, "lifecycle.stopped", _stopped(True, None, 2, 8, [], 7.0)),
    ]
)

# A beat 0 and 1; B claimed; A, displaced, beat 2 after B's claim; B, loading A's
# step-2 checkpoint, first beat 3. A's step 2 is no candidate (M1's candidacy
# rule), so B's first commit is a parentless resume.
PARENTLESS_BY_CANDIDACY = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "value", _value(0.9, 0, 2.0), name="loss"),
        r(3, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(4, "lifecycle.heartbeat", _beat(1, 0, 1, 3.0)),
        r(5, "lifecycle.started", _started("local://n2/22", 4.0)),
        r(6, "lifecycle.heartbeat", _beat(2, 0, 1, 5.0)),  # A's
        r(7, "value", _value(0.6, 3, 6.0), name="loss"),
        r(8, "lifecycle.heartbeat", _beat(3, 0, 5, 6.0)),
        r(9, "lifecycle.stopped", _stopped(True, None, 3, 5, [], 7.0)),
    ]
)

# A trailing gap (a known limit): A beat 0 and 1, wrote step 3's value with no
# step 2, and completed at step 3, so M5 synthesizes step 3 and nothing at 2; B
# resumed from it at 4. Every count above the gap is one short.
TRAILING_GAP = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "value", _value(0.9, 0, 2.0), name="loss"),
        r(3, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(4, "value", _value(0.8, 1, 3.0), name="loss"),
        r(5, "lifecycle.heartbeat", _beat(1, 0, 1, 3.0)),
        r(6, "value", _value(0.6, 3, 4.0), name="loss"),
        r(7, "lifecycle.stopped", _stopped(True, None, 3, 1, [], 5.0)),
        r(8, "lifecycle.started", _started("local://n1/12", 6.0)),
        r(9, "value", _value(0.5, 4, 7.0), name="loss"),
        r(10, "lifecycle.heartbeat", _beat(4, 0, 8, 7.0)),
        r(11, "lifecycle.stopped", _stopped(True, None, 4, 8, [], 8.0)),
    ]
)

# A value before every claim: no claim's, so no lineage holds it. The ledger
# rules that Task 14 names its P3 class if the corpus shows any; until then it
# reads unattributed in P3, and is P2's named exception.
BEFORE_EVERY_CLAIM = _dated(
    [
        r(1, "value", _value(0.9, 0, 1.0), name="loss"),
        r(2, "lifecycle.started", _started("local://n1/11", 2.0)),
        r(3, "lifecycle.heartbeat", _beat(0, 0, 2, 3.0)),
        r(4, "lifecycle.stopped", _stopped(True, None, 0, 2, [], 4.0)),
    ]
)

# ----- one log per P4 class the goldens do not hold ---------------------------------

# The reclaim tool's malformed release alone (M10): 0.3.0's verdict raises on
# it, a comparable value; 0.4.0 reads its death, preempted.
MALFORMED_ALONE = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(3, "lifecycle.stopped", _release("reclaimed: slurm TIMEOUT", [], 1)),
    ]
)

# The backstop naming no claim in a run nothing claimed (owner ruling c'): 0.3.0
# read errored, 0.4.0 reads the terminated it becomes, killed by its signal.
CLAIMLESS_BACKSTOP_UNCLAIMED = _dated(
    [
        r(
            1,
            "lifecycle.stopped",
            _stopped(
                False,
                "subprocess exited -9 with no terminal record",
                None,
                None,
                [],
                1.0,
            ),
        ),
    ]
)

# The goldens' cells, by hand: each golden not named here reads equal, cell by
# cell.
GOLDEN_CELLS = {
    "loading": {("status", 2): {P3.LEADING}},
    "errored": {("loss", 1): {P3.ERROR_EXIT}},
    "resumed_past_a_loading_crash": {("status", 0): {P3.LOADING}},
    "after_stopped": {("status", 0): {P3.M4}},
    "above": {("loss", 3): {P3.ABOVE}},
    "loading_retry_released": {("status", 0): {P3.LOADING}},
    "loading_retry_unended": {("status", 0): {P3.LOADING}},
    "loading_retry_at_its_resume_step": {("status", 2): {P3.LOADING}},
    "beatless_full_step": {
        ("loss", 0): {P3.PARENTLESS},
        ("loss", 2): {P3.LOADING},
        ("status", 2): {P3.LOADING},
        ("loss", 3): {P3.PARENTLESS},
    },
    "displacement_parent": {("loss", 2): {P3.SEVERAL}},
    "resumed_past_an_unrecorded_step": {
        ("loss", 0): {P3.PARENTLESS},
        ("loss", 1): {P3.PARENTLESS},
        ("loss", 3): {P3.PARENTLESS},
        ("loss", 4): {P3.PARENTLESS},
    },
    "doubly_displaced": {("loss", 3): {P3.PARENTLESS_BY_CANDIDACY}},
    "after_an_attributed_death": {("loss", 1): {P3.M4}},
    "reclaim_reported_later": {("loss", 2): {P3.UNREACHED_UNENDED}},
    "reclaim_of_10_23": {("loss", 2): {P3.UNREACHED_UNENDED}},
}

# The goldens' verdicts that differ, each by the rule that changes it (Task 10's
# P4 probe found these 13); every other golden's verdict is equal.
GOLDEN_VERDICTS = {
    "backstopped": {P4.RELEASE, P4.LATER_STOP},
    "late_backstop": {P4.BACKSTOP_TERMINATED},
    "later_error": {P4.LATER_STOP},
    "later_default": {P4.LATER_STOP},
    "later_release": {P4.LATER_STOP},
    "later_error_honored": {P4.LATER_STOP},
    "backstop_id_taken": {P4.BACKSTOP_TERMINATED},
    "claimless": {P4.CLAIMLESS},
    "claimless_error": {P4.CLAIMLESS},
    "claimless_release": {P4.CLAIMLESS},
    "wrapper": {P4.WRAPPER},
    "another_host": {P4.WRAPPER},
    "two_claims_one_launch": {P4.ATTRIBUTED_DEATH},
}


@pytest.fixture(scope="module")
def goldens(tmp_path_factory):
    return _run(tmp_path_factory.mktemp("goldens"), GOLDEN)


def test_every_golden_is_measured_clean(goldens):
    runs, _ = goldens
    assert set(runs) == set(GOLDEN)
    for rid, x in runs.items():
        assert x["status"] == "measured", rid
        assert (x["format"], x["address"]) == ("0.3.0", "v0.3.0"), rid
        assert x["refusal"] is None and x["crash"] is None, rid
        assert x["schema_invalid"] == [], rid
        assert not any(x["also"].values()), (rid, x["also"])
        assert x["p1"]["failures"] == {}, rid
        assert x["p1"]["checked"] == x["p1"]["passed"], rid
        assert x["p2"]["failures"] == {}, rid
        assert x["p3"]["not_compared"] is None, rid


@pytest.mark.parametrize("rid", list(GOLDEN))
def test_every_goldens_cells_are_attributed_to_their_class(goldens, rid):
    runs, _ = goldens
    assert _cells(runs[rid]) == GOLDEN_CELLS.get(rid, {})
    assert runs[rid]["p3"]["unattributed"] == 0


@pytest.mark.parametrize("rid", list(GOLDEN))
def test_every_goldens_verdict_and_stops(goldens, rid):
    runs, _ = goldens
    p4 = runs[rid]["p4"]
    assert p4["verdict"]["equal"] == (rid not in GOLDEN_VERDICTS)
    assert set(p4["verdict"]["classes"]) == GOLDEN_VERDICTS.get(rid, set())
    assert p4["stops"]["equal"] and p4["stops"]["classes"] == []


def test_a_leading_value_moves_and_an_equal_value_from_another_record_is_equal(
    goldens,
):
    """B's step-0 loading status goes to its first commit, at step 2; at step 0
    the lineage holds A's, an equal value from another record."""
    runs, _ = goldens
    p3 = runs["loading"]["p3"]
    assert p3["differing"] == 1 and p3["equal_other_record"] == 1


def test_the_summary_totals_the_runs(goldens):
    runs, summary = goldens
    assert summary["complete"] is True
    assert summary["runs"]["total"] == len(GOLDEN)
    assert summary["runs"]["status"] == {"measured": len(GOLDEN)}
    by_class = {}
    for x in runs.values():
        for c in x["p3"]["differing_cells"]:
            for k in c["classes"]:
                by_class[k] = by_class.get(k, 0) + 1
    assert summary["p3"]["by_class"] == by_class
    assert summary["p3"]["unattributed"] == 0
    assert summary["p3"]["differing"] == sum(len(v) for v in GOLDEN_CELLS.values())
    verdicts = {}
    for classes in GOLDEN_VERDICTS.values():
        for k in classes:
            verdicts[k] = verdicts.get(k, 0) + 1
    assert summary["p4"]["verdict"]["by_class"] == verdicts
    assert summary["p4"]["verdict"]["differing"] == len(GOLDEN_VERDICTS)
    assert summary["p4"]["stops"]["differing"] == 0
    assert summary["schema"]["invalid_rows"] == 0


# ----- each class, built once --------------------------------------------------------

CLASS_LOGS = {
    "beyond": (BEYOND, {("loss", 3): {P3.BEYOND}}),
    "revisited": (REVISITED, {("eval", 2): {P3.REVISITED}}),
    "several": (SEVERAL, {("loss", 0): {P3.SEVERAL}}),
    "several_and_leading": (
        SEVERAL_AND_LEADING,
        {("status", 2): {P3.SEVERAL, P3.LEADING}},
    ),
    "parentless_by_candidacy": (
        PARENTLESS_BY_CANDIDACY,
        {
            ("loss", 0): {P3.PARENTLESS_BY_CANDIDACY},
            ("loss", 3): {P3.PARENTLESS_BY_CANDIDACY},
        },
    ),
    "trailing_gap": (
        TRAILING_GAP,
        {
            ("loss", 2): {P3.TRAILING_GAP},
            ("loss", 3): {P3.TRAILING_GAP},
            ("loss", 4): {P3.TRAILING_GAP},
        },
    ),
    "released": (
        _crashed_in_step_1(BACKSTOP_AT_5),
        {("loss", 1): {P3.UNREACHED_UNENDED}},
    ),
    "unended": (_crashed_in_step_1(NO_END), {("loss", 1): {P3.UNREACHED_UNENDED}}),
}


@pytest.mark.parametrize("rid", list(CLASS_LOGS))
def test_each_p3_class_is_attributed_to_it(tmp_path, rid):
    rows, cells = CLASS_LOGS[rid]
    runs, summary = _run(tmp_path, {rid: rows})
    assert runs[rid]["status"] == "measured"
    assert _cells(runs[rid]) == cells
    assert runs[rid]["p3"]["unattributed"] == 0
    assert runs[rid]["schema_invalid"] == []


def test_a_cell_two_classes_claim_is_reported_as_both(tmp_path):
    runs, summary = _run(tmp_path, {"both": SEVERAL_AND_LEADING})
    assert summary["p3"]["by_class_set"] == {
        "+".join(sorted([P3.LEADING, P3.SEVERAL])): 1
    }
    assert summary["p3"]["by_class"] == {P3.LEADING: 1, P3.SEVERAL: 1}


def test_a_value_before_every_claim_has_no_p3_class_yet(tmp_path):
    """Ruling (progress.md, Task 3): Task 14 names its P3 class if the corpus
    shows any. Until then the cell is unattributed, with the reason the step
    left it uncommitted, and P2 names it."""
    runs, _ = _run(tmp_path, {"early": BEFORE_EVERY_CLAIM})
    x = runs["early"]
    assert _cells(x) == {("loss", 0): {P3.UNATTRIBUTED}}
    (cell,) = x["p3"]["differing_cells"]
    assert [u["seq"] for u in cell["unexplained"]] == [1]
    assert "before every claim" in cell["unexplained"][0]["fate"]
    assert x["p2"]["exceptions"] == {mr.P2Exception.BEFORE_EVERY_CLAIM: 1}


P4_LOGS = {
    "malformed_alone": (MALFORMED_ALONE, {P4.RELEASE}, "raised", ["preempted"]),
    "claimless_backstop_unclaimed": (
        CLAIMLESS_BACKSTOP_UNCLAIMED,
        {P4.BACKSTOP_TERMINATED},
        ["errored"],
        ["killed"],
    ),
}


@pytest.mark.parametrize("rid", list(P4_LOGS))
def test_each_p4_class_is_attributed_to_it(tmp_path, rid):
    rows, classes, old, new = P4_LOGS[rid]
    runs, _ = _run(tmp_path, {rid: rows})
    v = runs[rid]["p4"]["verdict"]
    assert set(v["classes"]) == classes
    assert (
        (v["old"]["kind"] == "raised")
        if old == "raised"
        else v["old"]["outcomes"] == old
    )
    assert v["new"]["outcomes"] == new


def test_a_0_3_0_read_that_raises_is_a_comparable_value(tmp_path):
    runs, _ = _run(tmp_path, {"m10": MALFORMED_ALONE})
    old = runs["m10"]["p4"]["verdict"]["old"]
    assert old["kind"] == "raised" and "lifecycle.stopped" in old["error"]


# ----- what fails attribution -------------------------------------------------------

# A worker's exit with one committed value and one honored stop.
EXIT_WITH_A_VALUE = _dated(
    [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "value", _value(0.9, 0, 2.0), name="loss"),
        r(3, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(4, "control.stop", {}, rid="halt"),
        r(5, "lifecycle.stopped", _stopped(False, None, 0, 1, ["halt"], 3.0)),
    ]
)


def test_a_deliberately_unexplained_difference_reads_unattributed(
    tmp_path, monkeypatch
):
    """The output loses the commit's value and the exit's honored stop, which
    no rule explains: the passes say the value is committed, the rows say it is
    not, and the stop is pending only after the migration."""
    real = mr._rows_0_4_0

    def corrupted(passes):
        out = []
        for x in real(passes):
            body = json.loads(x.body)
            if x.topic in ("lifecycle.commit", "lifecycle.stopped"):
                body["values"] = [] if body["values"] is not None else None
            if x.topic == "lifecycle.stopped":
                body["honored"] = []
            out.append(x._replace(body=json.dumps(body)))
        return out

    monkeypatch.setattr(mr, "_rows_0_4_0", corrupted)
    runs, summary = _run(tmp_path, {"exit": EXIT_WITH_A_VALUE})
    x = runs["exit"]
    assert _cells(x) == {("loss", 0): {P3.UNATTRIBUTED}}
    assert x["p3"]["unattributed"] == 1
    assert x["p2"]["failures"] == {mr.P2Failure.UNCOMMITTED_WITHOUT_A_REASON: 1}
    assert x["p4"]["stops"]["classes"] == [P4.UNATTRIBUTED]
    assert x["p4"]["stops"]["old"] == {} and x["p4"]["stops"]["new"] == {"halt": 4}
    assert summary["p3"]["runs_with_unattributed"] == 1
    assert summary["p4"]["stops"]["by_class"] == {P4.UNATTRIBUTED: 1}


def test_a_value_moved_to_where_a_rule_would_put_it_still_reads_unattributed(
    tmp_path, monkeypatch
):
    """The rows move A's step-0 status into B's first commit, where a leading
    value of B's would go: the cell's shape fits LEADING, but the passes gave
    the value to A's commit, so no rule explains it."""
    real = mr._rows_0_4_0

    def moved(passes):
        rows = real(passes)
        status = next(x.seq for x in rows if x.name == "status")  # A's, first
        out = []
        for x in rows:
            body = json.loads(x.body)
            if x.topic == "lifecycle.commit" and status in body["values"]:
                body["values"].remove(status)
            elif x.topic == "lifecycle.commit" and body["claim_seq"] != 1:
                body["values"].insert(0, status)  # B's first commit
            out.append(x._replace(body=json.dumps(body)))
        return out

    monkeypatch.setattr(mr, "_rows_0_4_0", moved)
    runs, _ = _run(tmp_path, {"loading": GOLDEN["loading"]})
    x = runs["loading"]
    assert P3.UNATTRIBUTED in _cells(x)[("status", 2)]
    assert x["p2"]["failures"] == {mr.P2Failure.ANOTHER_CLAIMS: 1}


def test_a_value_moved_off_the_lineage_reads_unattributed_not_revisited(
    tmp_path, monkeypatch
):
    """The rows move B's step-2 loss, on the head's lineage, into A's step-3
    commit, off it: the cell's shape fits a step the lineage revisited, but the
    passes gave the value to B's commit, so no rule explains it."""
    real = mr._rows_0_4_0

    def moved(passes):
        rows = real(passes)
        bodies = {x.seq: json.loads(x.body) for x in rows}
        (late,) = [
            x.seq for x in rows if x.name == "loss" and bodies[x.seq]["value"] == 0.71
        ]
        a_last = max(
            x.seq
            for x in rows
            if x.topic == "lifecycle.commit" and bodies[x.seq]["claim_seq"] == 1
        )
        out = []
        for x in rows:
            body = bodies[x.seq]
            if x.topic == "lifecycle.commit" and late in body["values"]:
                body["values"].remove(late)
            if x.seq == a_last:
                body["values"].append(late)
            out.append(x._replace(body=json.dumps(body)))
        return out

    monkeypatch.setattr(mr, "_rows_0_4_0", moved)
    runs, _ = _run(tmp_path, {"beyond": BEYOND})
    assert _cells(runs["beyond"])[("loss", 2)] == {P3.UNATTRIBUTED}


def test_a_new_read_that_raises_is_recorded_and_the_also_check_names_it(
    tmp_path, monkeypatch
):
    """The runner's detectors, tested on ground truth: this plants a step bug
    that once shipped, the placeholder rule for lone stops, which read every
    lone stop as its claim's exit. On a stop its claim beats after, the beat is
    then a commit the exit's parent names: the head fold raises, P3 is not
    compared, and the also check names the parent after its child."""
    import runstate.migrations.v0_3_0_to_v0_4_0 as step

    monkeypatch.setattr(
        step, "_lone_stop_rule", lambda stop, *, beaten_after: step._Exit(stop)
    )
    runs, summary = _run(tmp_path, {"placeholder": BEATEN_AFTER})
    x = runs["placeholder"]
    assert x["status"] == "measured"
    assert "MalformedRecordError" in x["p3"]["not_compared"]
    assert x["also"][mr.Also.PARENT_AT_OR_AFTER] == 1
    assert summary["p3"]["runs_not_compared"] == {"the new reads raise": 1}


# ----- P1 and P2 ------------------------------------------------------------------------


def test_p1_counts_a_parentless_resume_and_names_a_trailing_gap(tmp_path):
    runs, summary = _run(
        tmp_path,
        {"gap": TRAILING_GAP, "unrecorded": GOLDEN["resumed_past_an_unrecorded_step"]},
    )
    gap = runs["gap"]["p1"]
    assert (gap["checked"], gap["passed"]) == (3, 2)
    assert gap["failures"] == {mr.P1Failure.TRAILING_GAP: 1}
    assert [(f["seq"], f["step"], f["count"]) for f in gap["failing"]] == [(10, 4, 4)]
    unrecorded = runs["unrecorded"]["p1"]
    assert (unrecorded["checked"], unrecorded["passed"]) == (2, 2)
    assert unrecorded["parentless"] == {P3.PARENTLESS: 2}
    assert summary["p1"]["parentless"] == {P3.PARENTLESS: 2}


def test_p2_names_every_exception_by_its_kind(goldens):
    runs, summary = goldens
    E = mr.P2Exception
    assert runs["loading"]["p2"]["exceptions"] == {E.LEADING: 1}
    assert runs["errored"]["p2"]["exceptions"] == {E.ERROR_EXIT: 1}
    assert runs["above"]["p2"]["exceptions"] == {E.ABOVE_THE_LAST_STEP: 1}
    assert runs["after_stopped"]["p2"]["exceptions"] == {E.AFTER_ITS_END: 1}
    assert runs["after_an_attributed_death"]["p2"]["exceptions"] == {E.AFTER_ITS_END: 1}
    assert runs["loading_retry_released"]["p2"]["exceptions"] == {E.LOADING: 1}
    assert runs["skipped"]["p2"] | {"failing": []} == {
        "checked": 6,
        "at_its_step": 6,
        "exceptions": {},
        "failures": {},
        "failing": [],
    }
    assert summary["p2"]["failures"] == {}


def test_p2_names_a_released_claims_unreached_step(tmp_path):
    runs, _ = _run(tmp_path, {"released": _crashed_in_step_1(BACKSTOP_AT_5)})
    assert runs["released"]["p2"]["exceptions"] == {mr.P2Exception.UNREACHED_UNENDED: 1}


# ----- the census hooks (Task 13, the write-up) -----------------------------------------


def test_the_census_counts_lone_stops_by_signature(goldens):
    runs, summary = goldens
    # the backstop and the default body both name claim 1: neither is lone
    assert runs["backstopped"]["census"]["lone_stops"] == []
    (lone,) = runs["errored"]["census"]["lone_stops"]
    assert lone == {
        "seq": 5,
        "claim_seq": 1,
        "malformed": False,
        "error": "CUDA out of memory",
        "error_kind": "other",
        "completed": False,
        "final_step_null": False,
        "honored_empty": True,
        "t_minus_created_at": 0.0,
        "beats_after": 0,
        "values_after": 0,
        "latest_claim": True,
        "read_as": "exit",
    }
    (lone,) = runs["loading_retry_released"]["census"]["lone_stops"][1:2]
    assert (lone["error_kind"], lone["read_as"]) == ("backstop", "release")
    # the rule for lone stops: written later, beaten after, or the worker's own
    (lone,) = runs["reclaim_reported_later"]["census"]["lone_stops"]
    assert (lone["t_minus_created_at"], lone["read_as"]) == (-1500.0, "release")
    assert "t=before" in mr.LoneStop(**lone).signature()
    (lone,) = runs["beaten_after"]["census"]["lone_stops"]
    assert (lone["beats_after"], lone["read_as"]) == (1, "release")
    assert runs["beaten_after"]["census"]["beats_after_end"] == {"commit": 1}
    (lone,) = runs["cooperative"]["census"]["lone_stops"]
    assert (lone["honored_empty"], lone["read_as"]) == (False, "exit")
    assert sum(summary["census"]["lone_stops"].values()) == sum(
        len(x["census"]["lone_stops"]) for x in runs.values()
    )


def test_the_census_counts_the_known_limit_shapes(tmp_path):
    rows = [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "lifecycle.heartbeat", _beat(0, 0, 1, 2.0)),
        r(3, "lifecycle.stopped", _stopped(False, BACKSTOP, 0, 1, ["ghost"], 3.0)),
        r(4, "lifecycle.heartbeat", _beat(1, 0, 1, 4.0)),  # a beat after its end
        r(5, "lifecycle.heartbeat", _beat(None, 0, 1, 5.0)),
        r(6, "lifecycle.nak", {"reason": "malformed", "message": "?"}),
        r(7, "value", _value(1.0, 0, 6.0), name="loss"),
    ]
    runs, summary = _run(
        tmp_path,
        {"limits": _dated(rows), "candidacy": PARENTLESS_BY_CANDIDACY},
    )
    c = runs["limits"]["census"]
    assert c["beats_after_end"] == {"commit": 1, "beat": 1}
    assert c["withdrawals_of_unborne_ids"] == 1
    assert c["naks_without_request_id"] == 1
    assert runs["candidacy"]["census"]["parentless_by_candidacy"] == 1
    assert summary["census"]["parentless_by_candidacy"] == 1


def test_the_census_counts_whole_number_floats_and_null_names_in_refused_runs(
    tmp_path,
):
    rows = _dated(
        [
            r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
            r(2, "lifecycle.heartbeat", _beat(3.0, 0, 1, 2.0)),
            r(3, "value", _value(1.0, 0, 3.0)),  # no name
        ]
    )
    runs, summary = _run(tmp_path, {"floats": rows})
    x = runs["floats"]
    assert x["status"] == "refused"
    assert x["census"]["whole_number_floats"] == {"lifecycle.heartbeat.step": 1}
    assert x["census"]["null_name_values"] == 1
    assert summary["census"]["whole_number_floats"] == {"lifecycle.heartbeat.step": 1}


# ----- refusals ----------------------------------------------------------------------------


REFUSED = {
    "rewind": (
        [
            r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
            r(2, "lifecycle.heartbeat", _beat(1, 0, 1, 2.0)),
            r(3, "lifecycle.heartbeat", _beat(1, 0, 1, 3.0)),
        ],
        "M9: an in-claim rewind",
    ),
    "null_name": (
        [r(1, "value", _value(1.0, 0, 1.0))],
        "M9: a value with no name",
    ),
    "subscription": (
        [r(1, "control.subscribe", {"schedule": {}}, rid="s")],
        "M9: a subscription's record",
    ),
    "step_atom": (
        [r(1, "control.stop", {"from": {"step": 3}}, rid="h")],
        "M9: a stop holding a step atom",
    ),
    "unreadable": (
        [r(1, "lifecycle.started", {"handle": "local://n1/11"})],
        "an unreadable lifecycle.started",
    ),
    "supervisor_topic": (
        [r(1, "supervisor.note", {})],
        "a user's topic under supervisor.",
    ),
    "unclaimed_beat": (
        [r(1, "lifecycle.heartbeat", _beat(0, 0, 5, 1.0))],
        "a record naming a claim not before it",
    ),
}


@pytest.mark.parametrize("rid", list(REFUSED))
def test_each_refusal_is_mapped_to_its_kind_once(tmp_path, rid):
    rows, kind = REFUSED[rid]
    runs, summary = _run(tmp_path, {rid: rows})
    x = runs[rid]
    assert x["status"] == "refused"
    assert x["refusal"]["step"] == "0.3.0 -> 0.4.0"
    assert x["refusal"]["kind"] == kind, x["refusal"]["message"]
    assert summary["refusals"] == {"0.3.0 -> 0.4.0": {kind: 1}}


def _every_refusal():
    """Each input the step's own tests refuse, and one of each refusal those
    tables leave out."""
    import test_migration_v0_3_0_to_v0_4_0 as t

    cases = {}
    for table in ("UNREADABLE", "REFUSED", "HONORS_THE_EMPTY_ID", "SECOND_REAPS"):
        cases |= {f"{table}:{k}": v for k, v in getattr(t, table).items()}
    claim = r(1, "lifecycle.started", _started("local://n1/11", 1.0))
    cases |= {
        "a_name_0_4_0_pins_null": [claim._replace(name="n")],
        "a_beat_naming_no_claim": [r(1, "lifecycle.heartbeat", _beat(0, 0, 5, 1.0))],
        "a_watermark_naming_nothing": [
            claim,
            r(2, "lifecycle.heartbeat", _beat(0, 9, 1, 2.0)),
        ],
        "a_watermark_naming_a_commit": t.WATERMARK_ON_ANOTHER_CLAIMS_COMMIT,
        "a_watermark_on_a_dropped_stop": t._watermark_on(
            _stopped(False, None, 0, 1, [], 4.0)
        ),
        "a_whole_number_float": [
            claim,
            r(2, "lifecycle.heartbeat", _beat(3.0, 0, 1, 2.0)),
        ],
    }
    return cases


def test_every_refusal_the_step_raises_maps_to_one_known_kind():
    """The one boundary that reads a refusal's message: every refusal the step's
    tests know maps to exactly one pattern of the table, never to unknown, and
    every pattern of the table is reached."""
    from runstate.migrations.v0_3_0_to_v0_4_0 import run_passes

    table = mr._REFUSALS[mr.StepName.FROM_0_3_0]
    reached = set()
    for case, rows in _every_refusal().items():
        with pytest.raises(MigrationError) as refused:
            run_passes(rows)
        message = str(refused.value)
        matched = [i for i, (p, _) in enumerate(table) if re.search(p, message)]
        assert len(matched) == 1, (case, message, matched)
        reached |= set(matched)
        kind = mr.refusal_kind(mr.StepName.FROM_0_3_0, message)
        assert not kind.startswith("unknown") and "{" not in kind, (case, kind)
    assert reached == set(range(len(table)))


def test_a_shipped_step_that_breaks_preservation_is_its_refusal(tmp_path, monkeypatch):
    """``transform_checked`` holds the shipped step to keeping every seq and
    topic."""
    from runstate.migrations.v0_2_0_to_v0_3_0 import V0_2_0_to_V0_3_0

    real = V0_2_0_to_V0_3_0.transform

    def dropping(self, rows, **given):
        migrated = real(self, rows, **given)
        return migrated._replace(rows=migrated.rows[:-1])

    monkeypatch.setattr(V0_2_0_to_V0_3_0, "transform", dropping)
    root = tmp_path / "root"
    _write(root / "v0.2.0" / "old.db", _v0_2_0())
    out = tmp_path / "out"
    assert _main(root, out, max_rows=100) == 0
    runs, _ = _outputs(out)
    assert runs["old"]["refusal"]["kind"] == "a broken seq-and-topic preservation"


def test_a_root_that_does_not_exist_is_refused(tmp_path):
    with pytest.raises(SystemExit):
        _main(tmp_path / "nowhere", tmp_path / "out", max_rows=1)


def test_a_corrupt_log_is_counted_never_raised(tmp_path):
    """A log whose schema reads but whose rows do not is a crash at the read; one
    whose schema does not read is skipped as corrupt. The runs after them are
    still measured."""
    root = tmp_path / "root"
    rows = [
        r(i, "value", _value("x" * 200, 0, 1.0), name="loss") for i in range(1, 2000)
    ]
    for rid in ("a_rows", "b_schema"):
        _write(root / "v0.3.0" / f"{rid}.db", rows)
    data = bytearray((root / "v0.3.0" / "a_rows.db").read_bytes())
    data[4096 * 5 : 4096 * 6] = b"\xff" * 4096  # a data page
    (root / "v0.3.0" / "a_rows.db").write_bytes(bytes(data))
    truncated = (root / "v0.3.0" / "b_schema.db").read_bytes()[: 4096 * 3]
    (root / "v0.3.0" / "b_schema.db").write_bytes(truncated)
    _write(root / "v0.3.0" / "c_fine.db", EXIT_WITH_A_VALUE)
    out = tmp_path / "out"
    assert _main(root, out, max_rows=10000) == 0
    runs, summary = _outputs(out)
    assert runs["a_rows"]["status"] == "crashed"
    assert runs["a_rows"]["crash"]["stage"] == "read"
    assert runs["a_rows"]["rows"] == 1999
    assert "b_schema" not in runs
    assert summary["files"]["skipped"] == {mr.SkipWhy.CORRUPT: 1}
    assert runs["c_fine"]["status"] == "measured"


def test_an_unknown_refusal_is_its_own_kind_never_dropped(tmp_path, monkeypatch):
    import runstate.migrations.v0_3_0_to_v0_4_0 as step

    def refuse(rows):
        raise MigrationError("the record at seq 7 is new to this table")

    monkeypatch.setattr(step, "run_passes", refuse)
    runs, summary = _run(tmp_path, {"new": EXIT_WITH_A_VALUE})
    kind = "unknown: the record at seq N is new to this table"
    assert runs["new"]["refusal"]["kind"] == kind
    assert (
        runs["new"]["refusal"]["message"] == "the record at seq 7 is new to this table"
    )
    assert summary["refusals"] == {"0.3.0 -> 0.4.0": {kind: 1}}


def test_a_step_that_crashes_is_recorded_not_raised(tmp_path, monkeypatch):
    import runstate.migrations.v0_3_0_to_v0_4_0 as step

    def crash(rows):
        raise AssertionError("records after Input(seq=3) share a tie")

    monkeypatch.setattr(step, "run_passes", crash)
    runs, summary = _run(tmp_path, {"crash": EXIT_WITH_A_VALUE})
    assert runs["crash"]["status"] == "crashed"
    assert runs["crash"]["crash"]["stage"] == "0.3.0 -> 0.4.0"
    assert summary["crashes"] == {
        "0.3.0 -> 0.4.0": {
            "AssertionError in crash: records after Input(seq=N) share a tie": 1
        }
    }


def test_the_runner_writes_the_steps_own_rows():
    """The runner runs the passes once and writes their records, as
    ``transform`` does."""
    from runstate.migrations.ambiguity import parse_choices, resolutions
    from runstate.migrations.v0_3_0_to_v0_4_0 import V0_3_0_to_V0_4_0, run_passes

    step = V0_3_0_to_V0_4_0()
    given = resolutions(
        step, parse_choices([step], assume=[], resolve=[], accept=[], cap=1)
    )
    for rows in GOLDEN.values():
        written = step.transform(rows, resolutions=given, history=()).rows
        assert mr._rows_0_4_0(run_passes(rows)) == written


# ----- the files ---------------------------------------------------------------------------


def _v0_2_0(rid_handle="local://n1/11"):
    """A 0.2.0 log, as master's worker wrote it: nothing names its claim."""
    return [
        Row(
            1,
            "lifecycle.started",
            None,
            None,
            json.dumps({"handle": rid_handle, "t": 1.0}),
            1.0,
        ),
        Row(
            2,
            "value",
            "loss",
            None,
            json.dumps({"value": 0.9, "step": 0, "t": 2.0}),
            2.0,
        ),
        Row(
            3,
            "lifecycle.heartbeat",
            None,
            None,
            json.dumps({"step": 0, "consumed_seq": 0, "t": 2.0}),
            2.0,
        ),
        Row(
            4,
            "lifecycle.stopped",
            None,
            None,
            json.dumps({"completed": True, "error": None, "final_step": 0, "t": 3.0}),
            3.0,
        ),
    ]


def test_the_format_is_read_from_the_address(tmp_path):
    """A legacy address and v0.2.0 are format 0.2.0, which the shipped step
    brings to 0.3.0; a 0.2.0 log it refuses is recorded as its refusal."""
    root = tmp_path / "root"
    _write(root / "cell" / "legacy.db", _v0_2_0())
    _write(root / "v0.2.0" / "old.db", _v0_2_0())
    _write(
        root / "v0.2.0" / "beatfirst.db",
        [
            Row(
                1,
                "lifecycle.heartbeat",
                None,
                None,
                '{"step":0,"consumed_seq":0,"t":1.0}',
                1.0,
            )
        ],
    )
    _write(root / "v0.3.0" / "new.db", EXIT_WITH_A_VALUE)
    out = tmp_path / "out"
    assert _main(root, out, max_rows=100) == 0
    runs, summary = _outputs(out)
    assert {k: (x["address"], x["format"], x["status"]) for k, x in runs.items()} == {
        "legacy": ("legacy", "0.2.0", "measured"),
        "old": ("v0.2.0", "0.2.0", "measured"),
        "beatfirst": ("v0.2.0", "0.2.0", "refused"),
        "new": ("v0.3.0", "0.3.0", "measured"),
    }
    assert runs["legacy"]["p3"]["differing"] == 0
    assert runs["beatfirst"]["refusal"]["step"] == "0.2.0 -> 0.3.0"
    assert runs["beatfirst"]["refusal"]["kind"] == "a heartbeat before every claim"
    assert summary["runs"]["format"] == {"0.2.0": 3, "0.3.0": 1}


def test_a_symlinked_duplicate_is_counted_once(tmp_path):
    root = tmp_path / "root"
    target = root / "v0.3.0" / "exit.db"
    _write(target, EXIT_WITH_A_VALUE)
    (root / "home").mkdir()
    (root / "home" / "exit.db").symlink_to(target)  # a file symlink
    (root / "cell").symlink_to(root / "v0.3.0", target_is_directory=True)
    (root / "loop").symlink_to(root, target_is_directory=True)  # a cycle
    out = tmp_path / "out"
    assert _main(root, out, max_rows=100) == 0
    runs, summary = _outputs(out)
    assert list(runs) == ["exit"]
    assert runs["exit"]["path"] == str(target.resolve())
    assert summary["files"]["unique"] == 1
    assert summary["files"]["paths"] == summary["files"]["duplicate_paths"] + 1
    assert summary["files"]["duplicate_paths"] >= 2


def test_files_that_are_not_logs_are_skipped_and_counted(tmp_path):
    root = tmp_path / "root"
    _write(root / "v0.3.0" / "run.db", EXIT_WITH_A_VALUE)
    _write(root / "v0.2.0" / "run.db", _v0_2_0())
    (root / "v0.2.0" / "run.db").chmod(0o444)  # sealed by the migration that moved it
    (root / "cell").mkdir()
    (root / "cell" / "tomb.db").touch()  # a tombstone
    (root / "cell" / "text.db").write_text("not a database at all, " * 100)
    conn = sqlite3.connect(root / "cell" / "other.db")
    conn.execute("CREATE TABLE things (x)")
    conn.commit()
    conn.close()
    _write(root / "v0.4.0" / "future.db", EXIT_WITH_A_VALUE)
    (root / "cell" / "gone.db").symlink_to(root / "nowhere.db")
    out = tmp_path / "out"
    assert _main(root, out, max_rows=100) == 0
    runs, summary = _outputs(out)
    assert list(runs) == ["run"] and runs["run"]["format"] == "0.3.0"
    W = mr.SkipWhy
    assert summary["files"]["skipped"] == {
        W.SUPERSEDED: 1,
        W.EMPTY: 1,
        W.NOT_SQLITE: 1,
        W.NO_LOG_TABLE: 1,
        W.UNREGISTERED_FORMAT: 1,
        W.BROKEN_LINK: 1,
    }
    skipped = [json.loads(x) for x in (out / "skipped.jsonl").read_text().splitlines()]
    assert len(skipped) == 6
    assert {x["why"]: x["detail"] for x in skipped}[W.NOT_SQLITE] == "SQLITE_NOTADB"


def test_an_unsealed_log_beside_a_newer_one_is_a_conflict(tmp_path):
    """Two logs of one run, either of which may have been written last: neither
    is guessed at, and the newer is measured. A sealed older one is superseded
    (``runstate migrate`` seals the log it moves)."""
    root = tmp_path / "root"
    _write(root / "cell" / "run.db", _v0_2_0())  # onboarding skipped it
    _write(root / "cell" / "v0.2.0" / "run.db", _v0_2_0())
    _write(root / "home" / "old.db", _v0_2_0())
    (root / "home" / "old.db").chmod(0o444)
    _write(root / "home" / "v0.2.0" / "old.db", _v0_2_0())
    out = tmp_path / "out"
    assert _main(root, out, max_rows=100) == 0
    runs, summary = _outputs(out)
    assert {x["path"] for x in runs.values()} == {
        str((root / "cell" / "v0.2.0" / "run.db").resolve()),
        str((root / "home" / "v0.2.0" / "old.db").resolve()),
    }
    W = mr.SkipWhy
    assert summary["files"]["skipped"] == {W.CONFLICT: 1, W.SUPERSEDED: 1}


def test_the_runner_writes_nothing_under_a_root(tmp_path):
    root = tmp_path / "root"
    _write(root / "v0.3.0" / "exit.db", EXIT_WITH_A_VALUE)
    with pytest.raises(SystemExit):
        _main(root, root / "out", max_rows=100)
    assert sorted(p.name for p in root.rglob("*")) == ["exit.db", "v0.3.0"]


# ----- streaming and memory ----------------------------------------------------------------


def test_the_runner_streams_one_run_at_a_time(tmp_path, monkeypatch):
    """No run's rows outlive it, and each run's line is written before the next
    run is read."""
    root = tmp_path / "root"
    logs = {k: GOLDEN[k] for k in ("displaced", "loading", "skipped", "late_reap")}
    for rid, rows in logs.items():
        _write(root / "v0.3.0" / f"{rid}.db", rows)
    out = tmp_path / "out"
    real = mr._read_rows
    alive: list[weakref.ref] = []

    class Rows(list):
        pass

    def tracked(path):
        gc.collect()
        assert all(ref() is None for ref in alive), "an earlier run's rows outlive it"
        written = (out / "runs.jsonl").read_text().splitlines()
        assert len(written) == len(alive), "an earlier run's line is not written"
        rows = Rows(real(path))
        alive.append(weakref.ref(rows))
        return rows

    monkeypatch.setattr(mr, "_read_rows", tracked)
    assert _main(root, out, max_rows=100) == 0
    assert len(alive) == len(logs)


def test_max_rows_skips_a_run_without_loading_it(tmp_path, monkeypatch):
    root = tmp_path / "root"
    _write(root / "v0.3.0" / "small.db", EXIT_WITH_A_VALUE)  # 5 rows
    _write(root / "v0.3.0" / "big.db", GOLDEN["displaced"])  # 12 rows
    read = []
    real = mr._read_rows
    monkeypatch.setattr(mr, "_read_rows", lambda p: read.append(p.stem) or real(p))
    out = tmp_path / "out"
    assert _main(root, out, max_rows=5) == 0
    runs, summary = _outputs(out)
    assert read == ["small"]
    assert runs["big"]["status"] == "skipped_max_rows" and runs["big"]["rows"] == 12
    assert runs["big"]["p3"] is None
    assert summary["runs"]["status"] == {"measured": 1, "skipped_max_rows": 1}
    assert summary["args"]["max_rows"] == 5


def test_the_runner_runs_under_a_4gb_address_space_limit(tmp_path):
    root = tmp_path / "root"
    for rid in ("displaced", "loading", "late_reap"):
        _write(root / "v0.3.0" / f"{rid}.db", GOLDEN[rid])
    out = tmp_path / "out"
    command = " ".join(
        [
            "ulimit -v 4000000;",
            "exec",
            shlex.quote(sys.executable),
            shlex.quote(str(_SCRIPT)),
            shlex.quote(str(root)),
            "--out",
            shlex.quote(str(out)),
            "--max-rows",
            "1000",
            "--max-bytes",
            str(MAX_BYTES),
        ]
    )
    done = subprocess.run(
        ["bash", "-c", command], capture_output=True, text=True, timeout=300
    )
    assert done.returncode == 0, done.stderr
    runs, summary = _outputs(out)
    assert summary["runs"]["status"] == {"measured": 3}


# ----- fix round 1 ---------------------------------------------------------------------

# Claims that end in an exit with no parent: no commit, and no resume point.
NULL_PARENT_EXITS = {
    "errored": _dated(
        [
            r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
            r(2, "lifecycle.stopped", _stopped(False, "ImportError", None, 1, [], 3.0)),
        ]
    ),
    "preempted": _dated(
        [
            r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
            r(2, "lifecycle.stopped", _stopped(False, None, None, 1, [], 2.0)),
        ]
    ),
    "stepless_then_clean": _dated(
        [
            r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
            r(2, "value", _value("LOADING", None, 1.5), name="status"),
            r(3, "lifecycle.heartbeat", _beat(None, 0, 1, 2.0)),
            r(4, "lifecycle.stopped", _stopped(True, None, None, 1, [], 3.0)),
        ]
    ),
}


def test_an_exit_with_no_parent_is_measured(tmp_path):
    """Its lineage is the exit alone, with no commit: the empty chain."""
    runs, summary = _run(tmp_path, NULL_PARENT_EXITS)
    assert summary["runs"]["status"] == {"measured": 3}
    for rid, x in runs.items():
        assert x["p3"]["not_compared"] is None, rid
        assert x["p3"]["differing"] == 0 and x["p4"]["verdict"]["equal"], rid
        assert not any(x["also"].values()) and x["schema_invalid"] == [], rid


def test_a_failure_of_the_runners_own_measurement_is_recorded_and_the_run_goes_on(
    tmp_path, monkeypatch
):
    real = mr.p2
    calls = []

    def broken(*args):
        calls.append(1)
        if len(calls) == 1:
            raise KeyError(99)
        return real(*args)

    monkeypatch.setattr(mr, "p2", broken)
    runs, summary = _run(tmp_path, {"a": EXIT_WITH_A_VALUE, "b": EXIT_WITH_A_VALUE})
    assert runs["a"]["status"] == "crashed"
    assert runs["a"]["crash"]["stage"] == "the runner's own measurement"
    assert runs["a"]["crash"]["kind"] == "KeyError in broken: N"
    assert runs["b"]["status"] == "measured"
    assert summary["crashes"] == {
        "the runner's own measurement": {"KeyError in broken: N": 1}
    }
    assert summary["complete"] is True


def test_a_parentless_resume_with_a_candidate_reads_unattributed(tmp_path, monkeypatch):
    """The reviewer's probe: M1 made to drop a valid candidate. The root's class
    is tested against the candidates, not taken from M1, so nothing that rests
    on that root is attributed, and neither P1 nor the census counts it."""
    import runstate.migrations.v0_3_0_to_v0_4_0 as step

    real = step._resume_from

    def dropped(claims, beaten, i):
        x = real(claims, beaten, i)
        return step.Parentless() if isinstance(x, step.Resumed) else x

    monkeypatch.setattr(step, "_resume_from", dropped)
    runs, _ = _run(tmp_path, {"beyond": BEYOND})
    x = runs["beyond"]
    cells = _cells(x)
    assert len(cells) == 4 and all(c == {P3.UNATTRIBUTED} for c in cells.values())
    assert x["p1"]["parentless"] == {}
    assert x["p1"]["failures"] == {mr.P1Failure.UNSOUND_CHAIN: 1}
    assert x["census"]["parentless_by_candidacy"] == 0


def test_p1_checks_a_commit_with_no_chain(tmp_path, monkeypatch):
    """A commit whose parent follows it has no chain: a failure P1 counts among
    the checked."""
    real = mr._rows_0_4_0

    def late_parent(passes):
        rows = real(passes)
        commits = [x.seq for x in rows if x.topic == "lifecycle.commit"]
        out = []
        for x in rows:
            body = json.loads(x.body)
            if x.seq == commits[1]:  # step 1's, now naming the last commit
                body["parent"] = commits[-1]
            out.append(x._replace(body=json.dumps(body)))
        return out

    monkeypatch.setattr(mr, "_rows_0_4_0", late_parent)
    runs, _ = _run(tmp_path, {"skipped": GOLDEN["skipped"]})
    p1 = runs["skipped"]["p1"]
    assert p1["failures"] == {mr.P1Failure.NO_CHAIN: 3}  # steps 1, 3 and 5
    assert (p1["checked"], p1["passed"]) == (4, 1)


def test_max_bytes_skips_a_file_without_opening_it(tmp_path, monkeypatch):
    root = tmp_path / "root"
    _write(root / "v0.3.0" / "small.db", EXIT_WITH_A_VALUE)
    padded = [r(1, "value", _value("x" * 20_000, 0, 1.0), name="blob")]
    _write(root / "v0.3.0" / "big.db", padded)
    small = (root / "v0.3.0" / "small.db").stat().st_size
    assert (root / "v0.3.0" / "big.db").stat().st_size > small
    counted = []
    real = mr._count_rows
    monkeypatch.setattr(mr, "_count_rows", lambda p: counted.append(p.stem) or real(p))
    out = tmp_path / "out"
    assert _main(root, out, max_rows=100, max_bytes=small) == 0
    runs, summary = _outputs(out)
    assert counted == ["small"]
    assert runs["big"]["status"] == "skipped_max_bytes" and runs["big"]["rows"] is None
    assert summary["args"]["max_bytes"] == small


def test_a_run_too_big_to_read_is_a_crash_at_the_read(tmp_path, monkeypatch):
    real = mr._read_rows

    def read(path):
        if path.stem == "a":
            raise MemoryError()
        return real(path)

    monkeypatch.setattr(mr, "_read_rows", read)
    runs, _ = _run(tmp_path, {"a": EXIT_WITH_A_VALUE, "b": EXIT_WITH_A_VALUE})
    assert runs["a"]["status"] == "crashed" and runs["a"]["crash"]["stage"] == "read"
    assert runs["a"]["crash"]["kind"] == "MemoryError in read: "
    assert runs["b"]["status"] == "measured"


def test_the_summary_is_written_as_the_runner_goes_and_when_it_stops(
    tmp_path, monkeypatch
):
    """Rewritten every ``_SUMMARY_EVERY`` runs, and once more however the runner
    stops: an abort loses no totals, and says it is incomplete."""
    root = tmp_path / "root"
    for rid in ("a", "b", "c"):
        _write(root / "v0.3.0" / f"{rid}.db", EXIT_WITH_A_VALUE)
    out = tmp_path / "out"
    monkeypatch.setattr(mr, "_SUMMARY_EVERY", 1)
    real = mr.measure
    measured = []

    def stop_at_c(found, **kw):
        if found.path.stem == "c":
            summary = json.loads((out / "summary.json").read_text())
            assert summary["runs"]["total"] == 2 and summary["complete"] is False
            raise KeyboardInterrupt
        measured.append(found.path.stem)
        return real(found, **kw)

    monkeypatch.setattr(mr, "measure", stop_at_c)
    with pytest.raises(KeyboardInterrupt):
        _main(root, out, max_rows=100)
    summary = json.loads((out / "summary.json").read_text())
    assert measured == ["a", "b"]
    assert summary["runs"]["total"] == 2 and summary["complete"] is False


def test_0_3_0s_liveness_is_recorded_never_refused(tmp_path):
    """What ``runstate migrate`` would refuse as live: a claim no stop ended
    whose handle does not resolve dead, a foreign host's included."""
    dead = [r(1, "lifecycle.started", _started(DEAD, 1.0))]
    runs, summary = _run(
        tmp_path,
        {
            "ended": GOLDEN["exit"],
            "foreign": GOLDEN["doubly_displaced"],
            "dead": dead,
        },
    )
    assert {k: x["live_by_0_3_0"] for k, x in runs.items()} == {
        "ended": False,
        "foreign": True,
        "dead": False,
    }
    assert all(x["status"] == "measured" for x in runs.values())
    assert (summary["runs"]["live_by_0_3_0"], summary["runs"]["not_live_by_0_3_0"]) == (
        1,
        2,
    )


# ----- fix round 2 ---------------------------------------------------------------------


@pytest.mark.skipif(os.geteuid() == 0, reason="root searches any directory")
def test_a_path_discovery_cannot_read_is_skipped_with_its_reason(tmp_path):
    """A ``.db`` symlink and a directory symlink into a directory discovery may
    not search, and a directory it may not list: each an unreadable path, with
    its errno, and the logs beside them are measured."""
    root = tmp_path / "root"
    _write(root / "v0.3.0" / "aaa.db", EXIT_WITH_A_VALUE)
    _write(root / "v0.3.0" / "zzz.db", EXIT_WITH_A_VALUE)
    locked = tmp_path / "locked"
    _write(locked / "x.db", EXIT_WITH_A_VALUE)
    (locked / "sub").mkdir()
    (root / "v0.3.0" / "mmm.db").symlink_to(locked / "x.db")
    (root / "cell").mkdir()
    (root / "cell" / "link").symlink_to(locked / "sub", target_is_directory=True)
    _write(root / "shut" / "y.db", EXIT_WITH_A_VALUE)
    out = tmp_path / "out"
    locked.chmod(0o000)
    (root / "shut").chmod(0o000)
    try:
        assert _main(root, out, max_rows=100) == 0
    finally:
        locked.chmod(0o755)
        (root / "shut").chmod(0o755)
    runs, summary = _outputs(out)
    assert {k: x["status"] for k, x in runs.items()} == {
        "aaa": "measured",
        "zzz": "measured",
    }
    skipped = [json.loads(x) for x in (out / "skipped.jsonl").read_text().splitlines()]
    assert {Path(x["path"]).name: (x["why"], x["detail"]) for x in skipped} == {
        name: (mr.SkipWhy.UNREADABLE, "EACCES: Permission denied")
        for name in ("link", "mmm.db", "shut")
    }
    assert summary["files"]["skipped"] == {mr.SkipWhy.UNREADABLE: 3}
    assert summary["complete"] is True


def test_a_run_whose_record_cannot_begin_never_aborts(tmp_path, monkeypatch):
    """The reviewer's probe: the sidecar check refuses one run, every time. Its
    record is begun without I/O, so the crash is written, at the read."""
    real = mr._unread_sidecars

    def refused(path):
        if path.stem == "b":
            raise PermissionError(13, "denied")
        return real(path)

    monkeypatch.setattr(mr, "_unread_sidecars", refused)
    logs = {rid: EXIT_WITH_A_VALUE for rid in ("a", "b", "c")}
    runs, summary = _run(tmp_path, logs)
    assert {k: x["status"] for k, x in runs.items()} == {
        "a": "measured",
        "b": "crashed",
        "c": "measured",
    }
    assert runs["b"]["crash"]["stage"] == "read"
    assert summary["complete"] is True


def test_a_body_that_is_not_json_is_a_crash_of_the_read(tmp_path):
    """Whatever would read it first, a step or ``is_live``, and in either
    format."""
    claim = r(1, "lifecycle.started", _started("local://n1/11", 1.0))
    root = tmp_path / "root"
    _write(
        root / "v0.3.0" / "after_claim.db",
        [claim, Row(2, "lifecycle.stopped", None, None, "{not json", 2.0)],
    )
    _write(
        root / "v0.2.0" / "in_0_2_0.db",
        [Row(1, "lifecycle.started", None, None, "{not json", 1.0)],
    )
    out = tmp_path / "out"
    assert _main(root, out, max_rows=100) == 0
    runs, _ = _outputs(out)
    assert {k: (x["status"], x["crash"]["stage"]) for k, x in runs.items()} == {
        "after_claim": ("crashed", "read"),
        "in_0_2_0": ("crashed", "read"),
    }
    assert runs["after_claim"]["crash"]["kind"].startswith("JSONDecodeError")
