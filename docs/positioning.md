# What runstate is, and why not just use X

For the reasonable question *"isn't this Kafka / Kubernetes / MLflow / Postgres?"* — and for
deciding when the answer is **yes, use that instead**.

## The one-line claim

> **A run is a durable, first-class identity that outlives the processes executing it.** runstate is
> an append-only record of one such run, plus a cooperative control plane on the same surface.

That sentence contains the whole contribution. The pieces — ordered logs, compare-and-swap, process
lifecycle, metric dashboards — all exist elsewhere and are better elsewhere. What does not exist
elsewhere is the *identity*: attempt 4 resumes from step 400, is the same run as attempts 1–3, and
the whole history is one re-readable artifact. That is what **episodes** are.

## Why the obvious candidates do not cover it

Each of these solved one half of the problem and had no reason to cross into the other.

| | what it gives you | why it is not this |
|---|---|---|
| **Kafka** | the durable ordered log, at scale, done properly | no per-entity log identity — you get topics and partitions, not "this run." No claim arbitration for an entity. And you must operate a cluster. Kafka is the substrate without the conventions. |
| **Postgres** | durability, total order, CAS | no conventions, no vocabulary, no observation model. You would write runstate on top of it — which is literally what `PostgresChannel` is, at ~270 lines. |
| **Kubernetes** | process lifecycle as a first-class concern; start/stop really are buttons | it models **pods**, and a run outlives its pods. Its status is *reconciled desired state*, not history: you cannot ask "what was the loss at step 200, three restarts ago." |
| **LGTM / Logstash / Prometheus** | telemetry at scale, and good dashboards | aggregate and lossy by design (sampling, retention). No per-run identity, **no control plane at all**, and push-to-a-service — the data leaves the run and lives somewhere else. |
| **Erlang / Elixir** | supervision trees, process lifecycle, message passing — the closest on control | mailboxes are **ephemeral**. After a crash there is no re-readable record. And the unit is a *process*, not a run spanning processes. |
| **CSP, π-calculus, actors, sockets** | communication, composition, and a real theory of it | they deliberately abstract away durability and identity-over-time. A channel is synchronization, not memory. |
| **MLflow, W&B, Sacred, Aim, Neptune** | the actual prior art — experiment tracking, and mature | all chose the **service** model: push to our server, use our UI. That yields dashboards, not a protocol: you cannot build your own viewer against a stable wire format, and there is effectively no control plane. |

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
- **You need to enforce anything** → runstate enforces exactly one thing (see below). Anything else
  belongs to whatever spawns the workers. In particular **fencing tokens are not available**, and
  `specs/write-authority.md` explains why that is structural rather than unfinished: acquiring the
  claim is itself an append, so there is no un-fenceable acquisition operation to hang a token on.

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
