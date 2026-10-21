# 11 :: Add Bare-Metal Node to a Seed Cluster

**Animation:** [`../animations/11-add-bm-to-seed-cluster.html`](../animations/11-add-bm-to-seed-cluster.html)

| | |
|---|---|
| **Trigger** | Operator, when a Seed is short of capacity for the control planes it hosts |
| **Not** | A NodePool change. The Seed is the one cluster in the platform that is not hosted. |
| **Frequency** | Rare. A Seed grows when the number of KubeVirt clusters in its zone grows. |

---

## 1 :: Why this one is different

Every other cluster in the platform is a hosted cluster, so adding a worker means changing a
NodePool and letting the inventory fill it. The Seed is installed with user-provisioned
infrastructure and has no NodePool, no hosted control plane and nothing above it to ask.

It is also the bootstrap: the Seed hosts the control planes of the KubeVirt clusters in its
zone, so it cannot be built on the very capacity it provides. That is why it stays bare
metal and user-provisioned.

---

## 2 :: What the Seed carries

The control plane pods of every KubeVirt cluster in its zone, plus the operators needed to
provision them. It runs out of room when the zone gains KubeVirt clusters, not when tenants
grow, so demand here is a slow function of fleet size rather than of customer activity.

---

## 3 :: Process flow

| # | Phase | Action | Notes |
|---|---|---|---|
| 1 | `selecting-machine` | Draw a healthy unclaimed machine from the inventory and reserve it | Same draw and lock as [01](01-add-server-to-mce.md) |
| 2 | `preparing-network` | Resolve the Seed's network, write the host's network configuration | The Seed's own network, not a tenant VLAN |
| 3 | `joining` | Boot the machine so it joins the Seed as a worker | **See the open question. This is the undecided step.** |
| 4 | `approving` | Approve the node's certificate requests if the join path needs it | Only applies to the ignition path |
| 5 | `awaiting-ready` | Wait for the node to report ready and its workloads to schedule | Bounded |
| 6 | `recording` | Reflect the machine as in use so nothing else draws it | Via the inventory's own reporting cycle |

---

## 4 :: The decision this procedure is blocked on

There are two ways to add a worker to the Seed and the platform has not chosen one.

**Through the Agent flow.** The Seed already runs the operators needed, so it could use the
same mechanism as everything else: an InfraEnv on the Seed, a BareMetalHost, a discovery
image, an Agent. Nearly all of [01](01-add-server-to-mce.md) is reusable and the Seed stops
being a special case.

**Through ignition, as a classic user-provisioned node.** Simpler to reason about, no
dependency on the Seed's own operators being healthy, and it keeps the bootstrap layer free
of anything that could fail circularly. But it is a second mechanism to maintain, and it is
manual.

The second is safer for the bootstrap layer and the first is cheaper to build. The
deciding question is whether you are willing for the Seed's ability to grow to depend on
services running on the Seed itself.

---

## 5 :: Open questions

1. Choose the join mechanism above. Everything else here is downstream of it.
2. Whether this should be a workflow at all, given how rarely it runs. A written procedure
   that a human follows may be the right answer for a long time.
3. Whether the Seed is per zone or per site. Per zone was decided when zones were expected
   to be physical failure domains; with soft zones that rationale does not hold and it
   triples the bare-metal management footprint.
