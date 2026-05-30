"""End-to-end: ThreadLauncher + Worker + a concurrent orchestrator.

Validates the in-process stack composes. A real Worker loop runs on the
launcher's thread while the orchestrator drives it over the shared (thread-safe)
log: a pre-registered subscription is serviced into value events; a commanded
stop lands the worker at a safe point; and the worker's lifecycle.stopped and
the launcher's launcher.terminated together bracket the run.
"""

from runstate.launcher import ThreadLauncher
from runstate.liveness import peek_terminal
from runstate.worker import Worker


def _train(channel):
    with Worker(channel) as w:
        for step in w.steps(total=5):
            w.set("loss", 1.0 / (step + 1))


def test_subscription_serviced_end_to_end(tmp_path):
    launcher = ThreadLauncher(root=tmp_path)
    obs = launcher.open_channel("run")
    # Pre-register a per-step subscription so the worker picks it up on its first
    # control drain — deterministic, no timing race.
    obs.send(
        {"every": {"step": 1}},
        topic="control.subscribe",
        name="loss",
        request_id="obs-1",
    )

    launcher.launch("run", _train).join()

    vals = obs.read(topics=["value"], name="loss", request_ids=["obs-1"])
    assert len(vals) >= 1
    for e in vals:
        assert e.request_id == "obs-1"
        assert e.body["value"] == 1.0 / (e.body["step"] + 1)

    stopped = obs.latest("lifecycle.stopped")
    assert stopped.body["reason"] == "completed"
    assert stopped.body["final_step"] == 4
    assert obs.latest("launcher.terminated").body["exit_code"] == 0
    assert peek_terminal(obs).outcome == "completed"


def test_commanded_stop_end_to_end(tmp_path):
    launcher = ThreadLauncher(root=tmp_path)
    obs = launcher.open_channel("run2")
    obs.send({"from": {"step": 2}}, topic="control.stop", request_id="obs-1")

    launcher.launch("run2", _train).join()

    stopped = obs.latest("lifecycle.stopped")
    assert stopped.body["reason"] == "commanded"
    assert stopped.body["final_step"] == 2
    # clean thread exit despite the early stop
    assert obs.latest("launcher.terminated").body["exit_code"] == 0
