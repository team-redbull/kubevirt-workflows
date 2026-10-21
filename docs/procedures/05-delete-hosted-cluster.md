# 05 :: Delete Hosted Cluster

**Animation:** [`../animations/05-delete-hosted-cluster.html`](../animations/05-delete-hosted-cluster.html)

| | |
|---|---|
| **Trigger** | Customer, decommissioning a cluster |
| **Workflow id** | `delete-hosted-cluster-<cluster>` |
| **Runs on** | Git, the hub's queue, then the segments and DHCP queues |
| **Ends at** | Segment back in the pool, DHCP scope gone |

> Today this is not a workflow at all. Deletion happens by removing a chart, and a
> post-delete hook calls segment release. That makes the step with the clearest
> data-handling risk the one with the least orchestration. This document describes what
> should replace it.

---

## 1 :: Purpose

Tear a tenant cluster down and return everything it held: worker capacity to the pool, the
segment to its site, the DHCP scope to the server.

---

## 2 :: Inputs

| Field | Type | Required | Notes |
|---|---|---|---|
| `cluster` | str | ✅ | The cluster to remove |
| `hub` | str | ✅ | Which hub holds it |
| `values_branch` | str | ✅ | Branch to commit the removal on |
| `confirm` | bool | ✅ | Explicit, because this is not reversible |

---

## 3 :: Process flow

Order matters more here than anywhere else in the platform. Each step exists because the
next one is unsafe without it.

| # | Phase | Runs on | Action | Why here |
|---|---|---|---|---|
| 1 | `validating` | brain | Cluster exists on this hub; the caller confirmed | Nothing written yet |
| 2 | `draining` | hub queue | Cordon and drain tenant workloads, bounded | Gives workloads a chance to move before the nodes vanish |
| 3 | `removing-manifests` | git | Delete the HostedCluster and every NodePool file. One commit. | The file removal **is** the decommission |
| 4 | — | human | Merge to main | ArgoCD only reads main |
| 5 | `awaiting-prune` | hub queue | Wait until ArgoCD has removed every resource of the application | **Nothing below is safe while a resource still exists** |
| 6 | `awaiting-release` | hub queue | Wait for every Agent to become unbound | They are the capacity being returned |
| 7 | `scrubbing` | hub queue | Confirm each released machine has had its disk reset and re-registered, or is gone | **See section 4** |
| 8 | `deleting-scope` | dhcp queue | Delete the DHCP scope for the segment's network, then re-read to confirm absence | A live application would re-create it within a minute |
| 9 | `releasing-segment` | segments queue | Re-read the owner, then release. Already-available is success. | Returns the VLAN to the site pool |
| 10 | `verifying` | segments queue | Read back: available, no cluster, no type | |

### Why the scope is deleted before the segment is released

The scope is a consequence of the allocation. Release the segment first and the network can
be handed to another cluster while a scope for it still exists on the DHCP server. Deleting
the scope while the application still lives is equally wrong, because the GitOps layer
re-creates it. Hence steps five, eight, nine in that order.

---

## 4 :: The data-handling step

Worker machines released here ran a tenant's operating system and held tenant data on their
root disks. They are returning to a pool another tenant draws from.

| Outcome | Correct behaviour |
|---|---|
| Machine returns to the pool | Only after its disk is reset and it re-registers as a fresh Agent |
| Machine still mid-scrub | Must **not** appear as a spare agent |
| Pool now over its ceiling | [02](02-remove-server-from-mce.md) destroys the surplus later, not here |

Never-claimed agents booted only the discovery image, carry no tenant state, and need no
scrub.

**This step is currently unverified.** Nothing in the repositories specifies whether release
resets the disk. Until it does, treat step seven as a manual check rather than an automated
one.

---

## 5 :: Idempotency

Every step tolerates having already happened: a missing file, an absent scope, an already
available segment. A re-run after a partial teardown converges rather than failing.

---

## 6 :: Failure handling

| Condition | Treatment |
|---|---|
| Drain times out | Continue. A decommissioned cluster's workloads do not block its removal. |
| Prune incomplete | Retry. **Never proceed.** |
| Scope still present after delete | Retryable failure; an application is probably still alive |
| Another cluster now owns the segment | Terminal. Do not release someone else's allocation. |

---

## 7 :: Open questions

1. Whether drain should be skippable, and who may skip it.
2. Whether this workflow replaces the post-delete hook outright, or wraps it during the
   transition.
3. What is retained for audit after the cluster is gone, and for how long.
