# A Prolog query layer over the log — spec for a probe

**Status:** spec for an experiment, not a proposal to adopt. The artifact is a **separate package**
(`docs/design-v0.2.md` is explicit that a richer read/viewer protocol belongs in its own project on
runstate, never in this repo's `protocol/`). Nothing here changes runstate.

**Why do it.** `demand-driven-reads.md` describes a target — the relation as interface, incremental
tabling with answer subsumption — argued entirely on paper. This is the cheapest test of it, and
unlike another design round **it cannot be refuted by something already written in the repo**: either
the folds come out shorter and clearer as tabled predicates, or they do not.

**The validating idea:** runstate's folds are already the reference semantics. Port them to SWI, run
both against the same logs, and diff. **The Python folds are the oracle.**

## 1. The fact base

`Envelope` is a flat 5-tuple (`channel/envelope.py`), so the fact shape is direct:

```prolog
%  env(Seq, Topic, Name, RequestId, Body)
env(1, 'lifecycle.started',   null,   'L1', _{handle:"local://h/1", t:1000.0}).
env(2, 'value',               "loss", null, _{value:0.5, step:0, t:1001.0}).
env(3, 'lifecycle.heartbeat', null,   null, _{step:0, consumed_seq:0, t:1001.0}).
```

SWI reads the dumped JSONL directly with `json_read_dict/2` — no conversion step, and the body stays
an opaque dict exactly as the substrate treats it.

## 2. The folds to port

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

**Answer subsumption — verify before relying on it.** XSB has `lattice/1` and `po/1` answer-
subsumption modes. SWI's mode-directed tabling has `min`/`max`/`sum`/`-`, and `lattice/1` support
should be confirmed rather than assumed. If it is absent, the LWW fold is still expressible as `max`
above, and only the *domain*-ordered subsumption (interval containment and similar) would need a
different encoding.

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
