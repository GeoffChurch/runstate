# Prior-art survey rubric: does an existing system already do what runstate does?

## The claim under test

`docs/positioning.md` (repo `GeoffChurch/runstate`, at /home/gchurchill/src/runstate) says:

> A run is a durable, first-class identity that outlives the processes executing it. runstate is an
> append-only record of one such run, plus a cooperative control plane on the same surface. …
> What does not exist elsewhere is the *identity*: attempt 4 resumes from step 400, is the same run as
> attempts 1–3, and the whole history is one re-readable artifact.

and bets on **no service**: "The log is a file beside the run, and anything that can read a file can
participate." Its comparison table covers Kafka, Postgres, Kubernetes, LGTM, Erlang, CSP/actors and
MLflow/W&B. It does **not** cover durable-execution engines, event-sourced actors, ML trial stores, or
asset orchestrators. "Does not exist elsewhere" is a claim of non-existence and has never been surveyed.

**The survey's job is to try to falsify it**, and more usefully to find out whether some existing system
(or a thin layer over one) would serve runstate's two real consumers as well or better.

## What the consumers actually do (from the repo owner's records)

- **mycooc** — an ML experiment framework. Long GPU training runs, preemptible, resumed from the
  worker's own checkpoints by step; content-addressed run ids (hash of config + git fingerprint);
  reuse of finished runs ("don't recompute a done run") is its main day-to-day value, and extending a
  finished run when the step budget rises. It spawns its own processes (never runstate's launchers),
  uses the worker loop, `ensure` (read what exists, produce what is missing), lifecycle records and
  `control.stop` (37 real stops measured). It has a reclaim tool for crashed claims on other hosts.
- **translation** — batch NLP workers that write bulk artifacts to an off-channel content-addressed
  store and emit progress on the channel; consumers gate on `ensure` reaching a step or completion. It
  uses runstate's launchers.
- **No consumer has ever sent a subscription** (`control.subscribe`). The demand path in use is `ensure`.
- Planned: a **cockpit** TUI that attaches to runs it did not start (third-party observer).

## The concerns to score each system against

For each, rate **covered / partial / absent / conflicts**, say *how* (the mechanism, by name), and cite a
primary source. Details of runstate's own mechanism for each concern are in
`/tmp/claude-1641171234/-home-gchurchill-src-runstate/fd6b9e26-2832-4ee3-a3ce-199057df78bd/scratchpad/vs-shipped-map.md`
(sections C1–C21) if you need them.

1. **Durable run identity across processes and attempts** — one identity, many attempts/episodes, one
   re-readable history; resume from the worker's own checkpoint.
2. **Single-spawn** — at most one live attempt claims a run; how it is arbitrated (CAS, lease, broker,
   sharding).
3. **Liveness / failure detection** — heartbeats, timeouts, probes; cross-host; and whether a *third
   party attaching later* can tell a dead run from a live one.
4. **Terminal verdict** — completed vs resumable-interrupted vs failed vs killed; can a "completed" run be
   extended later?
5. **Cooperative stop** — an operator asks a running worker to stop at a safe point; the request is
   durable (survives the worker being down); does a stop survive into the next attempt?
6. **Per-step values** — a metric series keyed by step, readable by anyone, while the run is live.
7. **Memoisation / produce-on-miss** — "give me the loss up to step N": read what exists, launch or extend
   a producer for what is missing; reuse a finished run by content-addressed identity.
8. **Demand / subscriptions** — a reader asks the worker to report something on a schedule; leases.
9. **Derived runs and reuse graphs** — a run computed from other runs, keyed by their identities.
10. **Retention / GC.**
11. **Time** — can a cold third party date a record?
12. **Write authority, provenance, forgery** — what is enforced vs recorded.
13. **Deployment shape** — service/cluster required? library only? embedded file (SQLite)? Postgres?
    What must be operated?
14. **Constraints it imposes on worker code** — deterministic replay, a specific SDK/language, a
    framework's training loop, containerisation.
15. **Interop** — is there a documented wire format/schema another language can implement without the
    vendor's SDK?

Optional, for the redesign (`docs/backlog/if-built-today/`): does the system have anything like
questions with quantifiers, told negative facts ("nothing more will come"), or demand propagated by
rules? Note it if you see it; don't search hard.

## Rules

- **Primary sources**: official docs, source code, papers, maintainers' design docs. Give the URL and the
  version or date. Mark anything you could not confirm `[unverified]`.
- **No claims of non-existence without enumeration.** "X cannot do Y" needs the doc page that defines
  X's model, not the absence of a search hit.
- Be concrete and fair in both directions: say what each system does **better** than runstate, not only
  where it falls short.
- Do not modify anything in the repo.

## Output

Write to the file path you are given:
1. Per system: a one-paragraph summary, then the concern table (concern | rating | mechanism | source).
2. Per system: **the strongest case that it (or it plus a thin layer) subsumes runstate for these two
   consumers**, then **the strongest case that it doesn't**, and what adopting it would cost.
3. Across the family: which system comes closest, and which runstate concerns no system in the family
   covers.
Return a short summary: the closest system, its coverage, and the concerns it leaves uncovered.
