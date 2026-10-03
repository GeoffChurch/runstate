"""Simulated mycooc training: USER code, not part of the layer.

loss(k) is deterministic in k; each step sleeps; the checkpoint is a JSON file
{step, state} in a per-run directory, written atomically (tmp + rename).
Test instrumentation: every checkpoint write is also appended to ckpt_log.jsonl
(pid, step, t), so B2 can see which process wrote the artifact plane when.
"""

import json
import math
import os
import signal
import time


def loss(k: int) -> float:
    return round(1.0 / (1 + k) + 0.01 * math.sin(k), 6)


class SimTrainer:
    def __init__(self, root: str) -> None:
        self.root = root
        self.step_seconds = float(os.environ.get("RS_STEP_SECONDS", "0.05"))
        self.freeze_at = int(os.environ.get("RS_FREEZE_AT", "-1"))

    def _dir(self, rid: str) -> str:
        d = os.path.join(self.root, "ckpt", rid)
        os.makedirs(d, exist_ok=True)
        return d

    def resume_step(self, rid: str) -> int:
        try:
            with open(os.path.join(self._dir(rid), "ckpt.json")) as f:
                return int(json.load(f)["step"])
        except FileNotFoundError:
            return 0

    def train_step(self, rid: str, k: int) -> float:
        time.sleep(self.step_seconds)
        return loss(k)

    def after_value(self, rid: str, k: int) -> None:
        """Test hook: freeze self after the value for step k is written, before checkpointing."""
        if k == self.freeze_at:
            print(f"[{os.getpid()}] self-SIGSTOP after value step {k}", flush=True)
            os.kill(os.getpid(), signal.SIGSTOP)
            time.sleep(
                0.3
            )  # park this thread in a syscall so the group stop lands HERE, not later
            print(f"[{os.getpid()}] resumed after SIGCONT", flush=True)

    def checkpoint(self, rid: str, next_step: int) -> None:
        d = self._dir(rid)
        tmp = os.path.join(d, f"ckpt.json.{os.getpid()}")
        with open(tmp, "w") as f:
            json.dump({"step": next_step, "state": {"w": next_step * 0.5}}, f)
        os.replace(tmp, os.path.join(d, "ckpt.json"))
        with open(os.path.join(d, "ckpt_log.jsonl"), "a") as f:
            f.write(
                json.dumps({"pid": os.getpid(), "step": next_step, "t": time.time()})
                + "\n"
            )
