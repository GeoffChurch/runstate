"""The protocol vocabulary (docs/design-v0.2.md §6-8).

The L2 **conventions** layer: the typed convention bodies (``payloads``), the
subscription condition-algebra (``schedule``), and the liveness-handle format
(``handle``) -- the terms an other-language implementer reimplements to interop.
Distinct from the L1 substrate (``channel``, the transport) and the L3
orchestration helpers (``launcher`` / ``watcher`` / ``sweep`` / ``worker``).

Import the submodules directly, e.g. ``from runstate.vocabulary.payloads import
Stopped`` or ``from runstate.vocabulary.schedule import satisfied``. The body
dataclasses are also re-exported from the package top level (``runstate``).
"""
