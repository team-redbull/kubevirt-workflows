# 07 :: Add Bare-Metal Node to a KV Cluster

**Animation:** [`../animations/07-add-bm-to-kv-cluster.html`](../animations/07-add-bm-to-kv-cluster.html)

| | |
|---|---|
| **Trigger** | Operator, or a cell-capacity policy, when a KubeVirt cluster is near its node cap |
| **Composed of** | [04 Update NodePool](04-update-nodepool.md) then [01 Add Server to MCE](01-add-server-to-mce.md) |
| **Nothing new** | This workflow has no steps of its own |

---

## 1 :: Why this is not a new workflow

A KubeVirt cluster is a hosted cluster whose control plane runs as pods on the Seed and
whose workers are bare-metal machines. Adding a node to it is therefore the same two things
as adding a worker to any tenant cluster, with different parameters:

| | Tenant cluster | KV cluster |
|---|---|---|
| Hub | a Customer MCE | the zone's **Seed** |
| Pool type | usually `VM` | always `BM` |
| Worker role | runs tenant workloads | runs KubeVirt, hosts other tenants' VMs |

If 01 and 04 take the hub as a parameter rather than assuming a Customer MCE, this document
is configuration. If they do not, you will write them twice.

---

## 2 :: Sequence

| # | Call | Parameters |
|---|---|---|
| 1 | [04 Update NodePool](04-update-nodepool.md) | `cluster` = the KV cluster, `hub` = the Seed, `node_type` = the bare-metal class, `desired_total` = current + N |
| 2 | — | The Seed's NodePool controller claims free Agents from the Seed's bare-metal pools |
| 3 | [01 Add Server to MCE](01-add-server-to-mce.md) | Triggered by the controller on the **Seed**, `pool_type` = `BM`, once demand exceeds supply |
| 4 | `awaiting-kubevirt` | Wait for the new node to report `Ready` **and** for KubeVirt to be schedulable on it |

Step four is the only thing this layer adds. A node that has joined is not yet usable
capacity: the KubeVirt DaemonSet has to be running on it before any guest can be placed
there.

---

## 3 :: Preconditions

- The Seed for that zone exists and has bare-metal pools with free machines, or machines
  available in the inventory for the controller to draw.
- The cell is below its node cap. Crossing it means [06 Create KV Cluster](06-create-kv-cluster.md),
  not this.

---

## 4 :: Open questions

1. **The node cap needs one unit.** It has been stated both as a count of hypervisors and as
   a count of guests, and at the current ratio those differ by almost an order of magnitude.
   Pick one and put it in the architecture.
2. Who decides to run this. A per-hub controller cannot see cell utilisation across
   KubeVirt clusters, so cell capacity has no owner today. An alert plus a human is a
   defensible first answer.
3. Whether adding a hypervisor should trigger a rebalance of existing guests, or only affect
   new placement. Only affecting new placement is simpler and probably right.
