# What runstate is, and why not just use X

For the reasonable question *"isn't this Kafka / Kubernetes / MLflow / Postgres?"* — and for
deciding when the answer is **yes, use that instead**.

## The one-line claim

> **A run is a durable, first-class identity that outlives the processes executing it.** runstate is
> an append-only record of one such run, plus a cooperative control plane on the same surface.

The pieces exist elsewhere, and most are better elsewhere: ordered logs, compare-and-swap, process
lifecycle, metric dashboards — and the identity itself, in bounded forms. A Temporal activity retries
under one id and resumes from its last heartbeat's checkpoint; DBOS resumes a workflow id from its last
completed step; Determined's detached mode gives an externally launched trial a caller-chosen id, an
attempt counter and resume by step; a virtual actor's identity outlives its activations by definition.
"Attempt 4 resumes from step 400 and is the same run" is not new.

**What we did not find is the combination.** A survey of four families on 2026-10-02 (durable
execution, event-sourced actors, ML trial stores, asset orchestrators; rows below) found no system with
all five of:

1. **one caller-chosen identity hosting any number of attempts, extendable after it completes**, with
   every attempt's lifecycle and per-step values kept as one record. Elsewhere a completed run is
   closed, retried attempts are collapsed or overwritten, and per-step values live somewhere else;
2. **that record readable by a third party with no service**, including telling a dead run from a live
   one from dated records alone;
3. **a durable, cooperative stop**: a recorded request, read at a safe point, surviving the worker being
   down, for workers the system did not spawn;
4. **`ensure`**: produce only what is missing, by extending the same identity;
5. **a published, versioned schema** another language can implement without anyone's SDK.

Each one exists somewhere; the claim is the conjunction, scoped to what was surveyed. Those are what
**episodes**, the file beside the run, `control.stop`, `ensure` and `protocol/` are for.

