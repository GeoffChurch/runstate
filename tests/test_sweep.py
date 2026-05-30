"""sweep: sequential multi-run orchestration (docs/design-v0.2.md §9).

No Experiment class — a sweep is just the launcher + Watcher driven over a list
of variants, one run each, watched to a terminal RunResult. Covers the happy
path, on_event streaming, resume (skip runs that already finished), and
stop_on_failure (halt on the first failed outcome).
"""

from runstate.launcher import ThreadLauncher
from runstate.sweep import Variant, sweep
from runstate.watcher import Watcher
from runstate.worker import Worker


def _fast_watcher():
    return Watcher(poll_interval=0.005)


def _ok(channel):
    with Worker(channel) as w:
        for _ in w.steps(total=2):
            pass


def _boom(channel):
    with Worker(channel) as w:
        for _ in w.steps(total=1):
            raise RuntimeError("boom")


def test_sweep_runs_all_variants_and_returns_results(tmp_path):
    launcher = ThreadLauncher(root=tmp_path)
    variants = [Variant(f"run-{i}", _ok) for i in range(3)]
    results = sweep(variants, launcher, watcher=_fast_watcher())
    assert [r.run_id for r in results] == ["run-0", "run-1", "run-2"]
    assert all(r.outcome == "completed" for r in results)


def test_sweep_streams_events_via_on_event(tmp_path):
    launcher = ThreadLauncher(root=tmp_path)
    seen = []
    sweep(
        [Variant("r", _ok)],
        launcher,
        on_event=lambda rid, e: seen.append((rid, e.topic)),
        watcher=_fast_watcher(),
    )
    assert ("r", "lifecycle.stopped") in seen


def test_sweep_resume_skips_already_terminal_run(tmp_path):
    launcher = ThreadLauncher(root=tmp_path)
    calls = []

    def counted(channel):
        calls.append(1)
        _ok(channel)

    variants = [Variant("r", counted)]
    sweep(variants, launcher, watcher=_fast_watcher())
    assert len(calls) == 1

    # second sweep finds the terminal record on the (persisted) log and reuses it
    results = sweep(variants, launcher, resume=True, watcher=_fast_watcher())
    assert len(calls) == 1  # target not invoked again
    assert results[0].outcome == "completed"
    assert results[0].run_id == "r"


def test_sweep_stop_on_failure_halts(tmp_path):
    launcher = ThreadLauncher(root=tmp_path)
    variants = [Variant("a", _ok), Variant("b", _boom), Variant("c", _ok)]
    results = sweep(variants, launcher, stop_on_failure=True, watcher=_fast_watcher())
    assert [r.run_id for r in results] == ["a", "b"]
    assert results[0].outcome == "completed"
    assert results[1].outcome == "errored"
    # "c" was never launched
    assert launcher.open_channel("c").read() == []


def test_sweep_does_not_halt_on_clean_commanded_stop(tmp_path):
    # a "stopped" (clean, commanded) outcome is NOT a failure
    launcher = ThreadLauncher(root=tmp_path)

    def commanded(channel):
        channel.send({"from": {"step": 0}}, topic="control.stop", request_id="x")
        with Worker(channel) as w:
            for _ in w.steps(total=5):
                pass

    variants = [Variant("a", commanded), Variant("b", _ok)]
    results = sweep(variants, launcher, stop_on_failure=True, watcher=_fast_watcher())
    assert results[0].outcome == "stopped"
    assert [r.run_id for r in results] == ["a", "b"]  # did not halt
