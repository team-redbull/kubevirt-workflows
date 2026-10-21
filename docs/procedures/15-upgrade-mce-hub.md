# 15 :: Upgrade an MCE Hub

**Animation:** [`../animations/15-upgrade-mce-hub.html`](../animations/15-upgrade-mce-hub.html)

| | |
|---|---|
| **Trigger** | Platform operator |
| **Workflow id** | `upgrade-hub-<hub>` |
| **Applies to** | A Customer MCE or a Seed, both of which are standalone clusters |
| **Blast radius** | Every hosted cluster on that hub |

---

## 1 :: Purpose

Move a hub to a newer OCP and MCE version, which raises the ceiling on what the clusters it
hosts may run.

This is the top of the upgrade order. Nothing a hub hosts can go past it.

---

## 2 :: The check that makes this workflow non-trivial

Before touching anything, compute the version skew against **every** hosted cluster on the
hub, in both directions.

| Direction | Question |
|---|---|
| Upward | Does the new hub version still support the oldest cluster it hosts? |
| Downward | Does it unblock the clusters that are waiting for a newer release? |

A hub supports a window of versions below its own, so upgrading too far strands the clusters
at the bottom of that window. They must move first, via
[18 Migrate Version Band](18-migrate-hosted-cluster-version-band.md), or the upgrade has to
wait for them.

This is the real work of this workflow. The upgrade itself is ordinary.

---

## 3 :: Process flow

| # | Phase | Action | On failure |
|---|---|---|---|
| 1 | `inventorying` | List every hosted cluster on the hub with its current version | Retry |
| 2 | `computing-skew` | Check the target against the oldest and newest hosted cluster | Any cluster would be stranded → terminal, named |
| 3 | `preflight` | Hub healthy, quorum intact, no degraded operators, no in-flight operation on any hosted cluster | Terminal |
| 4 | `backing-up` | Back up hub etcd and verify it | Terminal if unverifiable |
| 5 | `pausing-automation` | Stop the Inventory Controller on this hub from acting during the upgrade | Retry |
| 6 | `upgrading-control-plane` | Roll the hub's masters one at a time, quorum held throughout | Bounded, stop on loss |
| 7 | `upgrading-workers` | Roll the hub's workers with surge | Bounded |
| 8 | `verifying` | Version reported, operators healthy, every hosted control plane still available | Bounded |
| 9 | `resuming-automation` | Re-enable the controller | Must happen even on failure |

---

## 4 :: Why the controller is paused

The hub's workers carry the hosted control planes of its tenants and, on a Seed, of its
KubeVirt clusters. Rolling them moves pods around. A controller watching inventory during
that churn will see transient states and may act on them. Pausing is cheaper than making
every reading robust against an upgrade.

Step nine must run on the failure path too, or a failed upgrade silently leaves inventory
automation off.

---

## 5 :: Open questions

1. How the controller is paused. An annotation it honours is the simplest answer and keeps
   the state where the controller already looks.
2. Whether tenants are told. Their control planes are pods on these workers, so a bad hub
   upgrade is visible to them even though nothing of theirs changed.
3. How long a hub may lag before it must be upgraded or retired. Nothing defines the
   lifetime of a version band today.
