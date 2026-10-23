# A coordinator outage as a pause, not a death

**Status: DEFERRED; decided with GeoffChurch/mycooc's 12b.** Recorded 2026-10-23 as the long-term shape behind
mycooc's decision 12d ([review](../review-2026-10-04-commits-and-lineage.md) §12, item 15). There, the owner
accepted that a running job depends on the coordination server: with runs' logs on `PostgresChannel`, an
outage of the server ends every running job's session.

## What an outage costs today

A channel never reconnects, so a worker whose session drops raises at its next write and exits; mycooc exits
69. It cannot write its own exit either, so its claim stays held until someone with evidence reports it. The
cost is each running job's iteration in flight, plus a requeue and a resume. It is never a correctness
failure: there is no double claim, and no committed record is lost while `synchronous_commit` and `fsync` are
on. Buffering writes through the outage and replaying them would be a silent reconnect, carrying a claim
across a session gap while a death report and a successor may already be on the log. It is refused.

## The shape: re-claim after a lost session

1. The worker waits for the server, for a bounded time, keeping its process, its accelerator and its state.
2. It ends its own old claim by a death report, `lost`, on the session evidence that its old session has
   ended. That evidence outranks its own live pid, which is why this needs mycooc's 12b ruled first: without
   session evidence a worker cannot end a claim whose handle probes alive.
3. It claims afresh, resuming from its old claim's last node that landed. If the commit it was writing
   landed with its acknowledgement lost, that node is its own position. Otherwise it re-sends the iteration
   it holds as the new claim's first commit: its values were computed from that node, and its state is the
   state after them.
4. If a successor claimed first, its compare-and-append loses, and it exits.

The claim boundary stays visible: the old claim is ended by a report, and leases are cut as at any claim
boundary. It is a Worker-level recovery, and nothing in it is mycooc's.

## Where the server lives

The other half is placement, the consumer's: put the server where its failure coincides with one the jobs
already suffer (fate-sharing), so it adds no failure domain. mycooc's cluster login node was that, since it
fails with the Slurm controller, and it lost on 2026-10-22 on disk (22 GB free on the controller's VM),
CPU (4 vCPUs shared with `slurmctld`, `slurmdbd` and `mariadbd`) and the admins' consent. If those lift, it is
the long-term home. High availability is not the substitute: advisory locks are not replicated, so a failover
ends every session anyway, and it needs fencing against a split brain.
