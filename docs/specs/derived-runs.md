# Spec: derived runs (compute-on-demand dissolves into the existing basis)

**Status:** reviewed 2026-06-11 (adversarial round: survives-with-amendments;
all six mandatory amendments folded — the full read-set identity, the custom
producer, the direct-emit mechanism, the quiescence precondition,
emit-only-missing, and the honest triangulation verdict). runstate side
implemented (the dissolution pin); the mycooc wiring is the next session's
build. The record of Cluster 1's last
leg *dissolving*: the "function producer" needs **no new worker class, no new
library surface** — only an identity recipe extension and one convention. The
build is consumer-side (mycooc-analyze, the oracle); the library's job here
is to have already been enough.

## The finding (recon-grounded)

The oracle (`mycooc/analyze_run.py`) is one shared load (~191 MB of tensors —
seconds, the dominant cost) followed by ~8 cheap analyses always consumed as
a bundle; nothing is shared *across* analyzed runs (each has its own
tensors). Therefore:

- **Key = the analyzed snapshot, not (snapshot, kind).** One derived run
  computes the whole bundle; binding the kinds together captures all the
  sharing that exists, and a warm multi-key service would amortize nothing
  across keys. The build-system shape (every artifact its own
  content-addressed node) wins.
- **A derived run is just an autonomous run, one step long.** Load → compute
  → emit the bundle as `value`s → claim `completed`. Memoization is the
  log's existence; re-demand is a replay; `ensure` already drives it.
- **The index algebra is unfunded, not refuted** — re-statused dormant with
  its trigger written in (see Dispositions).

## The derived-identity recipe (the one new design content)

A derived run's identity must address **what was actually analyzed**, by the
run-id recipe's own first lesson (*hash by content, not by proxy*):

```python
rid_analysis = run_id({
    "analyzed": R_rid,                       # provenance / discoverability
    "inputs": hash_files(read_set),          # EVERYTHING the analyzer reads
    "params": {"source": source, ...},       # analysis args that shape output
    "code": hash_code(analyzer_sources),
})
```

- **The trap this exists to avoid:** `h(R_rid, code)` aliases — a run's id is
  stable across episodes while its artifacts *evolve* (extend R from step 100
  to 500: same `R_rid`, different checkpoint), so two different computations
  would collide on one identity. Identity must never alias distinct
  computations.
- **The read-set is the WHOLE read-set** (the adversarial round's killer):
  mycooc's analyzer reads not just the `.pt` tensors but the vocab CSVs and
  the per-pair metrics CSVs — and the CSVs grow on every extend. Hashing
  only the tensors would alias step-100 and step-500 analyses whenever no
  new best landed (the exact forbidden trap, hidden one layer down). Absent
  files are filtered into the digest as absent (paths fold in), so a
  transport-less run hashes cleanly. Analysis *parameters* that shape the
  output (`source`, …) fold into the id — the recipe's own-your-partition
  lesson.
- **Content vs proxy, honestly:** with the full read-set, content keying is
  nearly as conservative as the `progress(R)` proxy (the CSVs change almost
  every extend) — its remaining superiority is *exactness*, not hit-rate:
  never a false hit, and byte-identical states re-hit regardless of how
  they were reached. The proxy variant stays documented as cheaper, with
  false-miss-only as its trade.
- **Quiescence precondition:** the hash and the worker's load are two reads
  of mutable files — analyzing a LIVE run can hash one state and load
  another (or a mid-`torch.save` torn file). `ensure_analysis` requires
  `live_episode(R) is None` (analyze settled snapshots; copy-then-hash is
  the alternative for live analysis, not taken).
- **Freshness needs no policy:** the identity *is* the freshness check.
  R changes → the read-set changes → a new derived id; stale analyses remain
  as content-addressed history, never consulted by mistake. (The rejected
  alternative — one analysis run per `R_rid`, refreshed via episodes — turns
  "is it fresh?" into a caller policy and lets `latest` silently mean
  different inputs at different times.)
- **`--compare` is a consumer-side join, not a pair-key:** the JSON compare
  is two independent single-run analyses printed together — zero joint
  computation — so it composes from two cached single-snapshot ensures; a
  pair identity would duplicate cached work and order the unordered.

This section's durable home is `docs/specs/run-id-recipe.md` ("Derived runs:
identity composes"); this spec carries the rationale.

## The one-step-run convention

A derived run's worker is `steps(total=1)`: it yields step 0, computes, and
**hand-emits** the bundle — `channel.send(asdict(Value(value=v, step=0,
t=now)), topic="value", name=k)` per key — then exits and claims `completed`.
(Direct sends, NOT `set()` + tick: `set` only feeds *subscription-driven*
emission and `ensure` never subscribes — a `set`-based worker would complete
with an empty series and every future call would cache-hit on nothing.)
**Emit-only-missing:** before sending, read the channel's existing names at
step 0 and skip those present — a crash-retry episode then fills gaps
instead of re-emitting, so float jitter across retries can never poison the
log with divergent same-cell re-emissions (which `history` treats as an
error, permanently, on an append-only log). The convention gives the run a
real (length-one) step axis — `progress` reads 0, `ensure(until={"step": 1})`
is satisfied exactly at completion, and the no-`ensure`-over-stepless
constraint is sidestepped rather than special-cased. The `completed` claim
is honest: an analysis is intrinsically done. **Single-driver-per-rid**,
documented: two concurrent `ensure_analysis` calls race claims, and the
loser's `ensure` can raise spuriously while the winner is mid-load (the
191 MB window has no heartbeat yet) — the retry succeeds; don't parallelize
demand for one snapshot.

