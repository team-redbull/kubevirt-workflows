# 13 :: Upgrade Hosted Cluster Workers

**Animation:** [`../animations/13-upgrade-hosted-cluster-workers.html`](../animations/13-upgrade-hosted-cluster-workers.html)

| | |
|---|---|
| **Trigger** | Customer, choosing their own window |
| **Workflow id** | `upgrade-workers-<cluster>-<node-type>` |
| **Separate from** | [14 Control Plane](14-upgrade-hosted-cluster-control-plane.md), deliberately |
| **Writes** | Git only |

> Workers are upgraded separately from the control plane so that unpredictable workload
> drain times cannot delay or block API availability. Run 14 first, then this.

---

## 1 :: Process flow

| # | Phase | Action | On failure |
|---|---|---|---|
| 1 | `validating` | Target release is not newer than the hub supports; control plane already at or above the target | Terminal |
| 2 | `checking-capacity` | The pool can supply the surge nodes without falling below its floor | Terminal, with the shortfall named |
| 3 | `preflight` | Cluster healthy, no operation already in flight | Terminal |
| 4 | `patching` | Set the release image on each zone's NodePool, pinned **by digest**. One commit. | Retry |
| 5 | — | Merge to main | Out of band |
| 6 | `rolling` | Watch the rollout zone by zone | See below |
| 7 | `verifying` | Every node reports the new version and is ready | Bounded wait |

---

## 2 :: The surge strategy, which is claimed but not configured

The architecture states that rolling replacement runs with surge above zero and unavailable
at zero, so new workers are fully provisioned before old ones drain and tenant capacity
never drops. **No committed manifest sets either value.** Today the rollout uses whatever
the platform defaults to.

This workflow should set them explicitly as part of the patch, and fail if they are absent
rather than inheriting a default that may change.

Surge has a cost the capacity check in step two exists to cover: each surging node is drawn
from the warm pool. Upgrading three zones at once can empty a pool that comfortably serves
steady state.

---

## 3 :: Zone ordering

Roll one zone at a time and wait for it to settle before starting the next. Rolling all
three together means a failure mid-upgrade affects every zone at once, which is exactly the
blast radius the three-zone layout exists to avoid.

---

## 4 :: Failure handling

Because the strategy replaces rather than upgrades in place, a failure leaves the old nodes
intact and the new ones faulty. That is recoverable: stop the rollout, leave the old nodes
serving, and investigate. The run should fail loudly rather than retrying a rollout that is
producing broken nodes.

---

## 5 :: Open questions

1. Whether the customer picks the window and the platform picks the ordering, or both.
2. Whether a failed zone should automatically halt the remaining zones. It should, but that
   needs the rollout to be observable per zone, which the current manifests do not make easy.
3. What happens to surge nodes if the upgrade is abandoned halfway.
