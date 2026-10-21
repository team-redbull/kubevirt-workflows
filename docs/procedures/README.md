# Workflows

Eighteen procedures, grouped by layer. Each has a process flow an engineer can build from,
and an animation that walks the same flow step by step.

**Open `../animations/<name>.html` in any browser.** Single self-contained files, no
dependencies and no network calls, so they work air-gapped. Arrow keys step, space plays.
Each step shows the real call, payload and response beneath the diagram, and a deep link
like `#step=7` opens on one step.

They are generated from `../animations/_generate.py`. Edit a step there and re-run; do not
hand-patch the HTML.

---

## The shape of it

Two loops that touch only through observed state on a hub. Demand is written by customers
and lands in Git. Supply is maintained by the [Inventory Controller](../inventory-controller.md),
which reads demand, compares it to what exists, and asks for the difference.

```
CUSTOMER                        CONTROLLER
    |                                |
  04 update nodepool            reads demand + supply
  03 create cluster                  |
  05 delete cluster             01 add server    /  02 remove server
    |                                |
   Git -> ArgoCD -> hub  <-----  observed state
```

Nothing in the demand column waits for the supply column. A cluster can be created with no
workers available; it simply has none until the pool fills.

---

## Interfaces

| | Reference | Covers |
|---|---|---|
| 00 | [Interfaces and Data Exchange](00-interfaces.md) | Every call the workflows make, with real payloads: the inventory API and its install lock, the segment lookup, the PXE map, and every Kubernetes resource written or read |

Read it before implementing any procedure. The traps it documents, bonding from the wrong
MAC list, the two meanings of a conflict on the lock, and the two-condition image freshness
check, are all things the code learned the hard way.

## Inventory — hub-agnostic, pool-agnostic

| | Procedure | Trigger |
|---|---|---|
| 01 | [Add Server to MCE](01-add-server-to-mce.md) | Controller, pool below target |
| 02 | [Remove Server from MCE](02-remove-server-from-mce.md) | Controller, pool above ceiling for the cooldown |

One workflow each for virtual and bare metal. Only the first step differs: bare metal
**draws** an existing machine, virtual **creates** one. Everything after is identical
because both end as an Agent.

## Tenant clusters

| | Procedure | Trigger |
|---|---|---|
| 03 | [Create Hosted Cluster](03-create-hosted-cluster.md) | Customer |
| 04 | [Update NodePool](04-update-nodepool.md) | Customer |
| 05 | [Delete Hosted Cluster](05-delete-hosted-cluster.md) | Customer |

## KubeVirt clusters — the same workflows, with the Seed as the hub

| | Procedure | Composed of |
|---|---|---|
| 06 | [Create KV Cluster](06-create-kv-cluster.md) | 03 against a Seed, plus KubeVirt as day 2 |
| 07 | [Add BM to KV Cluster](07-add-bm-to-kv-cluster.md) | 04 then 01 |
| 08 | [Remove BM from KV Cluster](08-remove-bm-from-kv-cluster.md) | Evacuate, then 04 then 02 |
| 09 | [Decommission KV Cluster](09-decommission-kv-cluster.md) | Evacuate the cell, then 05 |

This is the load-bearing simplification. A KubeVirt cluster **is** a hosted cluster whose
control plane runs on the Seed. If 01 to 05 take the hub as a parameter, this whole layer is
configuration. If they assume a Customer MCE, it is a second implementation.

## Seeds and hubs

| | Procedure | Note |
|---|---|---|
| 10 | [Create Seed Cluster](10-create-seed-cluster.md) | User-provisioned, largely manual |
| 11 | [Add BM to Seed Cluster](11-add-bm-to-seed-cluster.md) | The one cluster that is not hosted |
| 12 | [Create Customer MCE](12-create-customer-mce.md) | User-provisioned on virtual machines |

## Upgrades — top down

| | Procedure | Order |
|---|---|---|
| 17 | [Upgrade Seed Cluster](17-upgrade-seed-cluster.md) | 1st in a zone |
| 16 | [Upgrade KV Cluster](16-upgrade-kv-cluster.md) | 2nd |
| 15 | [Upgrade MCE Hub](15-upgrade-mce-hub.md) | 3rd |
| 14 | [Upgrade Hosted Control Plane](14-upgrade-hosted-cluster-control-plane.md) | 4th |
| 13 | [Upgrade Hosted Workers](13-upgrade-hosted-cluster-workers.md) | 5th |

Nothing may run a version newer than whatever hosts it, so the fleet upgrades downward.
Control planes and workers are split because unpredictable workload drain must never delay
API availability.

## Migration

| | Procedure | Note |
|---|---|---|
| 18 | [Migrate Version Band](18-migrate-hosted-cluster-version-band.md) | A rebuild, not a move |

---

## What changed from the earlier list

| Was | Now |
|---|---|
| Inventory replenishment, decommission, controller reconciliation | The controller became [its own document](../inventory-controller.md); the two actions became 01 and 02, hub- and pool-agnostic |
| Scale up and scale down as separate procedures | One, [04](04-update-nodepool.md), because the direction is just a number |
| Node quarantine | **Dropped.** With no overcommit, parking a virtual machine wastes dedicated capacity for a guest that should be rebuilt. The real need is excluding a bad hypervisor, which belongs with the KubeVirt cluster layer. |
| Upgrade as one item | Five, because the constraints differ completely at each layer |
| — | Added 09, 11 and 18, which had no procedure and no owner |

---

## Cross-cutting notes

**Absolute, never deltas.** Every count that crosses a boundary is the desired total. A
delta needs the caller to know current state and makes concurrent requests lossy.

**Workflow ids are the dedup key.** Stable per target, with the mutable value in the
payload. An id containing the target defeats the mechanism entirely.

**Create, verify, never overwrite.** An existing resource is success once its identity
fields match, and a conflict for a human when they do not, so an operator's hand correction
survives a re-run.

**Teardown order is load-bearing.** Detach before delete, then clear only the one finalizer.
Reordering it for tidiness makes deletion hang forever against a BMC that is not answering.