## Producer Protocol: the triangulation verdict (honest version)

A second implementer of the seam **does** exist and is required: mycooc must
write its own producer object (`.channel`/`.run_id`/`.extend` wrapping
`LocalLauncher.launch(cmd)`) because `launch_producer`'s default producer
passes thread-style kwargs that `LocalLauncher.launch` rejects — exactly as
the memoizer spec's own docstring predicted ("LocalLauncher gets its own
producer"). mycooc already shipped one once (`_SubprocessProducer`,
Phase 4). So the triangulation prediction half-held: a second *implementer*,
yes; a second *shape*, no — three implementers now share the identical
3-attribute seam, and freezing a named Protocol would add a name and no
constraint. Deferred on those grounds — argued from evidence, and recorded
as a correction to Decisions 5–6's "lands with the second implementer"
promise rather than a silent goalpost move.

## The mycooc wiring plan (the build, next session)

1. `analyze_run.py --worker`: attach, `steps(total=1)`, compute
   `_analyze_single`, hand-emit `_result_to_json`'s top-level keys as named
   `value`s at step 0 (emit-only-missing), `stopped(completed=True)`.
2. `ensure_analysis(run_dir)` (orchestrator-side): require
   `live_episode(R) is None`; hash the full read-set + params → derived rid →
   `ensure(AnalysisProducer(...), "pair_metrics", until={"step": 1})` — a
   small custom producer over `LocalLauncher.launch(cmd)` (the
   `_SubprocessProducer` shape) — then read the bundle off the derived
   channel (`value_series` / `latest`).
3. The analysis channel lives beside the analyzed run's
   (`{run_dir}/{rid_analysis}.db` — mycooc's layout choice).
4. Optional dogfood for the parked residue: `ensure`-redrive + the
   `stopped.reason` recipe fit analysis runs (cheap, recoverable) if picked
   up later; not required here.

## Dispositions

- `docs/backlog/memoizer-index-algebra.md` → **dormant, trigger written in**:
  revisit only for a consumer with many *independent*, *individually
  expensive*, *sparsely demanded* keys for which one-run-per-key is too
  heavy — and beat the run-granularity competitor explicitly. Unfunded ≠
  refuted (premise failed; reasoning stands).
- Synergy map: Cluster 1 **closes** — the function producer resolved by
  dissolution; the library is unchanged, which is the rubric's best outcome
  (the feature fell out of the basis).
- The dissolution claim gets an executable pin in runstate:
  `ensure` over a 1-step completed-claiming producer computes once and hits
  cache on the second call — if that test cannot pass without new library
  code, the dissolution was wrong and this spec is refuted.

## Non-goals

- Any new worker class, library API, or schema change.
- Per-kind demand granularity (no consumer; the load dominates).
- A shipped freshness/staleness policy (the identity subsumes it).
- The index algebra (dormant, trigger above).

## Tests

- runstate (the dissolution pin): a 1-step hand-emitting producer behind
  `ensure(until={"step": 1})` — first call launches and returns a NON-EMPTY
  series; second call returns the same from the log with **no relaunch** (no
  second `lifecycle.started`).
- mycooc (with the wiring): derived-id stability (byte-identical read-set ⟹
  same rid ⟹ cache hit; ANY read file touched — including a metrics CSV
  grown by an extend with no new best — ⟹ new rid ⟹ recompute); the bundle
  round-trips through `value_series`; `ensure_analysis` twice ⟹ one
  compute; a crash-retry episode emits only the missing names.
