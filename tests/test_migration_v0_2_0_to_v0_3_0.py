"""The 0.2.0 -> 0.3.0 step (reference-by-name.md §6; log-formats.md §6).

The golden logs are 0.2.0-shaped by hand, as master's worker and clients wrote
them: heartbeats and stoppeds that name nothing, nameless stops, answers related
by position. They mirror the spike's T4 synthetic world (crashes, displacement,
leases, re-sends, refusals, a third party's release). Their reads under master's
positional folds were computed once, from a ``git archive master`` (72d9c3f)
copy, and are pasted below as literals."""

import json
import os
import socket
import sqlite3
from pathlib import Path

import pytest

from runstate import LOG_FORMAT
from runstate.channel.sqlite import _SCHEMA, SqliteChannel
from runstate.formats import FORMATS, DirectoryLayout
from runstate.migrations import STEPS, MigrationError, Row, chain, migrate
from runstate.migrations.stores import SqliteStore
from runstate.migrations.v0_2_0_to_v0_3_0 import V0_2_0_to_V0_3_0
from runstate.observables import (
    MalformedRecordError,
    live_demand,
    live_episode,
    peek_terminal,
    progress,
    undischarged_stops,
)

step = V0_2_0_to_V0_3_0()


def r(seq, topic, body, rid=None, name=None, t=0.0):
    return Row(seq, topic, name, rid, json.dumps(body, separators=(",", ":")), t)


def test_names_are_what_the_positional_rule_said():
    rows = [
        r(1, "lifecycle.started", {"handle": "local://h/1", "t": 0.0}),
        r(2, "control.stop", {}),  # nameless
        r(3, "lifecycle.heartbeat", {"step": 1, "consumed_seq": 2, "t": 1.0}),
        r(
            4,
            "lifecycle.stopped",
            {"completed": False, "error": None, "final_step": 1, "t": 2.0},
        ),
    ]
    out = {x.seq: json.loads(x.body) for x in step.transform(rows)}
    stop_id = [x for x in step.transform(rows) if x.seq == 2][0].request_id
    assert stop_id == "stop@2"
    assert out[3]["claim_seq"] == 1
    assert out[4]["claim_seq"] == 1 and out[4]["honored"] == ["stop@2"]


def test_unchanged_records_keep_their_bytes():
    raw = r(1, "value", {"value": 0.1, "step": 0, "t": None}, name="loss")
    assert step.transform([raw]) == [raw]


def test_unchanged_bodies_are_never_reserialized():
    """The text is kept, not an equal re-serialization of it: a canonical dump of
    this body would drop the spaces, spell 1.50 as 1.5 and escape the é."""
    text = '{ "value" : 1.50, "step": 0, "t": null, "unit": "é" }'
    raw = Row(1, "value", "loss", "s1", text, 7.0)
    assert step.transform([raw]) == [raw]


def test_minted_names_never_collide():
    """Review focus 5."""
    rows = [
        r(1, "lifecycle.started", {"handle": "local://h/1", "t": 0.0}),
        r(2, "control.stop", {}, rid="stop@3"),  # a user id shaped like a minted one
        r(3, "control.stop", {}),  # nameless at seq 3 -> would mint stop@3
        r(
            4,
            "lifecycle.stopped",
            {"completed": False, "error": None, "final_step": 0, "t": 1.0},
        ),
    ]
    out = step.transform(rows)
    ids = [x.request_id for x in out if x.topic == "control.stop"]
    assert len(set(ids)) == 2
    honored = json.loads([x for x in out if x.topic == "lifecycle.stopped"][0].body)[
        "honored"
    ]
    assert sorted(honored) == sorted(ids)


def test_a_live_episode_is_live():
    rows = [r(1, "lifecycle.started", {"handle": "local://otherhost/1", "t": 0.0})]
    assert step.is_live(rows)  # unresolvable foreign handle reads as live


# ----- golden 0.2.0 logs -------------------------------------------------------

DEAD = f"local://{socket.gethostname()}/2147483646"  # this host, above pid_max


def _started(handle, t):
    return {"handle": handle, "t": t}


def _beat(step, consumed, t):
    return {"step": step, "consumed_seq": consumed, "t": t}


def _stopped(final_step, t, *, error=None):
    return {"completed": False, "error": error, "final_step": final_step, "t": t}


def _launched(handle, t):
    return {"handle": handle, "t": t, "status": "running"}


def _exited(code, t):
    return {"reason": "exited", "exit_code": code, "signal": None, "t": t}


def _killed(signal, t):
    return {"reason": "killed", "exit_code": None, "signal": signal, "t": t}


