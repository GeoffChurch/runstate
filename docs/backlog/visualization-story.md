# visualization-story — own the data-plane protocols too

The v0.1 positioning treats runstate as the *control-plane* counterpart to
data-plane tools (wandb, MLflow, TensorBoard). Users run runstate + wandb
side-by-side: runstate for bidirectional control, wandb for metric viz.

This is a defensible division of labor for v0.1 — we're not trying to
out-wandb wandb on plotting and team sharing in our first release. But
"control plane is ours, data plane is theirs" is not the only viable
long-term shape, and treating it as permanent gates us out of being a
one-stop shop.

## What runstate-as-one-stop-shop would mean

Three additional protocols beyond v0.1's cooperative-control messages:

1. **Data-plane event protocol** — richer worker-to-orchestrator
   events than the current scalar Progress(metrics: dict[str, float]).
   Candidates: Histogram, Image, Audio, Tensor, Text. Each gets its own
   typed message + JSON Schema entry. Stored in the same Channel as
   control messages, or in a parallel "data" Channel if volume warrants.

2. **Viewer-discovery protocol** — how a UI finds available runs and
   subscribes to updates. Pairs with the deferred v0.2 Store: the Store
   answers "what runs exist?"; the viewer protocol answers "how do I
   subscribe to one?"

3. **Artifact-storage protocol** — for big blobs (checkpoint files,
   model exports, eval outputs). Currently out of scope; users put
   artifacts wherever they want. Could become first-class with a
   pluggable artifact-store Protocol (filesystem, S3, etc.) analogous
   to the Channel Protocol.

Plus a companion webapp / TUI that consumes all three protocols and
provides the visualization layer.

## The discipline

If we go this direction, each protocol stays in its own file under
`protocol/`. The control-plane protocol (v0.1) stays as-is — it's the
core asset and we don't muddy it with viz concerns.

- `protocol/messages-v0.1.schema.json` — control plane (current)
- `protocol/data-v0.x.schema.json` — data plane events (future)
- `protocol/viewer-v0.x.schema.json` — discovery/subscription (future)
- `protocol/artifacts-v0.x.schema.json` — blob storage interface (future)

This gives us forward composability: a user building a Rust orchestrator
that only cares about control implements just the v0.1 schema. A user
building a TUI that wants live metrics implements data + viewer. No
mandatory all-or-nothing.

## What this is NOT

- Not a competitor to wandb on the polish / team-sharing / SaaS axis.
  If you want a hosted dashboard with team management, wandb is fine.
- Not a tracker rewrite. We don't need to invent new metric types;
  Histogram / Image / etc. are well-understood shapes; we just need to
  pick a JSON-serializable encoding.
- Not v0.1, NOT v0.2. The Store + Hasher work in v0.2 is the
  prerequisite — without persistent run metadata, the viewer protocol
  has nothing to discover.

## When to revisit

Revisit when v0.2 ships (Store + reuse + sweep helpers). At that point,
the natural next question is "how does someone *see* the runs Store
knows about?" — and the answer might be "we have a viewer protocol and
a reference webapp," or it might be "use mlflow ui after exporting,"
depending on the audience we've found by then.

## Open questions

- Does the data plane go through the same Channel as control, or a
  parallel Channel? Parallel keeps high-volume metric events from
  swamping the low-volume control queue; same-Channel is simpler.
- How do we handle binary data (images, tensors) in a JSON wire format?
  Base64? Out-of-band references with a URI? CBOR for the data plane
  even though JSON for control?
- Do we need an explicit run-metadata protocol (start time, config,
  tags, name) or does v0.2's Store cover that?
- Where does the Hasher-based run identity intersect with the viewer's
  notion of "which run am I looking at"? Run-hash as the viewer key
  is clean but ties data plane to v0.2 Store machinery.

None of these are urgent; document them as we approach the work.
