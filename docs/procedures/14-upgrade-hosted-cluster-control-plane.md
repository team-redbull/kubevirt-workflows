# 14 :: Upgrade Hosted Cluster Control Plane

**Animation:** [`../animations/14-upgrade-hosted-cluster-control-plane.html`](../animations/14-upgrade-hosted-cluster-control-plane.html)

| | |
|---|---|
| **Trigger** | Customer or platform, in a chosen window |
| **Workflow id** | `upgrade-control-plane-<cluster>` |
| **Order** | Run **before** [13 Workers](13-upgrade-hosted-cluster-workers.md) |
| **Writes** | Git only |

---

## 1 :: Why it is separate from the workers

The control plane is pods on a hub. It has no tenant workloads to drain, so it upgrades in
minutes and predictably. Workers drain tenant workloads and can take hours. Bundling them
means an unpredictable worker drain delays API availability for no reason.

---

## 2 :: Process flow

| # | Phase | Action | On failure |
|---|---|---|---|
| 1 | `validating` | Target release is within what the hub supports. A hosted cluster may never be newer than its hub. | Terminal |
| 2 | `preflight` | API responding, quorum intact, no degraded operators, no operation in flight | Terminal |
| 3 | `backing-up` | Take and verify an etcd backup | Terminal if it cannot be verified |
| 4 | `patching` | Set the release image on the HostedCluster, by digest. One commit. | Retry |
| 5 | — | Merge to main | Out of band |
| 6 | `rolling` | Watch the control plane roll, one replica at a time | Bounded |
| 7 | `verifying` | Version reported, quorum intact, operators not degraded | Bounded |

---

## 3 :: The constraint that governs everything here

A hosted cluster can never run a version newer than its hub supports, and a hub supports a
window of versions below its own. So this workflow is gated by [15 Upgrade MCE Hub](15-upgrade-mce-hub.md),
and the fleet upgrades from the top down: hub first, then the clusters it hosts.

When a tenant wants a version their hub cannot reach, the answer is not this workflow. It is
[18 Migrate Version Band](18-migrate-hosted-cluster-version-band.md).

---

## 4 :: Quorum

Replicas roll one at a time so two of three stay up throughout. The workflow must verify
quorum between replicas rather than only at the end, because losing it mid-roll is the
failure that is hard to recover from and easy to cause.

---

## 5 :: Open questions

1. Where the etcd backup goes and who has verified it can actually be restored. An unverified
   backup is a step that produces confidence rather than safety.
2. Whether the platform may upgrade a control plane without the tenant choosing a window,
   given it should be invisible to workloads.
3. Whether a failed roll should attempt to go back. Reverting a control plane is harder than
   it sounds and may be worse than stopping.