def _nak(reason, message):
    return {"reason": reason, "message": message}


def _value(value, step, t):
    return {"value": value, "step": step, "t": t}


# The stop plane: a stop before any worker exists, an id re-sent while pending and
# reused after its discharge, a stop the worker refused, and a stop that lands
# after the worker died, re-sent.
STOPS = [
    r(1, "control.stop", {}),
    r(2, "launcher.launched", _launched("local://n1/11", 1.0), rid="L1"),
    r(3, "lifecycle.started", _started("local://n1/11", 2.0), rid="L1"),
    r(4, "lifecycle.heartbeat", _beat(0, 1, 3.0)),
    r(5, "lifecycle.stopped", _stopped(0, 4.0)),
    r(6, "launcher.terminated", _exited(0, 5.0), rid="L1"),
    r(7, "control.stop", {}, rid="halt"),
    r(8, "control.stop", {"from": {"step": 5}}, rid="halt"),
    r(9, "launcher.launched", _launched("local://n1/12", 6.0), rid="L2"),
    r(10, "lifecycle.started", _started("local://n1/12", 7.0), rid="L2"),
    r(11, "lifecycle.heartbeat", _beat(0, 8, 8.0)),
    r(12, "lifecycle.stopped", _stopped(0, 9.0)),
    r(13, "launcher.terminated", _exited(0, 10.0), rid="L2"),
    r(14, "control.stop", {"from": {"time_seconds": 3600}}, rid="halt"),
    r(15, "control.stop", {"from": {"step": 3}}, rid="never"),
    r(16, "lifecycle.started", _started(DEAD, 11.0)),  # hand-run, stepless; dies
    r(
        17,
        "lifecycle.nak",
        _nak("unsatisfiable", "stop trigger can never fire"),
        rid="never",
    ),
    r(18, "lifecycle.heartbeat", _beat(None, 15, 12.0)),
    r(19, "control.stop", {"from": {"step": 100}}, rid="late"),
    r(20, "control.stop", {"from": {"step": 50}}, rid="late"),
]

# The subscribe plane: a durable subscription re-sent, cancelled and subscribed
# again; an unsubscribe before any subscribe of its id; a refusal; one-shot and
# count expiries; a time lease and a count lease voided by an episode boundary;
# a lease no episode has seen yet.
SUBS = [
    r(1, "control.subscribe", {"every": {"step": 1}}, rid="s1", name="loss"),
    r(2, "control.unsubscribe", {}, rid="ghost"),
    r(3, "launcher.launched", _launched("local://n1/21", 1.0), rid="L1"),
    r(4, "lifecycle.started", _started("local://n1/21", 2.0), rid="L1"),
    r(5, "control.subscribe", {"until": {"count": 0}}, rid="zero", name="loss"),
    r(6, "lifecycle.nak", _nak("unsatisfiable", "no fires"), rid="zero"),
    r(7, "value", _value(0.9, 0, 3.0), rid="s1", name="loss"),
    r(8, "lifecycle.heartbeat", _beat(0, 5, 3.0)),
    r(9, "control.subscribe", {"every": {"step": 2}}, rid="s1", name="loss"),
    r(10, "control.subscribe", {}, rid="once", name="acc"),
    r(11, "value", _value(0.5, 1, 4.0), rid="once", name="acc"),
    r(12, "control.unsubscribe", {}, rid="once"),
    r(
        13,
        "control.subscribe",
        {"every": {"step": 1}, "until": {"time_seconds": 60}},
        rid="tl",
        name="loss",
    ),
    r(14, "lifecycle.heartbeat", _beat(1, 13, 4.0)),
    r(15, "control.unsubscribe", {}, rid="s1"),
    r(16, "lifecycle.heartbeat", _beat(2, 15, 5.0)),
    r(17, "launcher.terminated", _killed(9, 6.0), rid="L1"),
    r(18, "control.subscribe", {"every": {"step": 1}}, rid="s1", name="loss"),
    r(19, "control.subscribe", {}, rid="ghost", name="acc"),
    r(
        20,
        "control.subscribe",
        {"every": {"step": 1}, "until": {"count": 3}},
        rid="cnt",
        name="loss",
    ),
    r(21, "launcher.launched", _launched("local://n1/22", 7.0), rid="L2"),
    r(22, "lifecycle.started", _started("local://n1/22", 8.0), rid="L2"),
    r(23, "value", _value(0.4, 2, 9.0), rid="s1", name="loss"),
    r(24, "value", _value(0.4, 2, 9.0), rid="cnt", name="loss"),
    r(25, "value", _value(0.6, 2, 9.0), rid="ghost", name="acc"),
    r(26, "control.unsubscribe", {}, rid="ghost"),
    r(27, "lifecycle.heartbeat", _beat(2, 20, 9.0)),
    r(28, "lifecycle.stopped", _stopped(2, 10.0)),
    r(29, "launcher.terminated", _exited(0, 11.0), rid="L2"),
    r(
        30,
        "control.subscribe",
        {"every": {"step": 1}, "until": {"time_seconds": 30}},
        rid="tl2",
        name="loss",
    ),
    r(31, "lifecycle.started", _started("local://n1/23", 12.0)),
    r(32, "lifecycle.stopped", _stopped(2, 13.0)),
]

