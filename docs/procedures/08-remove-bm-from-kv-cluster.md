# 08 :: Remove Bare-Metal Node from a KV Cluster

**Animation:** [`../animations/08-remove-bm-from-kv-cluster.html`](../animations/08-remove-bm-from-kv-cluster.html)

| | |
|---|---|
| **Trigger** | Operator, for maintenance, decommissioning, or a shrinking cell |
| **Composed of** | An evacuation, then [04 Update NodePool](04-update-nodepool.md), then [02 Remove Server from MCE](02-remove-server-from-mce.md) |
| **Adds** | One genuinely new step: evacuating the guests |

---

## 1 :: The one thing this layer adds

A hypervisor is not an ordinary worker. Removing it takes down every tenant virtual machine
running on it unless they are moved first. That evacuation is the whole difficulty, and it
is why this is not simply 02 with different parameters.

Live migration is available because guest storage is shared, so evacuation should be
non-disruptive. It is also the step most likely to stall.

---

## 2 :: Sequence

| # | Phase | Action | On failure |
|---|---|---|---|
| 1 | `checking-headroom` | Confirm the remaining hypervisors in the cell can hold this one's guests, with the platform's spare-capacity margin intact | Terminal. Removing it would oversubscribe the cell. |
| 2 | `cordoning` | Mark the node unschedulable so no new guest lands on it | Retry |
| 3 | `evacuating` | Live-migrate every guest off, one at a time, bounded per guest | See below |
| 4 | `verifying-empty` | Confirm no guest instances remain | Anything left → stop, do not proceed |
| 5 | `shrinking-nodepool` | [04](04-update-nodepool.md) against the Seed, total minus one | Retry |
| 6 | `releasing` | [02](02-remove-server-from-mce.md) tears down the host resources and returns the machine | Retry |

### When a guest will not migrate

Stop. Do not force it. A guest that cannot migrate is usually pinned by something real: no
shared storage for one of its volumes, a device that cannot move, or simply no target with
room. Killing it to finish the workflow turns a maintenance operation into a tenant outage.
Fail the run with the guest named and let a human decide.

---

## 3 :: Preconditions

- The cell has enough spare capacity to absorb this node's guests. The platform reserves a
  margin of untouched hypervisor capacity precisely so evacuation is always possible; this
  step is where that reserve earns its keep.
- Shared storage is healthy. Without it live migration cannot happen at all and the only
  options are disruptive.

---

## 4 :: Open questions

1. The capacity margin is stated in the architecture but nothing enforces it, and nothing
   reconciles it against the inventory buffer, which reserves capacity for a different
   reason. Two reserves, one pool, no arbitration.
2. Whether a node being removed for hardware failure should skip evacuation. If the host is
   already unhealthy its guests may be unmigratable, and the honest answer may be to accept
   the outage and rebuild those workers elsewhere.
3. Whether this should be callable by the capacity policy automatically, or stay operator-driven.
   Given that it can disturb running tenants, operator-driven is the safer default.
