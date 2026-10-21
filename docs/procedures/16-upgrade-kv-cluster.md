# 16 :: Upgrade a KV Cluster

**Animation:** [`../animations/16-upgrade-kv-cluster.html`](../animations/16-upgrade-kv-cluster.html)

| | |
|---|---|
| **Trigger** | Platform operator |
| **Workflow id** | `upgrade-kv-<cluster>` |
| **Structure** | [14](14-upgrade-hosted-cluster-control-plane.md) for the control plane, then a hypervisor-by-hypervisor worker roll |
| **Blast radius** | Every tenant virtual machine on the cell |

---

## 1 :: Why this is the most careful upgrade in the platform

A KubeVirt cluster's workers are hypervisors, and its control plane is pods on the Seed. The
control plane half is ordinary. The worker half is not: every node being upgraded is
carrying other people's running virtual machines, which are themselves worker nodes of
tenant clusters.

So a careless roll here looks to a tenant like their workers rebooting for no reason, in a
cluster they were not told was being touched.

---

## 2 :: Process flow

| # | Phase | Action | On failure |
|---|---|---|---|
| 1 | `preflight` | Cell healthy, shared storage healthy, no cell operation in flight | Terminal |
| 2 | `checking-headroom` | The cell can lose one hypervisor's worth of capacity at a time and still hold every guest | Terminal |
| 3 | `upgrading-control-plane` | [14](14-upgrade-hosted-cluster-control-plane.md) with the Seed as hub | Stop |
| 4 | `rolling-workers` | **For each hypervisor, one at a time:** cordon, live-migrate every guest off, verify empty, upgrade, wait for ready, wait for KubeVirt to be schedulable, uncordon | See below |
| 5 | `verifying` | Every node on the new version, every guest running, none stranded | Bounded |

### One at a time, and evacuate before upgrading

Both halves of that matter. Evacuation is what keeps the upgrade invisible to tenants, and
it is only possible because guest storage is shared. One at a time is what keeps the cell
able to absorb the evacuation.

### A guest that will not migrate stops the roll

Do not force it and do not skip the host. Stop, name the guest, let a human decide. The
alternative is an unannounced outage for whichever tenant owns it.

---

## 3 :: Interaction with the rest of the platform

While a cell is rolling, it has one less hypervisor of capacity than usual. Anything that
places new virtual machines needs to know, or it will fill the cell to a level that makes
the next evacuation impossible. Mark the cell as upgrading and have placement honour it.

The same mark should keep cell contraction and expansion from running concurrently. Two
workflows moving guests around one cell at once is a situation with no good outcome.

---

## 4 :: Open questions

1. Whether a tenant whose virtual machines are migrating should be told. Live migration is
   meant to be invisible, but it is not free, and a tenant debugging a latency spike
   deserves to know.
2. Whether the hypervisor roll may run unattended overnight. The evacuation step can stall
   on a single guest, and an unattended stall leaves a cordoned half-empty host until
   morning.
3. Where the upgrading mark lives and what honours it. Same question as the draining mark in
   [09](09-decommission-kv-cluster.md), and it should be the same mechanism.