# Episodes: A is displaced by B and writes a late beat and a late stopped (the
# cascade), B is killed, C errors before its first beat.
EPISODES = [
    r(1, "launcher.launched", _launched("local://n2/31", 1.0), rid="LA"),
    r(2, "lifecycle.started", _started("local://n2/31", 2.0), rid="LA"),
    r(3, "lifecycle.heartbeat", _beat(0, 0, 3.0)),
    r(4, "value", _value(0.9, 0, 3.0), name="loss"),
    r(5, "lifecycle.heartbeat", _beat(150, 0, 4.0)),
    r(6, "launcher.launched", _launched("local://n2/32", 5.0), rid="LB"),
    r(7, "lifecycle.started", _started("local://n2/32", 6.0), rid="LB"),
    r(8, "lifecycle.heartbeat", _beat(999, 0, 7.0)),
    r(9, "lifecycle.stopped", _stopped(999, 8.0)),
    r(10, "launcher.terminated", _exited(0, 9.0), rid="LA"),
    r(11, "launcher.terminated", _killed(9, 10.0), rid="LB"),
    r(12, "launcher.launched", _launched("local://n2/33", 11.0), rid="LC"),
    r(13, "lifecycle.started", _started("local://n2/33", 12.0), rid="LC"),
    r(14, "lifecycle.stopped", _stopped(None, 13.0, error="CUDA out of memory")),
    r(15, "launcher.terminated", _exited(1, 14.0), rid="LC"),
]

# One id on both planes: a stop the worker refused, then a subscription under the
# same id; a stop the worker accepted, then a refused subscription under its id.
SHARED = [
    r(1, "lifecycle.started", _started(DEAD, 1.0)),  # hand-run, stepless; dies
    r(2, "control.stop", {"from": {"step": 3}}, rid="x"),
    r(
        3,
        "lifecycle.nak",
        _nak("unsatisfiable", "stop trigger can never fire"),
        rid="x",
    ),
    r(4, "lifecycle.heartbeat", _beat(None, 2, 2.0)),
    r(5, "control.subscribe", {"every": {"time_seconds": 1}}, rid="x", name="load"),
    r(6, "value", _value(0.3, None, 3.0), rid="x", name="load"),
    r(7, "control.stop", {"from": {"time_seconds": 3600}}, rid="y"),
    r(8, "control.subscribe", {"until": {"count": 0}}, rid="y", name="load"),
    r(9, "lifecycle.nak", _nak("unsatisfiable", "no fires"), rid="y"),
    r(10, "lifecycle.heartbeat", _beat(None, 8, 4.0)),
]

# Records already malformed under 0.2.0: a malformed stop and subscribe, each
# refused, and a third party's claim release shaped as in the real corpus.
MALFORMED = [
    r(1, "lifecycle.started", _started("local://n3/41", 1.0)),
    r(2, "control.stop", {"from": {"bogus": 1}}, rid="bad"),
    r(3, "lifecycle.nak", _nak("malformed", "unknown key 'bogus'"), rid="bad"),
    r(4, "control.subscribe", {"frm": {"step": 1}}, rid="junk", name="loss"),
    r(5, "lifecycle.nak", _nak("malformed", "unknown key 'frm'"), rid="junk"),
    r(6, "lifecycle.heartbeat", _beat(4, 5, 2.0)),
    r(7, "control.stop", {}),
    r(8, "lifecycle.stopped", {"reason": "reclaimed: slurm TIMEOUT"}),
]

