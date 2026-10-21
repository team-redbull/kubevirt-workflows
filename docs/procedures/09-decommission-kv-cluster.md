# 09 :: Decommission a KV Cluster

**Animation:** [`../animations/09-decommission-kv-cluster.html`](../animations/09-decommission-kv-cluster.html)

| | |
|---|---|
| **Trigger** | Capacity policy or operator, when a cell has been under-used for a sustained period |
| **Composed of** | A fleet-wide evacuation, then [05 Delete Hosted Cluster](05-delete-hosted-cluster.md) against the Seed, then returning the hypervisors |
| **Reclaims** | A whole cell's worth of bare metal |

---

## 1 :: Purpose

Empty and remove a KubeVirt cluster, returning its hypervisors to the bare-metal inventory.
This is cell contraction: the counterpart to creating a cell when one fills up.

It is the most disruptive workflow in the platform, because every tenant virtual machine on
the cell has to move somewhere else first.

---

## 2 :: Preconditions

- The cell has been below its low threshold continuously for the cooldown. A single quiet
  afternoon is not a reason to tear down a cluster that takes hours to rebuild.
- Another cell **in the same zone** has room for every guest on this one, with the spare
  capacity margin still intact afterwards.
- Shared storage is healthy, so guests can migrate rather than being rebuilt.

---

## 3 :: Process flow

| # | Phase | Action | On failure |
|---|---|---|---|
| 1 | `confirming-sustained` | Re-check the cell is still under threshold and has been throughout the cooldown | Recovered → complete as a no-op |
| 2 | `finding-targets` | Identify destination cells in the same zone with enough headroom for every guest | Nowhere to go → terminal |
| 3 | `marking-draining` | Mark the cell so nothing new is placed on it | Retry |
| 4 | `evacuating` | For each hypervisor: cordon, live-migrate its guests to a target cell, verify empty | A guest that will not move stops the run, named |
| 5 | `verifying-empty` | No guest instances anywhere on the cell | Anything left → stop |
| 6 | `deleting-cluster` | [05](05-delete-hosted-cluster.md) with `hub` = the Seed and `cluster` = this KV cluster | Retry |
| 7 | `returning-hypervisors` | Each bare-metal node returns to the Seed's inventory, or is destroyed if the pool is over its ceiling | Retry |
| 8 | `releasing-segment` | Handled inside step 6 | — |

### Cross-cell migration is the assumption to check

Step four assumes a guest can migrate from one KubeVirt cluster to another. Within a cluster
that is ordinary live migration. **Across clusters it is a different operation**, and
whether it works depends on the two sharing a storage backend and the networking being
reachable from both. If they do not, evacuation becomes rebuild, and this workflow becomes a
tenant-visible event rather than a quiet one.

Resolve that before building step four. It changes the workflow from maintenance to an
outage.

---

## 4 :: Idempotency

Each hypervisor is evacuated independently and the run can resume mid-way: already-empty
hosts are skipped, already-deleted resources are success. Marking the cell draining is the
only state that must survive a restart, and it lives on the cluster rather than in the run.

---

## 5 :: Open questions

1. **Does cross-cluster live migration work here at all?** Everything above depends on it.
   If it does not, say so and design the rebuild path instead of pretending.
2. Where the draining mark lives, and what reads it. The per-hub controller cannot see
   cells, so something fleet-level has to both set it and honour it.
3. Whether tenants are told. A silent migration is nicer when it works and much worse when
   it does not.
4. Hysteresis. A cell that is torn down and rebuilt a week later cost more than it saved,
   so the cooldown here should be long, measured in days rather than minutes.
