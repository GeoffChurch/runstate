# visualization-story — a data-plane / viewer ecosystem on runstate (a SEPARATE project)

runstate is the *control-plane* counterpart to data-plane tools (wandb, MLflow,
TensorBoard): runstate for bidirectional cooperative control, wandb for metric viz.
That division of labor is defensible — we don't out-wandb wandb on plotting and
team sharing. But "control plane is ours, data plane is theirs" isn't the only
long-term shape; a **separate viz project built on runstate** could close the
data-plane gap without bloating runstate's core.

**These protocols do NOT belong in runstate** (see *The discipline*). runstate stays
the minimal cooperative-control protocol + substrate; the data-plane / viewer /
artifact protocols live in their own project that depends on it.

## What the data-plane / viewer project would add

Three protocols, in a separate project on top of runstate, beyond runstate's own
cooperative-control conventions:

1. **Data-plane event protocol** — richer worker-to-orchestrator
   events than the current scalar Progress(metrics: dict[str, float]).
   Candidates: Histogram, Image, Audio, Tensor, Text. Each gets its own
   typed message + JSON Schema entry. Stored in the same Channel as
   control messages, or in a parallel "data" Channel if volume warrants.

2. **Viewer-discovery protocol** — how a UI finds available runs and
   subscribes to updates. *(Re-keyed 2026-06-11: the Store dissolved —
   `../specs/store.md` — so "what runs exist?" is answered by the root
   set + content-addressed placement + pointers/birth records, and the
   viewer's need is the named promotion trigger for the provenance-record
   schema.)* The viewer protocol answers "how do I subscribe to one?"

3. **Artifact-storage protocol** — for big blobs (checkpoint files,
   model exports, eval outputs). Currently out of scope; users put
   artifacts wherever they want. Could become first-class with a
   pluggable artifact-store Protocol (filesystem, S3, etc.) analogous
   to the Channel Protocol.

Plus a companion webapp / TUI that consumes all three protocols and
provides the visualization layer.

## The discipline: a SEPARATE project, not runstate's `protocol/`

These protocols do **not** go in runstate — not even as separate files under its
`protocol/`. runstate's one asset is the cooperative-control protocol (the
topic-log substrate + the `control` / `lifecycle` / `launcher` / `value` convention
schemas); the data plane is a distinct concern (rendering, discovery, artifacts), a
distinct audience (viewers / UIs), and a distinct evolution timeline. Co-locating
them — even in their own files under runstate's `protocol/` — conflates the core
control protocol with a downstream viz opinion and creeps runstate toward "another
tracking tool."

Split by **project**, not by file:

- **runstate** — the cooperative-control protocol + the substrate the data plane
  rides on. The topic log already carries arbitrary `value` bodies; the observer
  plane folds them; the root set + content-addressed placement + pointers are the
  discovery surface. runstate gains *nothing* viz-specific.
- **a separate viz project** (its own repo, depending on runstate) — the typed
  rich-value bodies + schemas (Histogram / Image / Audio / Tensor / Text), the
  viewer-discovery / subscription protocol, the artifact-storage interface, and the
  companion webapp / TUI.

Forward composability still holds, now across project boundaries: a Rust
orchestrator that only needs control depends on runstate's schemas alone; a viewer
depends on the viz project (which depends on runstate). No mandatory all-or-nothing
— and runstate stays minimal and opinion-free.

## What this is NOT

- Not a competitor to wandb on the polish / team-sharing / SaaS axis.
  If you want a hosted dashboard with team management, wandb is fine.
- Not a tracker rewrite. We don't need to invent new metric types;
  Histogram / Image / etc. are well-understood shapes; we just need to
  pick a JSON-serializable encoding.
- Not v0.1, NOT v0.2. Persistent, discoverable run metadata is the
  prerequisite — satisfied 2026-06-11 by the dissolved relational layer
  (`../specs/store.md`); the remaining gate is a viewer audience.

## When to revisit

Revisit when a viewer audience exists (the metadata prerequisite is met:
placement + pointers + birth records are the discovery surface). The
natural next question is "how does someone *see* the runs the namespace
knows about?" — and the answer might be "a separate viz project (on runstate) has a
viewer protocol and a reference webapp," or it might be "use mlflow ui after
exporting," depending on the audience we've found by then.

## Open questions

- Does the data plane go through the same Channel as control, or a
  parallel Channel? Parallel keeps high-volume metric events from
  swamping the low-volume control queue; same-Channel is simpler.
- How do we handle binary data (images, tensors) in a JSON wire format?
  Base64? Out-of-band references with a URI? CBOR for the data plane
  even though JSON for control?
- Do we need an explicit run-metadata protocol (start time, config,
  tags, name)? (Partially settled: the worker's resolved-config record +
  `lifecycle.started` cover config/start; tags/names have no home — by
  design so far.)
- Where does content-addressed run identity intersect with the viewer's
  notion of "which run am I looking at"? Run-id as the viewer key is
  clean and is now also the run's *address* (`../specs/store.md`).

None of these are urgent; document them as we approach the work.