# A verb 0.2.0 did not know, refused by master's worker as unsupported, under an
# id an operator stop also bears: once before the stop, once after it. Under the
# positional rule neither nak answered the stop.
VERBS = [
    r(1, "lifecycle.started", _started(DEAD, 1.0)),  # hand-run, stepless; dies
    r(2, "control.pause", {}, rid="p"),
    r(3, "lifecycle.nak", _nak("unsupported", "unknown 'control.pause'"), rid="p"),
    r(4, "control.stop", {"from": {"time_seconds": 3600}}, rid="p"),
    r(5, "control.stop", {"from": {"time_seconds": 3600}}, rid="q"),
    r(6, "control.pause", {}, rid="q"),
    r(7, "lifecycle.nak", _nak("unsupported", "unknown 'control.pause'"), rid="q"),
    r(8, "lifecycle.heartbeat", _beat(None, 7, 2.0)),
]

# Third parties' well-formed releases. A claimless one keeps a startless run
# claimable (mycooc's resume_fanout)...
CLAIMLESS = [
    r(1, "control.stop", {}),
    r(2, "lifecycle.stopped", _stopped(None, 1.0)),
]

# ...and one releases a stranded claim after its worker was killed (mycooc's
# reclaim tool), discharging the operator's stop it never carried out (#39,
# copied faithfully).
RELEASED = [
    r(1, "launcher.launched", _launched("local://n4/51", 1.0), rid="L1"),
    r(2, "lifecycle.started", _started("local://n4/51", 2.0), rid="L1"),
    r(3, "lifecycle.heartbeat", _beat(3, 0, 3.0)),
    r(4, "control.stop", {"from": {"step": 10}}, rid="halt"),
    r(5, "launcher.terminated", _killed(9, 4.0), rid="L1"),
    r(6, "lifecycle.stopped", _stopped(None, 5.0)),
]


def _dated(rows):
    """Every record its own created_at, so the test can see each one kept."""
    assert [x.seq for x in rows] == list(range(1, len(rows) + 1))
    return [x._replace(created_at=1000.0 + x.seq / 4) for x in rows]


GOLDEN = {
    "stops": _dated(STOPS),
    "subs": _dated(SUBS),
    "episodes": _dated(EPISODES),
    "shared": _dated(SHARED),
    "malformed": _dated(MALFORMED),
    "verbs": _dated(VERBS),
    "claimless": _dated(CLAIMLESS),
    "released": _dated(RELEASED),
}
# The records each golden log holds that are invalid under 0.2.0's own schemas;
# every other log is well-formed (test_golden_inputs_are_0_2_0_as_declared).
INVALID_UNDER_0_2_0 = {"malformed": [2, 4, 8], "verbs": [2, 6]}
WELL_FORMED = [rid for rid in GOLDEN if rid not in INVALID_UNDER_0_2_0]


