# The protocol algebra — the constructions behind the layer interfaces

**Status:** drafted 2026-06-09 out of a design conversation (organic-development
retrospective). **Placement is deliberately unsettled** — candidate homes:
a design appendix (in `../design-v0.2.md` or standalone), partial incorporation
into `../overview.md` (reader-facing form of the decision rules), and the seed
of the **protocol-implementer's guide** (Documentation section of the
[index](index.md)). Until that settles, this file is the canonical draft.

**Purpose.** The orthonormal-basis rubric (`CLAUDE.md` "Design rigor") *audits*
primitives post-hoc; the constructions here are what *generate* the rubric's
claims — and, where they earn their place, they yield **decision rules**.
Admission test (the same one applied to primitives): a formalism earns a place
iff it **decides design questions**. Each section ends with the rule it yields
and what it has decided or retrodicted. The negative space — formalisms
rejected as decorative — is kept at the bottom, because it is half the content.

---

## L1 — the log is the free monoid; initiality decides the surface

The per-run log over the envelope alphabet `E` is `E*`: the **free monoid /
initial list-algebra**. The universal property is the precise form of the
rubric's "initial among communication views, *under full retention*": every
"view with an update action" receives a **unique fold** (catamorphism) from the
log — register / queue / mailbox / counter / bounded-window are exactly design
§4's "read projections," and their uniqueness is why they are query-shaped
rather than primitives. (Honesty caveat, kept: GC/compaction *quotients* the
log and breaks initiality — which is why retention-until-cleanup is the
contract and GC is an eyes-open §12 deferral.)

Caller-owned cursors are initiality seen from the other side: the substrate
exposes the initial algebra *and no fold state* — keeping every fold (and its
resumption token, the cursor) caller-side is what "no per-reader state in the
substrate" (§4 Contract) amounts to.

**The CAS is the lone non-fold.** `send(expected_seq=)` is a *guarded
extension* — append iff the frontier is `S` — i.e. a new atomic transition on
the state, not a query of it. That is why it alone needed a normative
concurrency contract (design §4 rev 5: one critical section across handles and
processes; `None` = provably lost; raise = indeterminate fault — the canonical
projection of "what happened to my extension attempt").

**Decision rule (L1).** A proposed substrate operation is either
(a) **a fold** → a derived helper, never a primitive (`latest` is admitted only
as a *memoized* fold, on backend-optimization grounds — §4), or
(b) **a new atomic transition** → it must justify itself the way the CAS did,
contract and conformance tests included.
*Decided:* `send` / `read`(+cursor) / `latest` / CAS is **complete**; anything
else proposed for the Channel surface gets reclassified or rejected by this
rule.

## L2 — conventions are a designated-elimination discipline; the discharge rule is the context Γ

Every control-plane fact is an **intro/elim pair**:

| intro | designated eliminator | multiplicity |
|---|---|---|
| `lifecycle.started` | `lifecycle.stopped` | obligation (worker must eventually emit; `launcher.terminated` is the external backstop) |
| `launcher.launched` | `launcher.terminated` | obligation (launcher viewpoint) |
| `control.subscribe` | `control.unsubscribe` | **affine** — may never be consumed (standing state) |
| `control.stop` | the next `lifecycle.stopped` (`../specs/stop-discharge.md`) | **linear** — must be consumed exactly once |

Per-pair multiplicity varies, but the load-bearing invariant is uniform:
**every fact has a designated eliminator** — *what consumes it is fixed by the
convention, never ad hoc*. The convention folds then compute
**Γ = the multiset of introduced-but-not-yet-consumed facts**, which is exactly
the converged discharge rule — "every control fact is live until its
counter-record" *is* the linear-logic context. Pure `value` events are the
no-obligation case (no eliminator, no entry in Γ).

**Retrodiction 1 — F2 (mycooc audit) is a type error.** The one-shot stop had
*no designated eliminator*: its consumption was an ephemeral return value
(`tick() → True`, once). An intro rule with no elim rule is ill-typed under the
discipline; the stop-discharge spec's fix is precisely "designate the elim."
The lens would have caught this at design time, before a consumer hit it.

