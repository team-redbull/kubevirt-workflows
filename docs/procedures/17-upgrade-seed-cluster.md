# 17 :: Upgrade a Seed Cluster

**Animation:** [`../animations/17-upgrade-seed-cluster.html`](../animations/17-upgrade-seed-cluster.html)

| | |
|---|---|
| **Trigger** | Platform operator, rarely |
| **Workflow id** | `upgrade-seed-<seed>` |
| **Structure** | [15 Upgrade MCE Hub](15-upgrade-mce-hub.md) with one extra consideration |
| **Blast radius** | The control planes of **every KubeVirt cluster** the Seed hosts |

---

## 1 :: What makes this different from any other hub

A Seed is installed with user-provisioned infrastructure and is the one cluster with nothing
underneath it. More importantly, its workers carry the control planes of all the KubeVirt
clusters in its zone. So rolling a Seed worker moves a KubeVirt cluster's API server.

What survives and what does not, while that happens:

| | Effect |
|---|---|
| Running tenant virtual machines | **Unaffected.** The data plane is independent of the control plane. |
| Creating, scaling or upgrading a KubeVirt cluster | Blocked |
| Inventory management for the zone | Blocked |

That split is the saving grace. A Seed upgrade pauses infrastructure management for a zone
but does not interrupt anything a tenant is running.

---

## 2 :: Process flow

As [15](15-upgrade-mce-hub.md), with these differences:

| # | Phase | Difference |
|---|---|---|
| 2 | `computing-skew` | The clusters to check are the **KubeVirt clusters** hosted here, not tenant clusters |
| 3 | `preflight` | Additionally: no cell operation in flight anywhere in the zone. An evacuation mid-Seed-upgrade has nowhere to report. |
| 5 | `pausing-automation` | Pause the controller on this Seed **and** anything acting on cells in this zone |
| 7 | `upgrading-workers` | Roll with surge, so hosted control planes move to a new node before the old one goes |
| 9 | `verifying` | Additionally: every KubeVirt cluster's API is reachable again |

---

## 3 :: Sequencing against everything else

The Seed is the top of its zone's stack, so the order within a zone is: Seed, then the
KubeVirt clusters it hosts, then the Customer MCEs, then tenant clusters. Doing it in any
other order risks a layer being newer than the one hosting it.

Across zones, do one zone at a time. A Seed being per zone is the only thing that keeps this
upgrade from being fleet-wide, and running two at once throws that away.

---

## 4 :: Open questions

1. **Whether a Seed should be per zone at all.** Its stated purpose is that a Seed failure
   affects only one zone, which assumed zones were physical failure domains. With soft
   zones they share hardware, so the isolation is not real while the cost, three bare-metal
   management clusters per site, is.
2. Whether a user-provisioned cluster can be upgraded by workflow at all, or whether the
   honest answer here is a written runbook a human follows.
3. What happens if a Seed upgrade fails halfway. Its risk register entry for Seed failure
   has never had a mitigation agreed, and this workflow is the most likely way to cause one.