def _write(root: Path, rid: str, rows: list[Row]) -> Path:
    """One log at its 0.2.0 address, every row as given, in WAL mode as a
    channel leaves it."""
    path = FORMATS["0.2.0"].sqlite_path(root, rid)
    path.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    conn.executemany(
        "INSERT INTO log (seq, topic, name, request_id, body, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()
    return path


def write_v0_2_0(root: Path) -> None:
    """Every golden log at its 0.2.0 address under ``root``."""
    for rid, rows in GOLDEN.items():
        _write(root, rid, rows)


def reads(ch):
    """The reads the comparison covers, as plain values."""
    try:
        res = peek_terminal(ch)
        verdict = None if res is None else (str(res.outcome), res.error, res.final_step)
    except MalformedRecordError as exc:
        verdict = f"MalformedRecordError at seq {exc.seq}"
    return {
        "peek_terminal": verdict,
        "live_episode": live_episode(ch),
        "undischarged_stops": [e.seq for e in undischarged_stops(ch)],
        "live_demand": [e.seq for e in live_demand(ch)],
        "progress": progress(ch),
    }


# master 72d9c3f's folds on the golden logs (one scratch run, 2026-10-03)
MASTER = {
    "stops": {
        "peek_terminal": None,
        "live_episode": None,
        "undischarged_stops": [14, 15, 19, 20],
        "live_demand": [],
        "progress": None,
    },
    "subs": {
        "peek_terminal": ("preempted", None, 2),
        "live_episode": None,
        "undischarged_stops": [],
        "live_demand": [18, 30],
        "progress": 2,
    },
    "episodes": {
        "peek_terminal": ("errored", "CUDA out of memory", None),
        "live_episode": None,
        "undischarged_stops": [],
        "live_demand": [],
        "progress": 999,
    },
    "shared": {
        "peek_terminal": None,
        "live_episode": None,
        "undischarged_stops": [2, 7],
        "live_demand": [5],
        "progress": None,
    },
    "malformed": {
        "peek_terminal": "MalformedRecordError at seq 8",
        "live_episode": None,
        "undischarged_stops": [],
        "live_demand": [],
        "progress": 4,
    },
    "verbs": {
        "peek_terminal": None,
        "live_episode": None,
        "undischarged_stops": [4, 5],
        "live_demand": [],
        "progress": None,
    },
    "claimless": {
        "peek_terminal": ("preempted", None, None),
        "live_episode": None,
        "undischarged_stops": [],
        "live_demand": [],
        "progress": None,
    },
    "released": {
        "peek_terminal": ("preempted", None, None),
        "live_episode": None,
        "undischarged_stops": [],
        "live_demand": [],
        "progress": 3,
    },
}

# The only reads allowed to differ: the spike's two classes (T4), each a positional
# defect that names fix, and each asserted on its own below.
STALE_BEAT_LEAK = {"episodes": (999, None)}  # progress: master's -> 0.3.0's
NAKED_STOP = {"stops": 15}  # the stop master still listed after its nak


def _migrated(root, rid):
    return SqliteChannel(FORMATS["0.3.0"].sqlite_path(root, rid), create=False)


def test_golden_logs_read_as_master_read_them(tmp_path):
    write_v0_2_0(tmp_path)
    assert sorted(migrate(SqliteStore(tmp_path), None, to="0.3.0")) == sorted(GOLDEN)
    for rid in GOLDEN:
        expected = dict(MASTER[rid])
        if rid in STALE_BEAT_LEAK:
            before, after = STALE_BEAT_LEAK[rid]
            assert expected["progress"] == before
            expected["progress"] = after
        if rid in NAKED_STOP:
            assert NAKED_STOP[rid] in expected["undischarged_stops"]
            expected["undischarged_stops"] = [
                s for s in expected["undischarged_stops"] if s != NAKED_STOP[rid]
            ]
        ch = _migrated(tmp_path, rid)
        assert reads(ch) == expected, rid
        ch.close()


def test_the_stale_beat_leak_is_the_whole_progress_difference(tmp_path):
    """Master's progress read a beat at or before the latest claim, so the claim
    had none of its own. Here the latest claim (13) has no beat naming it, and the
    beat master read (999, at seq 8) names the claim before it."""
    write_v0_2_0(tmp_path)
    migrate(SqliteStore(tmp_path), ["episodes"], to="0.3.0")
    ch = _migrated(tmp_path, "episodes")
    beats = ch.read(topics=["lifecycle.heartbeat"])
    assert [e.body["claim_seq"] for e in beats if e.body["step"] == 999] == [7]
    assert not [e for e in beats if e.body["claim_seq"] == 13]
    assert progress(ch) is None
    ch.close()


def test_the_naked_stop_is_answered_by_its_own_nak(tmp_path):
    """Master kept listing a refused stop until the next stopped. The stop at seq
    15 is the only request bearing its id, and the nak at 17 bears that id."""
    write_v0_2_0(tmp_path)
    migrate(SqliteStore(tmp_path), ["stops"], to="0.3.0")
    ch = _migrated(tmp_path, "stops")
    (stop,) = [e for e in ch.read(topics=["control.stop"]) if e.seq == 15]
    naks = ch.read(topics=["lifecycle.nak"])
    assert [e.seq for e in naks if e.request_id == stop.request_id] == [17]
    assert [e.seq for e in ch.read() if e.request_id == stop.request_id] == [15, 17]
    ch.close()


# ----- what the step writes ----------------------------------------------------


@pytest.mark.parametrize("rid", list(GOLDEN))
def test_records_keep_seq_topic_name_created_at_and_unchanged_bytes(rid):
    rows = GOLDEN[rid]
    out = step.transform(rows)
    kept, appended = out[: len(rows)], out[len(rows) :]
    assert all(type(x.topic) is str for x in out)  # never an enum member
    assert [(x.seq, x.topic, x.name, x.created_at) for x in kept] == [
        (x.seq, x.topic, x.name, x.created_at) for x in rows
    ]
    for old, new in zip(rows, kept):  # rewritten only when the body changed
        assert new.body == old.body or json.loads(new.body) != json.loads(old.body)
    assert [x.seq for x in appended] == [
        len(rows) + k for k in range(1, len(appended) + 1)
    ]
    assert all(x.topic == "lifecycle.bound" for x in appended)
    assert all(x.created_at == max(y.created_at for y in rows) for x in appended)


@pytest.mark.parametrize("rid", list(GOLDEN))
def test_each_request_id_names_one_request(rid):
    """Positionally each stop record was its own request, and a subscription id
    one request per answer-delimited segment. After the step, no two stops share
    an id and no stop shares one with a subscription."""
    out = step.transform(GOLDEN[rid])
    stops = [x.request_id for x in out if x.topic == "control.stop"]
    subscribed = {x.request_id for x in out if x.topic == "control.subscribe"}
    assert None not in stops
    assert len(set(stops)) == len(stops)
    assert not set(stops) & subscribed


def test_reused_stop_ids_are_split_and_honored_by_their_stopped():
    out = {x.seq: x for x in step.transform(GOLDEN["stops"])}
    assert [out[s].request_id for s in (1, 7, 8, 14, 15, 19, 20)] == [
        "stop@1",
        "halt#7",
        "halt#8",
        "halt#14",
        "never",
        "late#19",
        "late#20",
    ]
    assert json.loads(out[5].body)["honored"] == ["stop@1"]
    assert json.loads(out[12].body)["honored"] == ["halt#7", "halt#8"]


def test_subscription_ids_are_split_at_each_answer():
    out = {x.seq: x for x in step.transform(GOLDEN["subs"])}
    assert out[2].request_id == "ghost!orphan@2"  # answered nothing; names nothing
    assert [out[s].request_id for s in (1, 7, 9, 15)] == ["s1"] * 4
    assert [out[s].request_id for s in (18, 23)] == ["s1#1"] * 2  # with its fire
    assert [out[s].request_id for s in (19, 25, 26)] == ["ghost"] * 3


def test_an_answer_naming_a_later_subscription_is_renamed():
    """SHARED: the nak at 3 refused the stop at 2, before any subscribe of x;
    named, it would spend the subscription at 5."""
    out = {x.seq: x for x in step.transform(GOLDEN["shared"])}
    assert out[3].request_id == "x!orphan@3"
    assert (out[2].request_id, out[5].request_id) == ("x#2", "x")
    assert (out[7].request_id, out[8].request_id, out[9].request_id) == (
        "y#7",
        "y",
        "y",
    )


def test_a_nak_before_every_stop_of_its_id_names_nothing():
    """A nak cannot refuse a stop that comes after it, so under the positional
    rule it answered nothing. Named, it would spend the stop."""
    rows = [
        r(1, "lifecycle.nak", _nak("unsupported", "m"), rid="p"),
        r(2, "control.stop", {}, rid="p"),
    ]
    nak, stop = step.transform(rows)
    assert (nak.request_id, stop.request_id) == ("p!orphan@1", "p")


def test_a_stop_sharing_its_id_with_any_other_request_is_renamed():
    """VERBS: an unknown verb is a request too, and master's worker naks it under
    its id. A stop sharing that id keeps it only at the cost of being answered
    by a nak that refused something else."""
    out = {x.seq: x.request_id for x in step.transform(GOLDEN["verbs"])}
    assert (out[4], out[5]) == ("p#4", "q#5")
    assert (out[3], out[7]) == ("p!orphan@3", "q")  # q now names no stop


def test_leases_the_boundary_voided_get_an_inferred_bound():
    """A known limit: 0.2.0 never recorded which episode registered a lease. Each
    one the positional rule voided is bound to the first claim between it and the
    latest claim, which is what voids it by name."""
    out = step.transform(GOLDEN["subs"])
    bound = [(x.seq, x.request_id, json.loads(x.body)) for x in out[32:]]
    assert bound == [(33, "tl", {"claim_seq": 22}), (34, "cnt", {"claim_seq": 22})]


def test_a_displaced_workers_late_records_name_its_successor():
    """A known limit (episode-aim's objection): the misattribution is copied, not
    corrected. A's late beat and stopped land after B's claim, so the positional
    rule attributed them to B, and that is the name they get."""
    out = {x.seq: json.loads(x.body) for x in step.transform(GOLDEN["episodes"])}
    assert out[8]["claim_seq"] == 7 and out[9]["claim_seq"] == 7


def test_a_malformed_stopped_still_gets_the_names_it_had_positionally():
    """The positional rule was blind to body: a third party's malformed release
    still ended the claim and discharged the stops before it. It keeps its
    malformed fields (the verdict reads still raise) and gains only the names."""
    out = {x.seq: json.loads(x.body) for x in step.transform(GOLDEN["malformed"])}
    assert out[8] == {
        "reason": "reclaimed: slurm TIMEOUT",
        "honored": ["bad", "stop@7"],
        "claim_seq": 1,
    }


def test_a_stopped_before_any_claim_names_no_claim():
    rows = [
        r(1, "control.stop", {}, rid="pre"),
        r(
            2,
            "lifecycle.stopped",
            {"completed": False, "error": None, "final_step": None, "t": 1.0},
        ),
    ]
    body = json.loads(step.transform(rows)[1].body)
    assert body["claim_seq"] is None and body["honored"] == ["pre"]


def test_a_heartbeat_before_any_claim_refuses_the_run():
    """Ruling 10: Heartbeat.claim_seq is a required integer, so 0.3.0 cannot hold
    this beat, and leaving it unnamed would mix formats in one log. The run is
    refused; the beat is never dropped."""
    rows = [
        r(1, "launcher.launched", _launched("local://n/1", 0.0), rid="L"),
        r(2, "lifecycle.heartbeat", _beat(0, 0, 1.0)),
        r(3, "lifecycle.started", _started("local://n/1", 2.0), rid="L"),
    ]
    with pytest.raises(MigrationError, match="seq 2"):
        step.transform(rows)


def _journal_mode(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()


def test_a_refused_sqlite_run_is_refused_before_the_seal(tmp_path):
    """Ruling 12: the refusal is deterministic, so it comes before the seal. The
    run stays writable and unsealed, and a re-run refuses it the same way."""
    old = _write(tmp_path, "r", [r(1, "lifecycle.heartbeat", _beat(0, 0, 1.0))])
    for attempt in range(2):
        with pytest.raises(MigrationError, match=r"^run 'r': .*seq 1"):
            migrate(SqliteStore(tmp_path), ["r"], to="0.3.0")
        assert os.stat(old).st_mode & 0o200  #          no read-only bit
        assert _journal_mode(old) == "wal"  #   the seal would have left WAL
        assert not FORMATS["0.3.0"].sqlite_path(tmp_path, "r").exists()
        ch = SqliteChannel(old, create=False)
        assert ch.send({"attempt": attempt}, topic="value", name="n") is not None
        ch.close()


@pytest.fixture
def pg_v0_2_0_run(pg_ready):
    """A run in the shared postgres test database's 0.2.0 schema. A 0.3.0 schema
    left behind would make every later open there raise, so none may survive."""
    import uuid

    import psycopg

    rid = f"t12-{uuid.uuid4().hex}"
    with psycopg.connect(pg_ready, autocommit=True) as c:
        assert c.execute("SELECT to_regnamespace('runstate_v0_3_0')").fetchone() == (
            None,
        )
    try:
        yield pg_ready, rid
    finally:
        with psycopg.connect(pg_ready, autocommit=True) as c:
            c.execute("DROP SCHEMA IF EXISTS runstate_v0_3_0 CASCADE")
            c.execute("DELETE FROM runstate_v0_2_0.log WHERE run_id = %s", [rid])


def test_a_refused_postgres_run_rolls_back_its_seal(pg_v0_2_0_run):
    """Ruling 12 on postgres: the seal, the read and the copy are one
    transaction, so the refusal rolls the seal back with everything else."""
    import psycopg

    from runstate.migrations.stores import PostgresStore

    dsn, rid = pg_v0_2_0_run
    insert = (
        "INSERT INTO runstate_v0_2_0.log (run_id, seq, topic, name, request_id, body,"
        " created_at) VALUES (%s, %s, %s, %s, %s, %s, %s)"
    )
    with psycopg.connect(dsn) as c:
        c.execute(insert, [rid, *r(1, "lifecycle.heartbeat", _beat(0, 0, 1.0))])
    for attempt in range(2):
        with pytest.raises(MigrationError, match=rf"^run '{rid}': .*seq 1"):
            migrate(PostgresStore(dsn), [rid], to="0.3.0")
        with psycopg.connect(dsn) as c:
            sealed = c.execute(
                "SELECT to_regclass('runstate_v0_2_0.sealed_runs')"
            ).fetchone()
            if sealed != (None,):
                assert c.execute(
                    "SELECT count(*) FROM runstate_v0_2_0.sealed_runs WHERE run_id = %s",
                    [rid],
                ).fetchone() == (0,)
            assert c.execute(
                "SELECT to_regnamespace('runstate_v0_3_0')"
            ).fetchone() == (None,)
            c.execute(insert, [rid, 2 + attempt, "value", "n", None, "{}", 0.0])


# ----- liveness, read as 0.2.0 read it -------------------------------------------


def test_is_live_is_the_latest_claim_with_no_later_stopped():
    claim = r(2, "lifecycle.started", {"handle": "local://otherhost/1", "t": 0.0})
    assert not step.is_live([])
    assert step.is_live([r(1, "lifecycle.stopped", {}), claim])
    assert not step.is_live([claim, r(3, "lifecycle.stopped", {"reason": "x"})])


def test_a_dead_handle_is_not_live():
    assert not step.is_live([r(1, "lifecycle.started", _started(DEAD, 0.0))])


def test_a_claim_without_a_handle_cannot_be_judged():
    with pytest.raises(MigrationError, match="seq 1"):
        step.is_live([r(1, "lifecycle.started", {"t": 0.0})])


def test_a_refusal_through_migrate_names_its_run(tmp_path):
    """Minor 1: migrate() names the run in every refusal it surfaces, and stops
    at the first."""
    _write(tmp_path, "a", [r(1, "lifecycle.started", {"t": 0.0})])
    _write(tmp_path, "b", [r(1, "lifecycle.heartbeat", _beat(0, 0, 1.0))])
    with pytest.raises(MigrationError, match=r"^run 'a': .*seq 1.*no handle"):
        migrate(SqliteStore(tmp_path), None, to="0.3.0")
    with pytest.raises(MigrationError, match=r"^run 'b': .*seq 1.*precedes"):
        migrate(SqliteStore(tmp_path), ["b"], to="0.3.0")


# ----- schema validity -----------------------------------------------------------

_HERE = Path(__file__).resolve().parent
# 0.2.0's stack, vendored from master 72d9c3f: its lifecycle and subscription
# schemas left protocol/ when 0.3.0 replaced them.
STACK_0_2_0 = (
    _HERE / "fixtures" / "log-format-0.2.0",
    [
        "envelope-v0.2",
        "subscription-v0.2",
        "lifecycle-v0.4",
        "launcher-v0.4",
        "value-v0.2",
    ],
)
STACK_0_3_0 = (
    _HERE.parent / "protocol",
    [
        "envelope-v0.2",
        "subscription-v0.3",
        "lifecycle-v0.5",
        "launcher-v0.4",
        "value-v0.2",
    ],
)


def _invalid_seqs(rows, stack):
    """The seqs of the rows the stack rejects: the envelope schema, then the
    convention schema of the record's topic."""
    jsonschema = pytest.importorskip("jsonschema")
    root, (envelope, control, lifecycle, launcher, value) = stack

    def load(name):
        return jsonschema.Draft202012Validator(
            json.loads((root / f"{name}.schema.json").read_text())
        )

    env = load(envelope)
    conventions = {
        "control.": load(control),
        "lifecycle.": load(lifecycle),
        "launcher.": load(launcher),
        "value": load(value),
    }
    bad = []
    for x in rows:
        record = {
            "seq": x.seq,
            "topic": x.topic,
            "name": x.name,
            "request_id": x.request_id,
            "body": json.loads(x.body),
        }
        (convention,) = [v for k, v in conventions.items() if x.topic.startswith(k)]
        if not env.is_valid(record) or not convention.is_valid(record):
            bad.append(x.seq)
    return bad


@pytest.mark.parametrize("rid", list(GOLDEN))
def test_golden_inputs_are_0_2_0_as_declared(rid):
    assert _invalid_seqs(GOLDEN[rid], STACK_0_2_0) == INVALID_UNDER_0_2_0.get(rid, [])


@pytest.mark.parametrize("rid", WELL_FORMED)
def test_well_formed_logs_come_out_valid_under_the_0_3_0_schemas(rid):
    assert _invalid_seqs(step.transform(GOLDEN[rid]), STACK_0_3_0) == []


# ----- a retained step carries its formats' semantics ------------------------------


def test_the_step_imports_no_record_semantics():
    """Ruling 13 (log-formats §6): the step is retained forever, so it must read
    0.2.0 and write 0.3.0 as they were defined, whatever later code does. From
    the package it takes only Row, MigrationError and resolve(), an OS probe of a
    handle rather than a format rule."""
    import ast
    import inspect

    import runstate.migrations.v0_2_0_to_v0_3_0 as mod

    tree = ast.parse(inspect.getsource(mod))
    internal = {
        (n.level, n.module, a.name)
        for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom)
        and (n.level > 0 or (n.module or "").startswith("runstate"))
        for a in n.names
    }
    assert internal == {
        (1, None, "MigrationError"),
        (1, None, "Row"),
        (2, "vocabulary.handle", "resolve"),
    }
    plain = {
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
    }
    assert not {m for m in plain if m.startswith("runstate")}


# ----- registration ----------------------------------------------------------------


def test_the_step_and_format_are_registered():
    assert FORMATS["0.3.0"] == DirectoryLayout("0.3.0")
    assert LOG_FORMAT == "0.2.0"  # until the release that switches to it
    assert [type(s) for s in STEPS] == [V0_2_0_to_V0_3_0]
    assert chain("0.2.0", "0.3.0") == list(STEPS)
