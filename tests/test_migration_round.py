"""The migration round's corpus runner (scripts/migration_round.py; the plan's
Tasks 11 and 22; commits-and-lineage.md §7, the divergences and "Also").

The runner is run over temporary roots holding the golden logs as 0.3.0 files,
under one configuration holding every golden's facts
(``tests.test_migration_goldens:GOLDENS_CONFIGURATION``) and the goldens' choosers.
Every golden's divergences fire as the goldens harness pins them, and every read
the migration changes is explained by one: none is unattributed. A deliberately
corrupted output reads unattributed, a fork no chooser decides leaves its run
undecided, a symlinked duplicate is counted once, and the runner streams: one run
in memory at a time, each run's line written before the next run is read."""

import importlib.util
import json
import os
import re
import shlex
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("jsonschema")

from runstate.channel.sqlite import _SCHEMA  # noqa: E402
from runstate.migrations import MigrationError, Row  # noqa: E402
from tests.migration_goldens import (  # noqa: E402
    BACKSTOP,
    DEAD,
    GOLDEN,
    _beat,
    _dated,
    _started,
    _stopped,
    _value,
    r,
)
from tests.test_migration_goldens import DIVERGED  # noqa: E402

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "migration_round.py"


def _load():
    spec = importlib.util.spec_from_file_location("migration_round", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["migration_round"] = mod  # dataclasses resolve their module by name
    spec.loader.exec_module(mod)
    return mod


mr = _load()


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
# The goldens' configuration: their facts, their choosers, and the limits.
CONFIG = [
    "--assume",
    "tests.test_migration_goldens:GOLDENS_CONFIGURATION",
    "--resolve",
    "span=fewest",
    "--resolve",
    "origin=newest",
    "--resolve",
    "end-time=lower",
    "--resolve",
    "record-claim=positional",
    "--resolve",
    "stop-honored=positional",
    "--resolve",
    "lease-bound=first-claim",
    "--cap",
    "3000",
    "--budget",
    "6000000",
    "--run-on",
    "2",
]


def _main(root, out, *, max_rows, max_bytes=MAX_BYTES, config=CONFIG):
    """The runner's CLI, as the corpus check calls it."""
    argv = [str(root), "--out", str(out), "--max-rows", str(max_rows)]
    return mr.main(argv + ["--max-bytes", str(max_bytes), *config])


def _run(tmp_path, logs, *, max_rows=10_000, config=CONFIG):
    """Each log as a 0.3.0 file under one root; the runner's outputs, by run."""
    root = tmp_path / "root"
    for rid, rows in logs.items():
        _write(root / "v0.3.0" / f"{rid}.db", rows)
    out = tmp_path / "out"
    assert _main(root, out, max_rows=max_rows, config=config) == 0
    return _outputs(out)


class Shared:
    """A list the runner's per-run child processes append to: a file, one item a
    line, since each run is measured in its own process."""

    def __init__(self, path):
        self.path = path
        path.write_text("")

    def append(self, item):
        with self.path.open("a") as f:
            f.write(f"{item}\n")

    def items(self):
        return self.path.read_text().splitlines()


def _unattributed(run):
    reads = run["reads"]
    return (
        reads["series"]["unattributed"],
        reads["verdict"]["divergences"] == ["unattributed"],
        reads["stops"]["divergences"] == ["unattributed"],
    )


# ----- the goldens -------------------------------------------------------------------


@pytest.fixture(scope="module")
def goldens(tmp_path_factory):
    return _run(tmp_path_factory.mktemp("goldens"), GOLDEN)


def test_every_golden_is_measured_clean(goldens):
    runs, _ = goldens
    assert set(runs) == set(GOLDEN)
    for rid, x in runs.items():
        assert x["status"] == "measured", (rid, x["refusal"], x["crash"])
        assert (x["format"], x["address"]) == ("0.3.0", "v0.3.0"), rid
        assert x["schema_invalid"] == [], rid
        assert not any(x["also"].values()), (rid, x["also"])
        assert x["reads"]["series"]["not_compared"] is None, rid
        assert x["lineage_check"] == {"raised": [], "last_disagrees": []}, rid
        assert x["census"] is not None, rid


@pytest.mark.parametrize("rid", list(GOLDEN))
def test_each_goldens_divergences_fire_as_pinned(goldens, rid):
    runs, _ = goldens
    want = {k: len(v) for k, v in DIVERGED[rid].items()}
    assert runs[rid]["divergences"] == want


@pytest.mark.parametrize("rid", list(GOLDEN))
def test_every_read_the_migration_changes_is_a_divergences(goldens, rid):
    """The divergences are complete on the goldens: each series cell, verdict and
    stop fold that differs is explained by one that fired."""
    runs, _ = goldens
    assert _unattributed(runs[rid]) == (0, False, False), runs[rid]["reads"]


def test_the_summary_totals_the_runs(goldens):
    runs, summary = goldens
    assert summary["complete"] is True
    assert summary["runs"]["total"] == len(GOLDEN)
    assert summary["runs"]["status"] == {"measured": len(GOLDEN)}
    fired = {}
    for x in runs.values():
        for k in x["divergences"]:
            fired[k] = fired.get(k, 0) + 1
    assert summary["divergences"] == fired
    assert summary["runs_unaccepted"] == sum(
        1 for x in runs.values() if x["unaccepted"]
    )
    assert summary["reads"]["series"]["unattributed"] == 0
    assert summary["schema"]["invalid_rows"] == 0
    assert summary["args"]["cap"] == 3000 and summary["args"]["run_on"] == 2


# ----- what fails attribution, and what waits for a chooser ------------------------------

# A worker's exit honouring a stop (a golden, so its facts are stated).
EXIT = GOLDEN["exit"]


def test_a_deliberately_unexplained_difference_reads_unattributed(
    tmp_path, monkeypatch
):
    """The output loses the commits' values and the exit's honored stop, which no
    divergence fired for: the series cells and the stop fold read unattributed."""
    import runstate.migrations.v0_3_0_to_v0_4_0 as step

    real = step.V0_3_0_to_V0_4_0.transform

    def corrupted(self, rows, **given):
        migrated = real(self, rows, **given)
        out = []
        for x in migrated.rows:
            body = json.loads(x.body)
            if x.topic in ("lifecycle.commit", "lifecycle.stopped"):
                body["values"] = [] if body["values"] is not None else None
            if x.topic == "lifecycle.stopped":
                body["honored"] = []
            out.append(x._replace(body=json.dumps(body)))
        return migrated._replace(rows=out)

    monkeypatch.setattr(step.V0_3_0_to_V0_4_0, "transform", corrupted)
    runs, summary = _run(tmp_path, {"values": GOLDEN["loading"], "stops": EXIT})
    values, stops = runs["values"], runs["stops"]
    assert values["status"] == stops["status"] == "measured"
    assert values["reads"]["series"]["unattributed"] == 3  # loss@0, loss@1, loss@2
    assert stops["reads"]["stops"]["divergences"] == ["unattributed"]
    assert stops["reads"]["stops"]["old"] == [] and stops["reads"]["stops"]["new"] == [
        "halt"
    ]
    assert summary["reads"]["series"]["runs_with_unattributed"] == 1


def test_a_fork_no_chooser_decides_leaves_its_run_undecided(tmp_path):
    """The goldens' configuration without its span and origin choosers: a run
    that forks there waits, its forks counted by projection, as `runstate
    migrate`'s detection pass would hold it."""
    config = [x for x in CONFIG if x not in ("span=fewest", "origin=newest")]
    config = [
        x
        for k, x in enumerate(config)
        if not (x == "--resolve" and config[k + 1].startswith("--"))
    ]
    runs, summary = _run(tmp_path, {"displaced": GOLDEN["displaced"]}, config=config)
    x = runs["displaced"]
    assert x["status"] == "undecided"
    assert x["undecided_on"] == {"0.3.0 -> 0.4.0": {"span": 1}}
    assert x["forks"]["0.3.0 -> 0.4.0"]["span"] == 1
    assert x["census"] is not None, "an undecided run keeps the census of its step"
    assert summary["undecided"] == {"0.3.0 -> 0.4.0": {"span": 1}}


def test_a_divergence_no_accept_names_is_measured_and_counted(goldens):
    """`runstate migrate` would refuse those runs; the runner measures them."""
    runs, summary = goldens
    x = runs["backstopped"]
    assert x["status"] == "measured"
    assert sorted(x["unaccepted"]) == ["third-party-note", "third-party-release"]


def test_a_bad_configuration_is_the_command_lines_error(tmp_path):
    with pytest.raises(SystemExit):
        _main(
            tmp_path,
            tmp_path / "out",
            max_rows=1,
            config=[*CONFIG, "--resolve", "span=nowhere"],
        )


# ----- refusals ----------------------------------------------------------------------------


REFUSED = {
    "rewind": (
        [
            r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
            r(2, "lifecycle.heartbeat", _beat(1, 0, 1, 2.0)),
            r(3, "lifecycle.heartbeat", _beat(1, 0, 1, 3.0)),
        ],
        "an in-claim rewind",
    ),
    "null_name": (
        [r(1, "value", _value(1.0, 0, 1.0))],
        "a value with no name",
    ),
    "subscription": (
        [r(1, "control.subscribe", {"schedule": {}}, rid="s")],
        "a subscription's record",
    ),
    "step_atom": (
        [r(1, "control.stop", {"from": {"step": 3}}, rid="h")],
        "a stop holding a step atom",
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


def _unbounded(t):
    """The step's horizon case's facts: the reference Worker with ``beat-cov`` and
    ``marker`` unstated, so each claim may run silent passes no record shows."""
    import dataclasses

    import runstate.migrations.v0_3_0_to_v0_4_0_facts as V

    ref = t.REFERENCE.workers["ref"]
    return t.REFERENCE._replace(
        workers={
            "ref": dataclasses.replace(
                ref,
                load=frozenset(V.Load) - {V.Load.OUTSIDE},
                marker=frozenset(V.Marker),
                beat_cov=frozenset(V.Covered),
            )
        }
    )


def _every_refusal():
    """Each input the step's own tests refuse at its boundary, and one of each
    refusal those tables leave out, with the facts that bring it about."""
    import test_migration_v0_3_0_to_v0_4_0 as t

    cases = {}
    for table in ("UNREADABLE", "REFUSED", "HONORS_THE_EMPTY_ID", "SECOND_REAPS"):
        cases |= {
            f"{table}:{k}": (v, t.REFERENCE) for k, v in getattr(t, table).items()
        }
    claim = r(1, "lifecycle.started", _started("local://n1/11", 1.0))
    launch = r(1, "launcher.launched", t._launched(DEAD, 0.5), rid="L1")
    cases |= {
        "a_name_0_4_0_pins_null": ([claim._replace(name="n")], t.REFERENCE),
        "a_beat_naming_no_claim": (
            [r(1, "lifecycle.heartbeat", _beat(0, 0, 5, 1.0))],
            t.REFERENCE,
        ),
        "a_watermark_naming_nothing": (
            [claim, r(2, "lifecycle.heartbeat", _beat(0, 9, 1, 2.0))],
            t.REFERENCE,
        ),
        "a_watermark_naming_a_commit": (
            t.WATERMARK_ON_ANOTHER_CLAIMS_COMMIT,
            t.REFERENCE,
        ),
        "a_watermark_on_a_dropped_stop": (
            t._watermark_on(_stopped(False, None, 0, 1, [], 4.0)),
            t.NOTES,
        ),
        "a_whole_number_float": (
            [claim, r(2, "lifecycle.heartbeat", _beat(3.0, 0, 1, 2.0))],
            t.REFERENCE,
        ),
        "a_launch_two_launched_name": (
            _dated(
                [
                    launch,
                    launch._replace(seq=2),
                    r(3, "lifecycle.started", _started(DEAD, 1.0), rid="L1"),
                    r(4, "launcher.terminated", t.REAP, rid="L1"),
                ]
            ),
            t.REFERENCE,
        ),
        "a_malformed_stopped_no_classifier_reads": (
            GOLDEN["reclaimed"],
            t.UNSTATED,
        ),
        "the_facts_admit_no_history": (
            [claim, r(2, "lifecycle.heartbeat", _beat(5, 0, 1, 2.0))],
            t.REFERENCE,
        ),
        "past_the_cap": ([claim], t.UNSTATED),
        "the_horizon": (t._a_claim_then_another(), _unbounded(t)),
    }
    return cases


def test_every_refusal_the_step_raises_maps_to_one_known_kind():
    """The one boundary that reads a refusal's message: every refusal the step's
    tests know maps to exactly one pattern of the table, never to unknown."""
    import test_migration_v0_3_0_to_v0_4_0 as t

    table = mr._REFUSALS[mr.StepName.FROM_0_3_0]
    reached = set()
    for case, (rows, configuration) in _every_refusal().items():
        with pytest.raises(MigrationError) as refused:
            t.migrated(rows, configuration, **t.CHOSEN)
        message = str(refused.value)
        matched = [i for i, (p, _) in enumerate(table) if re.search(p, message)]
        assert matched, (case, message)
        reached.add(matched[0])
        kind = mr.refusal_kind(mr.StepName.FROM_0_3_0, message)
        assert not kind.startswith("unknown") and "{" not in kind, (case, kind)
    budget = mr.refusal_kind(
        mr.StepName.FROM_0_3_0, "more than 3 states summed over the layers by ..."
    )
    assert budget == "past the budget"


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
    _write(root / "v0.3.0" / "c_fine.db", EXIT)
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

    def refuse(self, rows, **given):
        raise MigrationError("the record at seq 7 is new to this table")

    monkeypatch.setattr(step.V0_3_0_to_V0_4_0, "transform", refuse)
    runs, summary = _run(tmp_path, {"new": EXIT})
    kind = "unknown: the record at seq N is new to this table"
    assert runs["new"]["refusal"]["kind"] == kind
    assert (
        runs["new"]["refusal"]["message"] == "the record at seq 7 is new to this table"
    )
    assert summary["refusals"] == {"0.3.0 -> 0.4.0": {kind: 1}}


def test_a_step_that_crashes_is_recorded_not_raised(tmp_path, monkeypatch):
    import runstate.migrations.v0_3_0_to_v0_4_0 as step

    def crash(self, rows, **given):
        raise AssertionError("records after seq 3 share a tie")

    monkeypatch.setattr(step.V0_3_0_to_V0_4_0, "transform", crash)
    runs, summary = _run(tmp_path, {"crash": EXIT})
    assert runs["crash"]["status"] == "crashed"
    assert runs["crash"]["crash"]["stage"] == "0.3.0 -> 0.4.0"
    assert summary["crashes"] == {
        "0.3.0 -> 0.4.0": {
            "AssertionError in crash: records after seq N share a tie": 1
        }
    }


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
    _write(root / "v0.3.0" / "new.db", EXIT)
    out = tmp_path / "out"
    assert _main(root, out, max_rows=100) == 0
    runs, summary = _outputs(out)
    assert {k: (x["address"], x["format"], x["status"]) for k, x in runs.items()} == {
        "legacy": ("legacy", "0.2.0", "measured"),
        "old": ("v0.2.0", "0.2.0", "measured"),
        "beatfirst": ("v0.2.0", "0.2.0", "refused"),
        "new": ("v0.3.0", "0.3.0", "measured"),
    }
    assert runs["legacy"]["reads"]["series"]["differing"] == 0
    assert runs["beatfirst"]["refusal"]["step"] == "0.2.0 -> 0.3.0"
    assert runs["beatfirst"]["refusal"]["kind"] == "a heartbeat before every claim"
    assert summary["runs"]["format"] == {"0.2.0": 3, "0.3.0": 1}


def test_a_symlinked_duplicate_is_counted_once(tmp_path):
    root = tmp_path / "root"
    target = root / "v0.3.0" / "exit.db"
    _write(target, EXIT)
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
    _write(root / "v0.3.0" / "run.db", EXIT)
    _write(root / "v0.2.0" / "run.db", _v0_2_0())
    (root / "v0.2.0" / "run.db").chmod(0o444)  # sealed by the migration that moved it
    (root / "cell").mkdir()
    (root / "cell" / "tomb.db").touch()  # a tombstone
    (root / "cell" / "text.db").write_text("not a database at all, " * 100)
    conn = sqlite3.connect(root / "cell" / "other.db")
    conn.execute("CREATE TABLE things (x)")
    conn.commit()
    conn.close()
    _write(root / "v0.4.0" / "future.db", EXIT)
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
    _write(root / "v0.3.0" / "exit.db", EXIT)
    with pytest.raises(SystemExit):
        _main(root, root / "out", max_rows=100)
    assert sorted(p.name for p in root.rglob("*")) == ["exit.db", "v0.3.0"]


# ----- streaming and memory ----------------------------------------------------------------


def test_the_runner_streams_one_run_at_a_time(tmp_path, monkeypatch):
    """Each run is read in its own child process, so no run's rows ever reach the
    runner's own process, and each run's line is written before the next run is
    read."""
    root = tmp_path / "root"
    logs = {k: GOLDEN[k] for k in ("displaced", "loading", "skipped", "late_reap")}
    for rid, rows in logs.items():
        _write(root / "v0.3.0" / f"{rid}.db", rows)
    out = tmp_path / "out"
    reads = tmp_path / "reads"
    real = mr._read_rows
    parent = os.getpid()

    def tracked(path):
        written = len((out / "runs.jsonl").read_text().splitlines())
        with reads.open("a") as f:
            f.write(f"{os.getpid() != parent} {written}\n")
        return real(path)

    monkeypatch.setattr(mr, "_read_rows", tracked)
    assert _main(root, out, max_rows=100) == 0
    assert reads.read_text().splitlines() == [f"True {k}" for k in range(len(logs))]


def test_each_run_is_timed_and_its_peak_rss_recorded(goldens):
    runs, _ = goldens
    for record in runs.values():
        assert record["seconds"] > 0
        assert record["peak_rss_kb"] > 0


def test_a_child_the_os_kills_is_its_runs_crash(tmp_path, monkeypatch):
    root = tmp_path / "root"
    _write(root / "v0.3.0" / "killed.db", GOLDEN["displaced"])
    out = tmp_path / "out"
    monkeypatch.setattr(mr, "_read_rows", lambda path: os.kill(os.getpid(), 9))
    assert _main(root, out, max_rows=100) == 0
    (line,) = (out / "runs.jsonl").read_text().splitlines()
    record = json.loads(line)
    assert record["status"] == "crashed"
    assert record["crash"]["stage"] == "the runner's own measurement"
    assert record["crash"]["kind"] == "the child killed by signal 9"


def test_max_rows_skips_a_run_without_loading_it(tmp_path, monkeypatch):
    root = tmp_path / "root"
    _write(root / "v0.3.0" / "small.db", EXIT)  # 5 rows
    _write(root / "v0.3.0" / "big.db", GOLDEN["displaced"])  # 12 rows
    read = Shared(tmp_path / "read")
    real = mr._read_rows
    monkeypatch.setattr(mr, "_read_rows", lambda p: read.append(p.stem) or real(p))
    out = tmp_path / "out"
    assert _main(root, out, max_rows=5) == 0
    runs, summary = _outputs(out)
    assert read.items() == ["small"]
    assert runs["big"]["status"] == "skipped_max_rows" and runs["big"]["rows"] == 12
    assert runs["big"]["reads"] is None
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
            *(shlex.quote(x) for x in CONFIG),
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
        assert x["reads"]["series"]["not_compared"] is None, rid
        assert x["reads"]["verdict"]["equal"], rid
        assert not any(x["also"].values()) and x["schema_invalid"] == [], rid


def test_a_failure_of_the_runners_own_measurement_is_recorded_and_the_run_goes_on(
    tmp_path, monkeypatch
):
    real = mr.compare
    calls = Shared(tmp_path / "calls")

    def broken(*args):
        calls.append(1)
        if len(calls.items()) == 1:
            raise KeyError(99)
        return real(*args)

    monkeypatch.setattr(mr, "compare", broken)
    runs, summary = _run(tmp_path, {"a": EXIT, "b": EXIT})
    assert runs["a"]["status"] == "crashed"
    assert runs["a"]["crash"]["stage"] == "the runner's own measurement"
    assert runs["a"]["crash"]["kind"] == "KeyError in broken: N"
    assert runs["b"]["status"] == "measured"
    assert summary["crashes"] == {
        "the runner's own measurement": {"KeyError in broken: N": 1}
    }
    assert summary["complete"] is True


def test_max_bytes_skips_a_file_without_opening_it(tmp_path, monkeypatch):
    root = tmp_path / "root"
    _write(root / "v0.3.0" / "small.db", EXIT)
    padded = [r(1, "value", _value("x" * 20_000, 0, 1.0), name="blob")]
    _write(root / "v0.3.0" / "big.db", padded)
    small = (root / "v0.3.0" / "small.db").stat().st_size
    assert (root / "v0.3.0" / "big.db").stat().st_size > small
    counted = Shared(tmp_path / "counted")
    real = mr._count_rows
    monkeypatch.setattr(mr, "_count_rows", lambda p: counted.append(p.stem) or real(p))
    out = tmp_path / "out"
    assert _main(root, out, max_rows=100, max_bytes=small) == 0
    runs, summary = _outputs(out)
    assert counted.items() == ["small"]
    assert runs["big"]["status"] == "skipped_max_bytes" and runs["big"]["rows"] is None
    assert summary["args"]["max_bytes"] == small


def test_a_run_too_big_to_read_is_a_crash_at_the_read(tmp_path, monkeypatch):
    real = mr._read_rows

    def read(path):
        if path.stem == "a":
            raise MemoryError()
        return real(path)

    monkeypatch.setattr(mr, "_read_rows", read)
    runs, _ = _run(tmp_path, {"a": EXIT, "b": EXIT})
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
        _write(root / "v0.3.0" / f"{rid}.db", EXIT)
    out = tmp_path / "out"
    monkeypatch.setattr(mr, "_SUMMARY_EVERY", 1)
    real = mr.measure
    measured = Shared(tmp_path / "measured")

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
    assert measured.items() == ["a", "b"]
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
    _write(root / "v0.3.0" / "aaa.db", EXIT)
    _write(root / "v0.3.0" / "zzz.db", EXIT)
    locked = tmp_path / "locked"
    _write(locked / "x.db", EXIT)
    (locked / "sub").mkdir()
    (root / "v0.3.0" / "mmm.db").symlink_to(locked / "x.db")
    (root / "cell").mkdir()
    (root / "cell" / "link").symlink_to(locked / "sub", target_is_directory=True)
    _write(root / "shut" / "y.db", EXIT)
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
    logs = {rid: EXIT for rid in ("a", "b", "c")}
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


def test_a_verdict_difference_is_explained_only_by_a_site_in_the_latest_episode():
    """The verdict reads the latest claim's episode: a verdict divergence that fired
    in an earlier claim does not explain a difference in it."""
    fired = {d.name: () for d in mr._VERDICT}
    fired["attributed-death"] = (5,)
    assert mr._explained(False, fired, mr._VERDICT, since=4) == ["attributed-death"]
    assert mr._explained(False, fired, mr._VERDICT, since=6) == [mr.UNATTRIBUTED]
    assert mr._explained(True, fired, mr._VERDICT, since=6) == []


def test_a_claims_first_pass_is_its_first_tick_or_its_first_nonzero_value():
    """The census's first pass label: a claim's first heartbeat step, and for a
    beatless claim its first stepped value past the step-0 loading statuses."""
    rows = [
        r(1, "lifecycle.started", _started("local://n1/11", 1.0)),
        r(2, "value", _value("LOADING", 0, 1.1), name="status"),
        r(3, "value", _value(0.5, 1, 1.2), name="loss"),
        r(4, "lifecycle.heartbeat", _beat(1, 0, 1, 1.3)),
        r(5, "lifecycle.started", _started("local://n1/12", 2.0)),
        r(6, "value", _value("LOADING", 0, 2.1), name="status"),
        r(7, "value", _value(0.4, 2, 2.2), name="loss"),
    ]
    assert mr._first_ticks(rows) == {1: 1, 5: 2}


# ----- the census and P1's reads, on logs whose values are argued (Task 23 fix 1) ------


def _census(rows, resolutions):
    """The runner's census of ``rows``, as ``_run_step`` takes it: right after the
    0.3.0 step's detect, from the candidates the step holds."""
    rows = list(rows)
    mr._STEP_0_3_0.detect(rows, assumptions=resolutions.assumptions, history=())
    plan = mr.Planned(mr._STEP_0_3_0, resolutions, frozenset(), mr.Memo())
    return mr.census(rows, plan)


def _versions(rows, resolutions, claim):
    reader = mr.step04.FACTS.of(resolutions.assumptions).classifier.read(list(rows))
    return sorted(repr(v) for v in reader.started(claim))


def test_the_census_counts_a_resume_at_the_loaded_step_under_any_reading():
    """The reference Worker's log of three passes (0, 1, 2), then a claim at seq 9
    resuming at 2: its first pass is labelled 2. Under ``resume`` ``at`` it loaded pass
    2's save and runs pass 2 again: a resume at the loaded step, the census's one hit.
    Under ``after`` it loaded pass 1's save, an older one, and continues at 2: the
    loaded step is 1, no hit. With both stated, both candidates stand and the hit is
    counted once, as the census counts it, under any reading."""
    import test_migration_v0_3_0_to_v0_4_0 as t
    import runstate.migrations.v0_3_0_to_v0_4_0_facts as V

    rows = t._a_resumed_workers_log(2)
    older = frozenset({V.Load.FRESH, V.Load.NEWEST, V.Load.OLDER})
    at = t._preset(resume=frozenset({V.Resume.AT}))
    hit = [mr.Resumed(9, 2, _versions(rows, at, 9))]
    assert _census(rows, at).resumed == hit
    assert _census(rows, t._preset(load=older)).resumed == []
    both = t._preset(resume=frozenset(V.Resume), load=older)
    assert _census(rows, both).resumed == hit


def test_the_census_splits_an_end_time_fork_into_its_dating_and_its_places():
    """Two logs whose end-time forks the step's own tests pin. The beacon log: pass 0
    is dated by its tick (one dating, no fork); pass 1 closes silently, before the
    beacon or after it: undated, with no tick, at two places. The six-record log: claim
    1 never commits; claim 3's one commit is dated by its tick or ended before it,
    undated: a dating fork, its undated end at one place."""
    import test_migration_oracle as o
    import test_migration_v0_3_0_to_v0_4_0 as t
    from runstate.migrations.ambiguity import REFUSE, Assumptions, Chosen, Resolutions

    beacon = _census(t._a_beacon_between_two_places(), t.under(t.FIX1, **t.CHOSEN))
    assert beacon == mr.Census(
        commits=2, dating=0, undated=1, places=1, rewinds=0, resumed=[]
    )
    rows, hs = o._six_records()
    cfg = o.D.configuration([o.observed(h) for h in hs])
    limits = mr.Limits(cap=o.CAP, budget=o.BUDGET, run_on=o.RUN_ON)
    lower = Chosen("lower", mr.step04.END_TIME.generic[0].chooser)
    res = Resolutions(
        Assumptions(cfg, "x", limits),
        {
            **{
                p.name: Chosen(None, REFUSE.chooser)
                for p in mr.step04.V0_3_0_to_V0_4_0.PROJECTIONS
            },
            "end-time": lower,
        },
    )
    assert _census(rows, res) == mr.Census(
        commits=1, dating=1, undated=0, places=0, rewinds=0, resumed=[]
    )


def _migrated_beacon():
    import test_migration_v0_3_0_to_v0_4_0 as t
    from runstate.channel import MemoryChannel

    m = t.step.transform(
        t._a_beacon_between_two_places(),
        resolutions=t.under(t.FIX1, **t.CHOSEN),
        history=(),
    )
    env = mr._envelopes(m.rows)
    return MemoryChannel(log=env), env


def test_p1s_reads_hold_over_a_migrated_log():
    assert mr.lineage_check(*_migrated_beacon()) == mr.LineageCheck([], [])


def test_p1_records_a_lineage_read_that_raises(monkeypatch):
    """The lineage itself raising stops the check there; a per-name read raising is
    that name's, and the other reads go on."""
    ch, env = _migrated_beacon()

    def boom(*args, **kw):
        raise ValueError("boom")

    monkeypatch.setattr(mr.reads, "prefix_len", boom)
    assert mr.lineage_check(ch, env) == mr.LineageCheck(["loss: ValueError: boom"], [])
    monkeypatch.setattr(mr.reads, "lineage", boom)
    assert mr.lineage_check(ch, env) == mr.LineageCheck(
        ["lineage: ValueError: boom"], []
    )


def test_p1_records_a_last_element_that_is_not_the_series_last(monkeypatch):
    """The beacon log's ``loss`` series holds one element, pass 0's value. A
    ``last_element`` that answers none, or an element other than that one, disagrees
    with the series."""
    import dataclasses

    ch, env = _migrated_beacon()
    head = mr.reads.LatestClaimHead().resolve(ch)
    (only,) = mr.reads.series(ch, "loss", head=head)
    real = mr.reads.last_element
    monkeypatch.setattr(mr.reads, "last_element", lambda *a, **kw: None)
    assert mr.lineage_check(ch, env) == mr.LineageCheck([], ["loss"])
    other = dataclasses.replace(only, value=only.value + 1)
    monkeypatch.setattr(mr.reads, "last_element", lambda *a, **kw: other)
    assert mr.lineage_check(ch, env) == mr.LineageCheck([], ["loss"])
    monkeypatch.setattr(mr.reads, "last_element", real)
    assert mr.lineage_check(ch, env) == mr.LineageCheck([], [])


def test_a_refusal_at_the_horizon_is_its_own_kind():
    """The step's own horizon case (a claim, then another, with ``beat-cov`` and
    ``marker`` unstated, so a candidate runs silent passes to the horizon): the runner
    names its kind, ``the horizon``, never unknown."""
    import test_migration_v0_3_0_to_v0_4_0 as t

    rows, configuration = _every_refusal()["the_horizon"]
    with pytest.raises(MigrationError) as refused:
        t.migrated(rows, configuration, **t.CHOSEN)
    assert "closes a pass at the horizon" in str(refused.value)
    kind = mr.refusal_kind(mr.StepName.FROM_0_3_0, str(refused.value))
    assert kind == "the horizon"


def test_a_child_that_cannot_send_its_record_aborts_the_runner(tmp_path, monkeypatch):
    """A record the child cannot pickle (a ``MemoryError``, say) is the runner's own
    failure: it aborts, naming the run, rather than recording something else."""
    root = tmp_path / "root"
    _write(root / "v0.3.0" / "unsent.db", GOLDEN["exit"])

    def fail(*args, **kw):
        raise MemoryError

    monkeypatch.setattr(mr.pickle, "dumps", fail)
    with pytest.raises(RuntimeError, match=r"unsent\.db could not send its record"):
        _main(root, tmp_path / "out", max_rows=100)
