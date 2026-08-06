# A Prolog query layer over the log — spec for a probe

**Status: RETARGETED.** The original probe — port runstate's folds to SWI and differential-test them
— would measure the case where tabling has no edge. **Do not run it as specced.** §0 says what to
point it at instead; the engine findings in §3 stand regardless and are the reason.

The artifact remains a **separate package** (`docs/design-v0.2.md` is explicit that a richer
read/viewer protocol belongs in its own project on runstate, never in this repo's `protocol/`).
Nothing here changes runstate.

## 0. Why the original target is wrong, and what to point at instead

Strip out answer subsumption (unsound, §3) and take tabling's remaining benefits one at a time:

| benefit | worth it here? |
|---|---|
| memoisation | trivial elsewhere. No. |
| **termination for recursive definitions** | genuinely hard to hand-roll — but **measured: `observables.py` has 20 functions and *zero* recursive ones.** Nothing to protect. |
| **incremental re-derivation** | **degenerates.** Its machinery exists to handle *retraction*; an append-only log has none, so "incremental" reduces to processing the suffix after a watermark — which is `read(after=cursor)`, already in the substrate and already used by `Worker._cursor` and the `Watcher`. |
| WFS / three-valued | the four-state cell projection is a short fold. No. |
| declarative query, unification | real — but the competitor is **SQL, not Python**. The logs already live in sqlite/Postgres, where relational query and aggregation pushdown are already available. |

Every fold is a **single-pass window over one log**. Porting them is a lateral move.

**Where tabling would genuinely earn its place is the dependency graph** — transitive closure,
"every run blocked on an incomplete producer", demand-driven materialisation over a DAG. That is
`mycooc/rungraph`, which is **layer 7 of `../layers.md` and explicitly not runstate's**.

**Retargeted probe:** point it at the rungraph, not the folds. `next_claimable` is the oracle, its
four predicates over a config DAG are the test, and it has no substrate concerns at all — a smaller,
cheaper experiment that actually discriminates.

**The validating idea, unchanged and reusable:** the existing implementation is the reference
semantics. Port, run both against the same inputs, diff. **The Python side is the oracle.**

## 1. The fact base (as sketched for the original target; the shape carries over)

`Envelope` is a flat 5-tuple (`channel/envelope.py`), so the fact shape is direct:

```prolog
%  env(Seq, Topic, Name, RequestId, Body)
env(1, 'lifecycle.started',   null,   'L1', _{handle:"local://h/1", t:1000.0}).
env(2, 'value',               "loss", null, _{value:0.5, step:0, t:1001.0}).
env(3, 'lifecycle.heartbeat', null,   null, _{step:0, consumed_seq:0, t:1001.0}).
```

SWI reads the dumped JSONL directly with `json_read_dict/2` — no conversion step, and the body stays
an opaque dict exactly as the substrate treats it.

## 2. The folds to port — *superseded by §0*, kept because the impure/pure split is informative

Seven of the eight are **pure functions of the log** and port directly. One is not, and that is
informative rather than awkward.

| fold | note |
|---|---|
| `latest_episode` | max `seq` over `lifecycle.started` |
| `_episode_stopped` | the terminal after the latest claim |
| `progress` | max over the value axis and the heartbeat axis, episode-scoped |
| `undischarged_stops` | `control.stop`s after the latest terminal |
| `last_activity` | max `t` over the five dated topics |
| **`peek_terminal`** | **the interesting one** — a join of two independent partial observers (`lifecycle.*` self-report, `launcher.*` external) into a closed verdict lattice. `protocol-algebra.md` L3 already describes it in exactly those words; in Prolog it *is* a join rather than a function that resembles one |
| **`value_series`** | **the showcase** — see §3 |
| `live_episode` | **impure**: it calls `resolve(handle)`, an OS probe. Pass the probe result in as a parameter rather than calling out. That mirrors what the design already says — `resolve` is a seam — and keeps the query layer pure |

## 3. What tabling should demonstrate

**Last-write-wins is literally the lexicographic order, and mode-directed tabling says so.**

```prolog
:- table cell(_, _, max).          % max over Seq-Value: standard order compares Seq first

cell(Name, Step, Seq-Value) :-
    env(Seq, value, Name, _, Body),
    get_dict(step,  Body, Step),
    get_dict(value, Body, Value).
```

`max` over `Seq-Value` compares `Seq` first by the standard order of terms — so this is
**revision-at-head lexicographic order**, which is precisely the LWW register `value_series`
implements by hand. If that reads as cleanly in practice as it does here, that is the single
strongest signal from the probe.

**Streaming and incrementality:**

```prolog
:- table cell/3 as incremental.
%  a new record lands:
incr_assert(env(42, value, "loss", null, _{value:0.31, step:41, t:_})).
%  dependent tables recompute; a subscribed query sees the new answer
```

That is the demand-driven streaming of `demand-driven-reads.md` §1, with the engine doing the
propagation rather than a hand-rolled watermark.

**Greedy answer subsumption is UNSOUND, in every mode of both engines. Measured.**

`:- table p(max)` and friends do not preserve least-fixed-point semantics. Reproduced on
SWI-Prolog 10.0.0 and XSB 5.0 (both built here), against the counterexample in the sole user comment
on SWI's mode-directed-tabling page:

```prolog
p(0).  p(1).
p(2) :- p(X), X = 1.
p(3) :- p(X), X = 0.     % p(1) subsumes p(0) BEFORE this clause is tried
```

| engine | plain tabling (LFP) | mode-directed |
|---|---|---|
| SWI 10.0.0 | `3` | `max` → **2** · `po(gt)` → **[2]** · `lattice(join/3)` → **[2]** |
| XSB 5.0 | `[0,1,2,3]` | `lattice(join/3)` → **[2]** |

The result is *scheduling-dependent*: had `p(3)`'s body consumed the table while it still held `0`,
`p(3)` would derive. That is what makes it unsound rather than merely surprising.

**There is no sound mode to fall back on.** The literature is
[Tabling with Sound Answer Subsumption](https://arxiv.org/abs/1608.00787) (Vandenbroucke, Piróg,
Desouter, Schrijvers, TPLP 2016), and it supplies **no fix** — it gives a *correctness condition*
telling you when greedy subsumption happens to be safe. Its own conclusion:

> The verification of correctness does constitute a **non-trivial effort**. Hence, manually proving
> the correctness condition for realistically sized programs could be **unfeasible in practice**.
> Ideally we would have an automated analysis … This is future work.

That was 2016 and the analysis does not appear to exist.

**So the constraint on this probe, demonstrated rather than argued:**

> **Aggregated tables must not be recursive.** The two sound options are aggregate-at-the-end (plain
> tabling + `aggregate/3` — correct, but forgoes the incremental benefit that motivates subsumption
> at all) or keep the aggregation non-recursive.

`cell/3` above is safe **because** it derives from `env/5` facts only — there is no fixpoint for the
join to be premature about. That is not incidental; it is the reason the fold is correct, and it
must survive any refactor.

**Where it would bite this project.** A bandit computing "best config so far" and feeding that back
into which configs to run next is precisely `p(2) :- p(X), …` over a max-aggregated table. That is
the demand-driven layer's natural shape, so this is a live hazard rather than a curiosity.

**Two incidental engine findings.** XSB rejects `p(max)` outright (*"Non predicate specification …
Ignored!"*) and then proceeds **untabled**, so the recursion diverges — a silent downgrade to a
non-terminating program. And XSB cannot aggregate a 1-ary predicate at all: the subsumed value must
be a separate argument from the key. SWI additionally requires the moded argument to be *unbound at
call time*, so `cell(N, S, _-V)` raises and you must bind then destructure.

## 4. The validation harness

This is the part that makes the probe honest rather than a vibe check.

1. **Harvest.** A pytest fixture dumps every channel's log at teardown as JSONL. The suite's ~1000
   tests then become a corpus of real scenarios — including every edge case anyone has bothered to
   write down. `test_schema.py` already does exactly this shape of harvest ("drive a scenario that
   exercises every reserved topic, harvest the log"), so there is precedent.
2. **Oracle.** For each dumped log, record the Python folds' answers alongside it.
3. **Differential test.** SWI loads the log, computes the same folds, emits JSON. A pytest test in
   the *Prolog package* asserts equality against the oracle.

Two properties worth having:

- **A disagreement is a finding either way.** If Prolog is wrong, the port is wrong. If Python is
  wrong, the probe found a bug in the reference — which is the more interesting outcome, and this
  session has produced several such (`count`, post-terminal writes, the claim cascade).
- **It generalises to the real question.** Once the folds agree on single logs, the same harness over
  *many* logs is the sweep case — `(config, step, metric)` across runs — which is what
  `demand-driven-reads.md` is actually about.

## 5. Explicitly out of scope

Everything the substrate does, because Prolog does not help there and the probe should not pretend
otherwise:

- **Writing.** Read-only. No claim, no CAS, no records emitted.
- **Liveness.** `resolve` is a parameter (§2).
- **Durability and coordination.** Tables live in one process's memory and die with it. That is the
  layer runstate is, and the probe sits above it.
- **The lifecycle plane's design questions.** Every refutation in this repo — write authority ending
  at the claiming instant, the unfenceable birth CAS, positional attribution, aim buying
  non-transferability but not forgery resistance — is a fact about what an append-only log with a CAS
  can guarantee, and is **language-independent**. None of them is retired by this, and a probe that
  appeared to retire one would be measuring wrong.

## 6. What would make it a success, and what would kill it

**Success:** the folds are shorter and more obviously correct as tabled predicates; `peek_terminal`
reads as a lattice join; `cell/3` reads as LWW without saying so; incremental tabling gives streaming
with no watermark code. Then the demand-driven target has evidence behind it and the next question is
the FFI boundary at the demand hook.

**Kills it:** the folds come out *longer* because the episode-scoping windows (which are positional,
and which every fold does differently) fight the relational encoding; or the JSON-dict body handling
is noisy enough to swamp the gain; or SWI's tabling needs the fact base fully materialised in memory
per log, making the many-logs sweep case impractical.

That last one is the real risk and should be checked early — load the largest available corpus log
and measure.

## 7. Related

- `demand-driven-reads.md` — the target this probes
- `../layers.md` — the probe is layers 1–2 and 6; layers 0, 3, 4 stay where they are
- `../design-v0.2.md` — "its *own* protocol in a separate project on runstate"