**Retrodiction 2 — the refuted A2 is a category error.** Cursor-fencing scoped
a resource by *position* (the worker's cursor) rather than by *consumption*.
Position is not intrinsic to the log's universal property — folds don't know
where any reader stands — which is exactly why it broke pre-staged stops.
Consumption-scoping is fold-stable. Three design reviews re-derived this the
hard way.

**Decision rule (L2).** A new convention message must arrive as an intro/elim
pair with a designated discharge (multiplicity declared), **or** be a pure
value carrying no obligation. Anything else is ill-typed — sharper and more
checkable than the rubric's prose form of the same instinct.

**Corollary — the commutativity upgrade.** A fold over matched pairs is
order-sensitive only *through the matching*; keying the pairs (discharge-by-
`request_id`, see the backlog [index](index.md) protocol-extensions entry)
turns Γ into a join-semilattice — the CRDT / multi-writer / replicated-log
direction. One lens, and the "galaxy-scale" analysis (run-local total order +
causal asynchrony between homes) drops out as a corollary.

## L2 addendum (2026-06-10) — the pairing-by-`seq` rule, named

The intro/elim pairs are *positionally* paired: a standing fact's eliminator
must **follow it by `seq`** (design §7 now states it once; instances: stop ↔
next `stopped`; subscribe ↔ next unsubscribe-or-nak bearing its `request_id` —
the **answer fold**, public home `observables.live_demand`; episode
terminality ↔ no opener following the terminal). The elimination is
author-blind — the worker writing the expiry `control.unsubscribe` applies
the same affine eliminator a client's rescind does (gc and `free` share an
opcode), which is why no `lifecycle.expired` constructor exists: every
consumer would immediately quotient the two, so the minimal generator set is
the initial vocabulary. Enforced worker-side as *registered ⟺ a future fire
is possible* (`specs/service-worker.md`).

## L3 — fold observers separately; join only at the verdict

`lifecycle.*` (self-report) and `launcher.*` (external report) are independent
**partial observers** — the rubric's "two viewpoints" orthogonality.
`RunResult.outcome` is the canonical projection of their *join* into a closed
verdict lattice (which is why there is no `success` bool: the enum **is** the
projection).

**Decision rule (L3).** Never merge observers *in the data*; take the join *at
the edge* (the verdict). Defends, e.g., against "just write the `terminated`
into `lifecycle.*`" — a proposal this rule rejects without discussion.

## The negative space — formalisms rejected as decorative

- **Multiparty/asynchronous session types.** They want a global choreography
  with fixed roles; the substrate's bet is opinion-freedom — opt-in
  conventions, open party set, compose-your-own loop from raw `send`/`read`.
  Typing the choreography would re-impose exactly the opinion the two-layer
  split exists to avoid.
- **Heavier categorical apparatus** (fibrations of typed views over raw logs,
  adjunction diagrams for L1↔L2). Restates the refinement relation without
  deciding anything. Fails the admission test.
- **The mechanically checked type theory of this project is the schema stack.**
  `additionalProperties: false` is a deliberate refusal of width subtyping
  (closed records, no silent extension). Any formal story must bottom out as
  *justification of the schemas*, not float beside them.
- **Standing counterexamples against over-formalizing** (kept in `CLAUDE.md`):
  the heartbeat is deliberately *enriched* (`{step, consumed_seq}`), not
  Unit/terminal; the condition-algebra is deliberately the *free* term algebra,
  not a normal form (conditions are never compared or hashed).

## Open questions / next steps

- [ ] **Placement:** design appendix vs `overview.md` incorporation vs both.
  The three decision rules are the reader-facing payload; the retrodictions
  and negative space may be appendix-only material.
- [ ] **Implementer's guide seed:** an implementer who knows "L2 = designated
  intro/elim pairs + discharge folds" can re-derive most of the conventions —
  fold this into the planned protocol-implementer's guide.
- [ ] **Uninvestigated lens — effects & handlers:** the Worker surface as an
  effect signature (yield value / check stop / heartbeat) with launchers as
  handlers; possibly the right shape for worker SDKs in other languages.
  Unvetted; apply the admission test before adopting.
