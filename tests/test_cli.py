"""The minimal CLI (`runstate status` / `runstate stop`).

Drives ``cli.main([...])`` directly with capsys + tmp roots. The pins: both
discovery layouts (flat + Recipe-1 sharded); completed / live / dead / malformed
runs each render sensibly; the phantom-creation guard (status/stop on a missing
rid must create NO db file, third-party-observer item 4); stop appends exactly one
control.stop and warns when the run is down (design §7 S2); --wait resolves against
a seeded heartbeat watermark.
"""

import os
import socket
import time

from runstate.channel.sqlite import SqliteChannel
from runstate.cli import main
from runstate.vocabulary.handle import local_handle

_DEAD = (
    f"local://{socket.gethostname()}/2147483646"  # a pid that does not exist, THIS host
)


def _flat(root, rid):
    return SqliteChannel(root / f"{rid}.db")


def _sharded(root, rid):
    # Recipe-1 placement: runs/<rid[:2]>/<rid>/<rid>.db (specs/store.md Recipe 1)
    d = root / "runs" / rid[:2] / rid
    d.mkdir(parents=True)
    return SqliteChannel(d / f"{rid}.db")


def _started(ch, handle, t=0.0):
    ch.send({"handle": handle, "t": t}, topic="lifecycle.started")


def _heartbeat(ch, step, consumed_seq, t):
    ch.send(
        {"step": step, "consumed_seq": consumed_seq, "t": t},
        topic="lifecycle.heartbeat",
    )


def _stopped(ch, *, completed, final_step, t=0.0):
    ch.send(
        {"completed": completed, "error": None, "final_step": final_step, "t": t},
        topic="lifecycle.stopped",
    )


def _line_for(out, rid):
    # each status row starts with the run_id (left-aligned first column)
    return next(line for line in out.splitlines() if line.startswith(rid))


def test_status_discovers_both_layouts(tmp_path, capsys):
    # flat <root>/<rid>.db AND sharded <root>/runs/<xx>/<rid>/<rid>.db
    a = _flat(tmp_path, "flatrun")
    _stopped(a, completed=True, final_step=3)
    a.close()
    b = _sharded(tmp_path, "shardedrun")
    _stopped(b, completed=True, final_step=7)
    b.close()

    assert main(["status", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "flatrun" in out and "shardedrun" in out


def test_status_renders_completed_live_dead(tmp_path, capsys):
    done = _flat(tmp_path, "done")
    _started(done, "local://h/1")
    _stopped(done, completed=True, final_step=9)
    done.close()

    live = _flat(tmp_path, "live")
    _started(live, local_handle())  # our pid -> resolves alive
    _heartbeat(live, step=4, consumed_seq=0, t=time.time())
    live.close()

    dead = _flat(tmp_path, "dead")
    _started(dead, _DEAD)  # dead pid, this host -> live_episode None, no terminal
    _heartbeat(dead, step=2, consumed_seq=0, t=time.time() - 100)
    dead.close()

    assert main(["status", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "completed" in _line_for(out, "done")
    assert _line_for(out, "done").split()[2] == "9"  # progress from final_step
    assert "live" in _line_for(out, "live")
    # dead: started but not live and no terminal record -> idle, with a real age
    assert "idle" in _line_for(out, "dead")
    assert _line_for(out, "dead").rstrip().endswith("s")  # last_activity gave an age


def test_status_never_started(tmp_path, capsys):
    # a log with a beacon but no lifecycle.started reads never-started (no episode)
    ch = _flat(tmp_path, "orphan")
    _heartbeat(ch, step=1, consumed_seq=0, t=time.time())
    ch.close()
    assert main(["status", str(tmp_path)]) == 0
    assert "never-started" in _line_for(capsys.readouterr().out, "orphan")


def test_status_malformed_verdict_is_a_cell_not_a_crash(tmp_path, capsys):
    # a stopped body missing keys is uninterpretable to the verdict fold; the CLI
    # catches MalformedRecordError per-run and renders it, never crashing the table.
    ch = _flat(tmp_path, "bad")
    ch.send(
        {"completed": True}, topic="lifecycle.stopped"
    )  # missing error/final_step/t
    ch.close()
    ok = _flat(tmp_path, "good")
    _stopped(ok, completed=True, final_step=1)
    ok.close()

    assert main(["status", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "malformed" in _line_for(out, "bad")
    assert "completed" in _line_for(out, "good")  # the good run still renders


def test_status_empty_root_creates_no_db(tmp_path, capsys):
    # phantom-creation guard: status on an empty root reports nothing and must not
    # fabricate any .db (third-party-observer item 4).
    assert main(["status", str(tmp_path)]) == 0
    assert "no runs" in capsys.readouterr().out
    assert list(tmp_path.rglob("*.db")) == []


def test_stop_appends_one_control_stop_and_warns_when_down(tmp_path, capsys):
    # a down run (started then stopped -> no live episode): the stop is armed for
    # the next episode, and the CLI warns; exactly ONE control.stop is appended.
    ch = _flat(tmp_path, "downrun")
    _started(ch, "local://h/1")
    _stopped(ch, completed=False, final_step=2)
    ch.close()

    assert main(["stop", str(tmp_path), "downrun"]) == 0
    out = capsys.readouterr().out
    assert "sent control.stop" in out
    assert "ARMED for the NEXT episode" in out  # the down-stop warning

    check = _flat(tmp_path, "downrun")
    stops = check.read(topics=["control.stop"])
    check.close()
    assert len(stops) == 1  # exactly one, request_id cli:<uuid>
    assert stops[0].request_id.startswith("cli:")


def test_stop_missing_run_errors_and_creates_no_db(tmp_path, capsys):
    # stop on a nonexistent rid must error out and fabricate NO db (item 4).
    assert main(["stop", str(tmp_path), "ghost"]) == 1
    err = capsys.readouterr().err
    assert "no run 'ghost'" in err
    assert not (tmp_path / "ghost.db").exists()
    assert list(tmp_path.rglob("*.db")) == []


def test_stop_live_run_does_not_warn(tmp_path, capsys):
    ch = _flat(tmp_path, "liverun")
    _started(ch, local_handle())  # alive -> live episode -> no armed warning
    ch.close()
    assert main(["stop", str(tmp_path), "liverun"]) == 0
    out = capsys.readouterr().out
    assert "sent control.stop" in out
    assert "ARMED" not in out


def test_stop_wait_accepted_against_a_heartbeat_watermark(tmp_path, capsys):
    # --wait resolves at the consumption watermark: a heartbeat whose consumed_seq
    # already covers the stop's seq reads as accepted (design §6).
    ch = _flat(tmp_path, "waitrun")
    _started(ch, local_handle())  # live -> no warning
    _heartbeat(
        ch, step=0, consumed_seq=1000, t=time.time()
    )  # watermark >> any stop seq
    ch.close()

    assert main(["stop", str(tmp_path), "waitrun", "--wait", "5"]) == 0
    out = capsys.readouterr().out
    assert "accepted" in out


def test_stop_wait_times_out_on_a_down_run(tmp_path, capsys):
    # down + --wait: no worker drains it, so the bounded wait times out (reported
    # as not-a-refusal), and the armed warning still prints.
    ch = _flat(tmp_path, "downwait")
    _started(ch, "local://h/1")
    _stopped(ch, completed=False, final_step=1)
    ch.close()

    assert main(["stop", str(tmp_path), "downwait", "--wait", "0"]) == 0
    out = capsys.readouterr().out
    assert "ARMED for the NEXT episode" in out
    assert "not consumed within" in out
