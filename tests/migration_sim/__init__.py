"""A generative model of conforming 0.3.0 log writers: the ground truth for the
0.3.0 -> 0.4.0 migration (commits-and-lineage.md §2 and §7).

A **history** (``history.py``) is what writers did, event by event: claims,
iterations, values, beats, saves, exits, kills, third parties' stops, stop
requests, launches and reaps. From one history the model gives:

- **its 0.3.0 log** (``render.py``), the rows a migration reads;
- **its truth** (``truth.py``), the lineage a native 0.4.0 writer would have
  recorded for the same history, in §2's terms.

Two histories that render one log but need different truths make that log
ambiguous. ``enumerate.py`` streams bounded families of histories for checking a
migration design against that, and ``build.py`` builds a claim's events from its
writer's structured choices.

The model knows nothing of any migration: it states what writers can do and
what they would have recorded natively, never how a migration infers it.
"""
