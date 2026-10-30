"""The staged live layer of log format 0.4.0 (docs/specs/commits-and-lineage.md).

STAGING ONLY. ``docs/plans/2026-10-28-layer2-live.md`` builds the 0.4.0 live layer
here, beside the unchanged 0.3.0 modules, in a package that mirrors the final
layout, so every commit stays green while the Worker, the reads and their tests
move over a task at a time. Its tests are ``tests/test_next_*.py``. Task 19, the
cutover, moves each module here over its 0.3.0 namesake and deletes this package.

Modules here import each other relatively (``.x`` in this package, ``..x`` from
``vocabulary/``) and reach unchanged package modules one level further up, never
through an absolute ``runstate.`` import, so the cutover's rewrite is mechanical.
"""
