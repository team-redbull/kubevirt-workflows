# 03 :: Create Hosted Cluster

**Animation:** [`../animations/03-create-hosted-cluster.html`](../animations/03-create-hosted-cluster.html)

| | |
|---|---|
| **Trigger** | Customer, via the self-service catalog or a ticket |
| **Workflow id** | `create-hosted-cluster-<cluster>` |
| **Runs on** | Segments queue, then Git, then the hub's queue to watch |
| **Writes** | Git only. Nothing is applied to a cluster by this workflow. |
| **Ends at** | Control plane available. **Not** at workers ready. |

> **This workflow creates demand, it does not satisfy it.** It writes a HostedCluster and its
> NodePools and stops. Workers appear because the Inventory Controller sees the new demand
> and fills the pools. If the warm pool is deep, that looks instant. If it is empty, the
> cluster sits with zero workers and nothing here will tell the customer why. See the
> admission note in section 6.

---

## 1 :: Purpose

Stand up a tenant's OpenShift cluster: a hosted control plane on a hub, and one NodePool per
zone for every worker type the tenant asked for.

The same workflow creates a **KubeVirt cluster** when the hub is a Seed and the worker type
is bare metal. See [06](06-create-kv-cluster.md); it is this plus a day-2 step, not a
separate thing.

---

## 2 :: Inputs

| Field | Type | Required | Notes |
|---|---|---|---|
| `cluster` | str | ✅ | Tenant cluster name, globally unique |
| `hub` | str | ✅ | Which hub hosts the control plane. **See open question 1.** |
| `release_image` | str | ✅ | OCP release, pinned **by digest, not by tag** |
| `worker_types` | list | ✅ | One entry per node type, each with a per-zone replica count |
| `values_branch` | str | ✅ | Branch to commit on. A human merges it. |
| `pull_secret_ref`, `ssh_key_ref` | str | ✅ | Names of existing secrets on the hub |

---

## 3 :: Preconditions

- The hub exists, is healthy, and its OCP version supports `release_image` within the skew
  the platform allows.
- An InfraEnv exists on that hub for every requested worker type in all three zones. A
  missing pool is a failed precondition, not something to create here.
- The site has a free segment for the tenant.

---

## 4 :: Process flow

| # | Phase | Runs on | Action | On failure |
|---|---|---|---|---|
| 1 | `validating` | brain | Cluster name free, hub known, every worker type has pools in all three zones | Terminal. Nothing written. |
| 2 | `allocating-segment` | segments queue | Allocate an `HC` segment for the cluster at the hub's site | Pool exhausted → terminal, a human creates segments |
| 3 | `verifying-allocation` | segments queue | Read the allocation back: status, cluster, VLAN and type must all match | Mismatch → terminal, before anything is committed |
| 4 | `composing` | brain | Build the HostedCluster manifest and one NodePool per zone per worker type | Terminal |
| 5 | `committing` | git | One commit, all manifests, on `values_branch` | Retry |
| 6 | — | human | **Merge to main.** ArgoCD only reads main. | Out of band |
| 7 | `awaiting-control-plane` | hub queue | Wait for the HostedCluster to report available | Bounded wait, then fail with what is degraded |
| 8 | `completed` | — | Report kubeconfig secret name and the NodePools created | |

### Zone distribution

A worker type's count is spread evenly across the three zones, with earlier zones taking the
remainder. A request below three is rejected: every zone gets at least one node, or the
cluster is not zone-redundant and should not claim to be.

### What this deliberately does not wait for

Workers. The run completes when the control plane answers. Cluster operators may read
degraded until workers arrive, which is expected and must not be treated as a failure by
anything downstream.

---

## 5 :: Idempotency

- The workflow id is the cluster name, so a duplicate request is rejected while one runs.
- Segment allocation is idempotent server-side per cluster, site and type.
- The Git commit compares the allocation rather than the file text, so an operator's
  hand-edit to unrelated values survives a re-run.

---

## 6 :: Admission, which does not exist yet

Nothing checks whether the pools can absorb the request. A tenant can ask for fifty workers,
have it committed, and watch the NodePools sit unfulfilled indefinitely with no feedback.
Before this is customer-facing it needs a pre-flight that either rejects the request with a
capacity reason or accepts it with an honest estimate.

---

## 7 :: Open questions

1. **Who picks the hub.** A hosted cluster cannot span hubs, and hubs are one per OCP
   version band per site, so this is a real placement decision with no owner today. It is an
   input until something owns it.
2. Whether the merge to main stays manual. It is the only human step in the run, and it is
   what makes the end-to-end time unpredictable.
3. Whether a tenant may mix worker types in one cluster, which the input allows and nothing
   else in the platform has been designed against.