The combination looks inherent to the run shape rather than an artefact of runstate's design choices.
A prototype on 2026-10-02 put this run shape on the nearest system, DBOS 3.2.0 over SQLite. The layer
on top had to rebuild runstate's own mechanisms: episodes (a new workflow id per episode, with values
stitched across ids), stop discharge (forwarding a stop across ids), the liveness tiers (a heartbeat and
a staleness rule), and the self-claim (DBOS's recovery took over live runs).

## Why the obvious candidates do not cover it

Each of these solved part of the problem and had no reason to cross into the rest.

| | what it gives you | why it is not this |
|---|---|---|
| **Kafka** | the durable ordered log, at scale, done properly; per-producer fencing (a `transactional.id` whose epoch `InitProducerId` bumps, [KIP-98](https://cwiki.apache.org/confluence/display/KAFKA/KIP-98+-+Exactly+Once+Delivery+and+Transactional+Messaging)); a compacted topic is a last-write-wins register | no per-entity log to read — "this run" is a scan of its partition. The fencing covers one producer's transactional writes, not liveness or a run's lifecycle. And you must operate a cluster. Kafka is the substrate without the conventions. |
| **Postgres** | durability, total order, CAS | no conventions, no vocabulary, no observation model. You would write runstate on top of it — which is literally what `PostgresChannel` is, at ~270 lines. |
| **Kubernetes** | process lifecycle as a first-class concern; start/stop really are buttons | it models **pods**, and a run outlives its pods. Its status is *reconciled desired state*, not history: you cannot ask "what was the loss at step 200, three restarts ago." |
| **LGTM / Logstash / Prometheus** | telemetry at scale, and good dashboards | aggregate and lossy by design (sampling, retention). No per-run identity, **no control plane at all**, and push-to-a-service — the data leaves the run and lives somewhere else. |
| **Erlang / Elixir** | supervision trees, process lifecycle, message passing | mailboxes are **ephemeral**. After a crash there is no re-readable record. And the unit is a *process*, not a run spanning processes. |
| **Virtual actors and event sourcing** — Orleans, Akka/Pekko Persistence, Restate | an entity whose identity outlives its activations, a per-entity journal, and enforced single-writer (single activation; Restate rejects an older attempt's journal events) | the runtime hosts the entity's code, while a run is an **external process** that checkpoints itself. A journal records one entity's events, not its attempts as re-readable episodes, and is read through the runtime. Akka's SQL journal shares runstate's `(id, seq)` key, and keeps commands out of the journal so every write can be a compare-and-swap. |
| **Durable execution** — [Temporal](https://docs.temporal.io/encyclopedia/detecting-activity-failures), [DBOS](https://docs.dbos.dev/python/tutorials/workflow-tutorial) | the closest on run semantics: one id across retries, resume from the last heartbeat's checkpoint or completed step, heartbeat timeouts, cancellation delivered at the heartbeat, fencing of stale attempts. DBOS is a library over a SQLite or Postgres file | the training loop cannot be the workflow, since workflow code must be deterministic, so the run becomes an activity or step inside one. Temporal needs a server, caps a history's size, deletes it after a retention period, and omits retried attempts. DBOS has no heartbeat in the open library, writes cancel as a verdict that lands only between steps, keeps one mutable status row, and silently ignores new inputs when a finished id is started again. |
| **ML trial stores** — Determined, [Optuna's journal storage](https://optuna.readthedocs.io/en/stable/tutorial/20_recipes/011_journal_storage.html), Ray Tune | the closest on the workload. Determined's detached mode gives a trial it did not launch a caller-chosen id, an attempt counter, resume by step and a per-step metric history that archives superseded values on resume — runstate's take-the-latest read, reached independently. Optuna's journal is one append-only file with no server and an NFS-safe lock | Determined is a service, has no stop and no single-spawn for detached trials, and has had no release since 2025-03. Optuna's unit is a study, a retried trial is a new trial, and a finished trial cannot be extended. Ray Tune keeps a trial's identity only inside one driver, and must own the processes. None has anything like `ensure`. |
| **Asset orchestrators** — Dagster, Flyte, Metaflow | content-addressed reuse, lineage, and staleness propagation for derived data (Dagster's data versions do this better than runstate); bounded resume within one submission | the unit is an **atomic task**: its output exists or it does not, so there is no step-indexed run to extend, and producing only a missing suffix means splitting the work into separately cached chunks. A stop is a signal to the process, not a recorded request. Dagster and Flyte need a server and must launch the worker. |
| **CSP, π-calculus, actors, sockets** | communication, composition, and a real theory of it | they deliberately abstract away durability and identity-over-time. A channel is synchronization, not memory. |
| **MLflow, W&B, Sacred, Aim, Neptune** | experiment tracking, and mature. MLflow now defaults to a local SQLite file; W&B resumes a run by a caller-chosen id, writes a local transaction log, and ships a terminal viewer that reads it live | the format is each tool's internal schema, not a published contract, so you build your viewer against their API, not a wire format. Control is thin: MLflow has none, and W&B's stop kills the script rather than asking it to stop. |

## The bet

**No service. The log is a file beside the run, and anything that can read a file can participate.**

Consequences, good and bad, stated together:

- A viewer needs no API key, no daemon, no network. `runstate-tui` is a separate program that shares
  no code with the producer — only the log format.
- The record survives the tooling. A run's log is readable in ten years with `sqlite3`.
- **But** there is no aggregation across thousands of runs for free, no retention policy, no
  hosted UI, and no team-scale access control. Those are real things the service model gives you.

## When you should use something else

Honest cases, because a positioning doc that never says "use the other thing" is marketing.

- **You want dashboards and don't want to build them** → W&B or MLflow. They are good, and this is
  the case they are for.
- **You need thousands of runs aggregated, queried, and retained by policy** → an observability
  stack. Per-run logs are the wrong shape for fleet-scale telemetry.
- **Your work units are stateless or cheap to restart** → you do not need episodes. Use a job queue.
- **Your state naturally lives in a database row** → then the database is your state. runstate earns
  its place when the unit of work is long-running, resumable, expensive to restart, and needs
  watching *while it runs*.
- **You can run a server, your workers can be its endpoints, and you want enforced single-writer and
  central failure detection more than a durable file** → Temporal or Restate.
- **You want a library over SQLite or Postgres with fencing and durable messages, and can give up the
  episode history and extending a finished run** → DBOS.
- **Your unit is a trial inside a search loop, with no outside control** → Optuna or Ray Tune.
- **Your units are atomic tasks and what you need is lineage and staleness of derived data** → Dagster
  or Flyte.
- **You need to enforce anything** → runstate enforces exactly one thing (see below). Anything else
  belongs to whatever spawns the workers. In particular **runstate declines fencing tokens**, and
  `specs/write-authority.md` explains the trade: acquiring the claim is itself an append, so there is no
  separate acquisition operation to hang a token on. That is a choice, not a limit of the deployment
  shape. DBOS fences every write over the same SQLite or Postgres file by making ownership a mutable row
  rather than an appended record (`_check_owner_txn` in `dbos/_sys_db.py`). runstate keeps the claim a
  record instead, because a record is observable, ordered against everything else, and the same on
  every backend.

## What runstate guarantees, exactly

The boundary is narrow on purpose, and most confusion about the design comes from assuming it is
wider.

**Enforced** — true regardless of anyone's cooperation, because the substrate makes it so:

- appends are atomic; the order is total;
- `send(expected_seq=)` admits exactly one winner at that seq.

That is the entire list, and it is why the *required* substrate is four operations. (Two more —
`hold_episode` / `episode_alive` — exist as optional capability protocols off the base ABC, for
backends that can offer a session-bound liveness signal. They are a signal, never a claim gate.)

**Recorded** — true if the writer was honest. Who claimed, what was requested, what was observed,
what the verdict was. Every topic has a declared writer and reader; nothing checks that a writer
told the truth.

**Never** — stop a process, start a process, own a directory, schedule anything, or enforce anything
over time.

The sharpest analogy is **POSIX advisory locking**: the claim is `flock` — a record everyone agrees
to check, which stops nobody who declines. `layers.md` collects the rest (event sourcing + CQRS for
the fold structure, Chandra–Toueg failure detectors for the liveness tier, linear/affine logic for
the control verbs, `make`/Nix for `ensure`).

The recurring design error, in this repo's own history, is mistaking a **recorded** fact for an
**enforced** one:

| looks like | actually is |
|---|---|
| the claim = one writer over time | one claimant *at the claiming instant* (`specs/write-authority.md`) |
| `control.stop` = stops the worker | a durably recorded, ordered *request* |
| `lifecycle.stopped` = the run stopped | a *report* some worker wrote |
| `resolve()` = whether it is alive | a detector with an accuracy/latency profile |

A caveat that costs people time: a record guaranteeing nothing can still *cause* everything, because
other things read it and act. Evicting a live claim revokes no authority and still spawns a second
worker.

## Adoption, honestly

The "many projects speak one language" goal depends on network effects a small protocol rarely gets.
The realistic and still-substantial win is *one researcher's projects plus one viewer that works
across all of them*. Design for that; treat wider adoption as upside, not as the plan.
