# submitit-launcher — a first-class SLURM / submitit adapter

**Status:** forward-looking; the design entry for promoting the
`examples/submitit/` recipe to a first-class adapter. The recipe is the
**validating prototype** — it runs the full protocol (subscribe → launch →
stream `value` → terminal `RunResult` → reaped `launcher.terminated`) over
`submitit.AutoExecutor(cluster="local")`, verified 2026-07-16. Nothing is
library code yet; this file collects the decisions an owner must make before it
becomes one.

## Why a recipe first, not library code

The README claims you can "spawn however you want — submitit, ray — and talk via
the protocol." `examples/submitit/` *is* that claim, executed: a ~50-line
`SubmititLauncher` / `SubmititHandle` implementing runstate's
`Launcher` / `LaunchHandle` protocol, with the submitted function setting the
`RUNSTATE_*` env (and `RUNSTATE_LAUNCH_ID`) so the worker's `attach()` meets the
same log. That the recipe is short and needs no library change is the evidence
that the protocol boundary is in the right place. Promoting it to a shipped
adapter is a separate, deliberate decision — below.

## Decisions for the first-class adapter

### 1. Packaging stance — in-repo extra vs separate package

The backlog's "Ecosystem adapters" section plans **`runstate-submitit`** as a
*separate package* (alongside `runstate-ray`, `runstate-k8s`). The alternative
is an **in-repo `[submitit]` extra**, following the **postgres-extra precedent**
(`docs/specs/channel-postgres.md`: an optional dependency, `import runstate`
stays clean, one CI job exercises it). Tradeoff:

- *In-repo extra* — one repo, one version, conformance tested in this suite;
  but grows the core's surface and couples release cadence to submitit's.
- *Separate package* — clean dependency story and independent cadence (submitit
  is a heavier, faster-moving dep than psycopg); but a second repo to keep in
  sync with protocol bumps.

Recommendation deferred. The postgres precedent makes an in-repo `[submitit]`
extra defensible *if* the adapter stays as thin as the recipe; a separate
package is right the moment it grows resource-config surface (see 5).

### 2. Target shape — callable vs cmd (the heterogeneous-`launch` finding)

submitit submits a **callable** (like `ThreadLauncher`), not a command (like
`LocalLauncher`). This is a third instance of
[launcher-protocol-typing](launcher-protocol-typing.md)'s finding: the reference
launchers' `launch` signatures are already disjoint (`target: Callable` vs
`cmd: list[str]`), so `launch` cannot be structurally typed, and the four helpers
take `launcher: Any`. A `SubmititLauncher.launch` adds a callable-plus-resources
signature — it does **not** worsen the finding (the helpers already accept `Any`),
but it is the concrete third case that would drive the proposed split (uniform
`open_channel` + a per-launcher launch thunk). The recipe sidesteps it by fixing
the worker callable and taking `total` as the config; a first-class adapter must
decide whether `launch` takes an arbitrary callable (needs cloudpickle — the
recipe relies on it) or a cmd (submitit can run one via
`submitit.helpers.CommandFunction`).

### 3. Terminated mapping from submitit job states

The recipe maps success → `Terminated(exited, exit_code=0)` and failure →
`exited, exit_code=1` (synthetic, like `ThreadLauncher`'s thread-death mapping).
A first-class adapter should map submitit/SLURM states faithfully to the
`launcher.terminated` convention (`reason: exited|killed`, `exit_code?`,
`signal?`):

- `FAILED` → `errored` (with the real exit code where submitit exposes it);
- `TIMEOUT` (SLURM wall-clock kill) → `killed` (signal `SIGTERM`/`SIGKILL`);
- `CANCELLED` → `killed`;
- **`PREEMPTED` + requeue is NOT a death** — see 5.

The synthetic 0/1 in the recipe is honest only because the worker's clean
`lifecycle.stopped` wins tiers 1-2 (the `terminated` is never the verdict on the
happy path); a real adapter that wants `terminated` to carry the true manner of
death must read the job state.

### 4. Handle grammar — `slurm://<job_id>` and the `?start=` disambiguator

The recipe's handle is `slurm://<job_id>`; `resolve()` abstains on it
(vocabulary/handle.py — a job id is not a local pid), so a handle-less observer
falls to the heartbeat tier, which is correct. Decisions for the real grammar:

- **A `resolve` for `slurm://`** could shell out to `squeue -j <job_id>` (design
  §8 already names `squeue -j` as the SLURM liveness probe) — turning the
  abstain into a real cross-host liveness fact, the one thing the recipe cannot
  do. This is the adapter's most valuable addition over the recipe.
- **Array tasks** — SLURM array jobs are `job_id_taskid`; the grammar needs a
  form for them (`slurm://<job_id>_<taskid>`), and `resolve` must query the task.
- **The reserved `?start=` disambiguator** (conventions-hygiene F9, deferred for
  `local://` pid reuse) is *unneeded* for `slurm://`: SLURM job ids are
  monotonic within a controller, so there is no reuse ambiguity a start-time
  would disambiguate. Note this explicitly so the F9 work is not mistakenly
  scoped to cover slurm handles.

### 5. Checkpointing / requeue interplay with `steps(start=)`

The genuine synergy — and the reason this adapter is more than plumbing. SLURM
**preemption + requeue** restarts a job (same or new id) after a checkpoint;
submitit models it with `submitit.helpers.Checkpointable`. runstate's episode
model already handles this: a requeued job is a **new episode** attaching to the
**same** `run_id`, resuming from checkpoint via `steps(start=)` and the
checkpoint-the-frontier discipline (`examples/reuse/`, `examples/redrive/`). So a
`Checkpointable` submitit worker + runstate compose for free — a preemption is
just another episode boundary. The adapter should:

- document that requeue = a new runstate episode (not a `terminated`-then-relaunch
  the orchestrator must drive);
- ensure the requeued process re-sets `RUNSTATE_LAUNCH_ID` (a requeue is arguably
  a new launch — or the same one; that is a real question, since the launch-id
  correlates the death record to the claim);
- verify `peek_terminal`'s episode-awareness does the right thing across a
  requeue (a preempted-then-requeued run must not read terminal).

This is the item that most wants a real cluster to validate; the local recipe
cannot exercise preemption.

## Open / needs a real cluster

- `cluster="slurm"` end-to-end validation (the recipe proves only the `local`
  executor path; the SLURM path is one line but untested here).
- Resource parameters (`executor.update_parameters(...)`: partition, GPUs,
  wall-time) — surface and defaults.
- The requeue/`Checkpointable` path (5), which needs preemption to exercise.

## See also

- `examples/submitit/driver.py` — the validating prototype.
- [launcher-protocol-typing](launcher-protocol-typing.md) — the `launch`
  signature finding this adapter is the third instance of.
- `docs/specs/channel-postgres.md` — the in-repo optional-extra precedent (2, 1).
- `docs/backlog/release-and-stability-contract.md` — if it ships as an extra, it
  bears on the packaging decisions there.
