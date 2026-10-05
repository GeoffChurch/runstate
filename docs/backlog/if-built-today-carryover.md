# What carries over from if-built-today into runstate

**Status:** LIVING (opened 2026-10-04 with the owner). [if-built-today](if-built-today/README.md) is a
greenfield design: a monotone store of polarised literals. This entry tracks which of its ideas should
shape runstate *incrementally*, and what each would change.

Layers 1 and 2 already adopted the largest one: **identity is data, never position.** That is
reference by name, and commits and lineage.

| Idea (if-built-today file) | Status in runstate | What it would change |
|---|---|---|
| Identity is data (README) | **adopted:** layers 1–2 | — |
| Settledness: a query can know it is finished (`2-polarity.md`) | **adopted into layer 2** (decision 15) | reads return `SettledShort`, never waiting forever on a finished stream |
| Demand as `asked` facts (`3-questions.md`) | **next layer** ([demand-streams](demand-streams.md)) | replaces subscriptions; brings `ensure` and lazy launch under one primitive; demand subsumption; the residual |
| Identity kept through aggregation (`4-aggregation.md`) | open | cross-run summaries say which rows contributed |
| Objections, not retraction (`3-provenance.md`) | partly adopted (layer 1) | corrections name what they contest |
| Naming the non-monotone core (`1-logic.md`) | open | an explicit list of where runstate pays for coordination |

## Identity kept through aggregation

**The idea** comes from if-built-today's contextuality argument, after Morton (2017), and its
`4-aggregation.md`. Gluing rows **by identity** cannot produce views that are locally consistent but globally
impossible: every compatible family has a global gluing. **Aggregating identity away** performs the
"forgetting map" that makes such views possible.

**For runstate:**
- `aligned()` (layer 2) is a join on identity, so it is safe.
- Summaries across runs, in the cockpit or a separate viz project, should carry **which rows contributed**
  to each figure. A table of "best loss per config" should keep the run, node and stream behind each cell.

**What would refute it:** a consumer summary that is useful only without its contributors, and whose
contributors cannot be kept at reasonable cost.

## Objections, not retraction

**The idea** is from `3-provenance.md`. Nothing is ever taken back. A correction is a record that **names
what it contests**, and readers adjudicate under a stated policy.

**For runstate:**
- Layer 1 moved most of the way: releases, answers and discharges name what they act on.
- **What remains:**
  - the reclaim tool's release of a stranded claim;
  - a forged or mistaken verdict.

  Each would become an objection record naming the claim or verdict it contests. With signatures
  ([authenticated-records](authenticated-records.md)), the adjudication policy can weigh *who* objected.

## Naming the non-monotone core

**The idea** is from `1-logic.md`: monotone parts are free of coordination (CALM), and the non-monotone core
is exactly where coordination is paid. Name that core, and every later design question gets easier.

**runstate's non-monotone core, as a first list:**
- **the claim CAS:** the one arbiter, ordering claims;
- **lease expiry and voiding:** a lease ends because time passed, or because an episode ended;
- **"latest claim" reads:** `peek_terminal`, `live_episode` and the default head;
- **register reads:** a register's current value, which layer 2 confines to offered streams sampled under
  demand;
- **the dying breath:** a CAS over a fully read control tail.

**Next step:** confirm or extend the list against the code, then record it in `design-v0.2.md` as the
coordination budget.
