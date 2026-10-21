# 04 :: Update NodePool

**Animation:** [`../animations/04-update-nodepool.html`](../animations/04-update-nodepool.html)

| | |
|---|---|
| **Trigger** | Customer, changing a worker count up or down |
| **Workflow id** | `update-nodepool-<cluster>-<node-type>` — one in flight per pair |
| **Runs on** | Git, then the hub's queue to watch convergence |
| **Writes** | Git only |
| **Ends at** | ArgoCD synced. **Not** at nodes ready. |

> **This is the only place a customer's desired worker count enters the system.** Everything
> downstream is a consequence. This workflow never provisions, never destroys, and never
> talks to the inventory.

---

## 1 :: Purpose

Set the desired replica count for one cluster and one worker type, spread across the three
zones. Create the NodePools first if this worker type is new to the cluster.

---

## 2 :: Inputs

| Field | Type | Required | Notes |
|---|---|---|---|
| `cluster` | str | ✅ | Existing hosted cluster |
| `node_type` | str | ✅ | e.g. `32-128` |
| `desired_total` | int | ✅ | **Absolute total across all three zones**, never a delta |
| `values_branch` | str | ✅ | Branch to commit on |

Absolute rather than delta is deliberate. A delta requires the caller to know the current
state and makes two concurrent requests lossy. An absolute value is idempotent, and the last
one wins without any coordination.

---

## 3 :: Process flow

| # | Phase | Runs on | Action | On failure |
|---|---|---|---|---|
| 1 | `validating` | brain | `desired_total >= 3`, cluster exists, pools exist for this type in all zones | Terminal. Below three, tell the caller to delete instead. |
| 2 | `reading-current` | git | Read the current replicas for all three zones in one clone | Retry |
| 3 | `computing` | brain | Spread the total evenly; earlier zones take the remainder | — |
| 4 | `checkpoint` | brain | If a newer request arrived while reading, pivot to it now | — |
| 5 | — | brain | No change? Complete without committing. | — |
| 6 | `creating-or-updating` | git | Create the three manifests if absent, else patch `spec.replicas`. One commit. | Retry |
| 7 | — | human | Merge to main | Out of band |
| 8 | `awaiting-sync` | hub queue | Wait for ArgoCD healthy and synced | Bounded wait, then fail |
| 9 | `completed` | — | Report the per-zone distribution | |

### Distribution

```
base   = desired_total / 3
extra  = desired_total % 3
zones sorted; the first `extra` zones get base + 1
```

Deterministic, so the same total always yields the same layout regardless of current state.

---

## 4 :: What happens next, and why it is not here

**Scaling up.** The NodePools now want more agents than exist. The hub's NodePool controller
claims whatever is free. The Inventory Controller sees demand rise, computes a new target
and triggers [01](01-add-server-to-mce.md). If the warm pool was deep enough, the nodes are
already there.

**Scaling down.** Agents are released and become unbound, back in the pool, available to the
next cluster. Nothing is destroyed. Only if that pushes the pool above its ceiling, and it
stays there through the cooldown, does [02](02-remove-server-from-mce.md) run.

This separation is the point. Demand and supply are two loops that touch only through
observed state on the hub.

---

## 5 :: Idempotency

- The workflow id is the cluster and node type, so concurrent requests for the same pair
  serialize rather than racing on one Git file.
- The written value is absolute, so a replay produces the same manifest.
- A commit is skipped entirely when the computed distribution already matches.

---

## 6 :: Gaps

1. **No capacity pre-flight.** A total the pools cannot absorb is committed anyway and the
   NodePool waits forever. Same gap as [03](03-create-hosted-cluster.md).
2. **No upper bound.** Nothing stops a request that exceeds the cell's node cap.
3. The merge to main is manual, so the time from request to running node is not predictable
   from inside this workflow.
