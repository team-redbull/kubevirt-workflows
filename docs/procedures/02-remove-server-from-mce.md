# 02 :: Remove Server from MCE

**Animation:** [`../animations/02-remove-server-from-mce.html`](../animations/02-remove-server-from-mce.html)

**Interfaces:** [`00-interfaces.md`](00-interfaces.md) — exact endpoints, payloads and resource bodies

| | |
|---|---|
| **Trigger** | Inventory Controller, when a pool has been **above its ceiling** for the cooldown |
| **Workflow id** | `remove-server-<infraenv>` — same pool key as [01](01-add-server-to-mce.md) |
| **Runs on** | The hub's worker queue; the KV cluster's queue for the VM delete only |
| **Scope** | Hub-agnostic and pool-agnostic, same as 01 |
| **Removes** | One Agent per iteration, until the pool is back at target |

> **This is not the scale-down path.** A tenant reducing a NodePool simply unbinds its agents,
> which return to the pool. Nothing is destroyed. This workflow only runs when the pool is
> *over* its ceiling and has stayed there through the cooldown. Wiring it to every scale-down
> would destroy and rebuild constantly and defeat the watermark band entirely.

---

## 1 :: Purpose

Shrink a pool back to target by destroying surplus capacity. Destroying is the exception,
triggered by a sustained surplus, not the normal response to a node being released.

---

## 2 :: Inputs

| Field | Type | Required | Notes |
|---|---|---|---|
| `hub` | str | ✅ | Canonical hub name |
| `infra_env` | str | ✅ | The pool |
| `namespace` | str | ✅ | Where the InfraEnv lives |
| `pool_type` | enum | ✅ | `VM` or `BM` |
| `target_total` | int | ✅ | Absolute desired size, same signal shape as 01 |
| `kv_cluster` | str | VM only | Where the VM lives |

---

## 3 :: Preconditions

- The pool total exceeds the ceiling and has done for the full cooldown. The controller owns
  that judgement; this workflow trusts the target it is given.
- A worker is running on the hub's queue, and on the KV cluster's queue for a VM pool.

---

## 4 :: Process flow

| # | Phase | Runs on | Action | On failure |
|---|---|---|---|---|
| 1 | `selecting-victim` | hub queue | List Agents in the pool and pick one that is **unbound**. Prefer the longest idle. | None selectable → complete as a no-op |
| 2 | `confirming-unbound` | hub queue | **Re-read immediately before destroying.** A NodePool can claim an agent at any instant. | Became bound → skip it, pick another |
| 3 | `detaching` | hub queue | Annotate the BareMetalHost detached | Retry |
| 4 | `deleting-nmstate` | hub queue | Delete the NMStateConfig | 404 is success |
| 5 | `deleting-bmh` | hub queue | Delete the BareMetalHost | 404 is success |
| 6 | `clearing-finalizer` | hub queue | Bounded wait, then clear **only** the `baremetalhost.metal3.io` finalizer | Retry |
| 7 | `deleting-secret` | hub queue | Delete the BMC Secret | 404 is success |
| 8 | `verifying` | hub queue | Confirm all three are gone | Anything survives → retryable failure |
| 9 | `unmapping-pxe` | hub queue | **IPMI only.** Remove the boot MAC mapping | 404 is success |
| 10a | `deleting-vm` | **KV queue** | **VM only.** Delete the VM, releasing hypervisor CPU, memory and storage | Retry |
| 10b | `releasing-server` | hub queue | **BM only.** Release the server-scan reservation if this run holds one | See note below |
| 11 | — | — | Re-read total. Still above target? Loop. Else complete. | |

### Step order is load-bearing

Detach **before** delete. Metal3's finalizer will otherwise block forever trying to
deprovision through a BMC that is no longer answering. This ordering was learned
empirically; do not reorder it for tidiness.

### A note on returning a bare-metal machine

There is **no endpoint that marks a server available**. That field is written only by the
per-hub status job, which runs every fifteen minutes and notices the Agent has gone. So
removal is: tear down, release any lock, and let the job observe. Do not try to push the
state.

---

## 5 :: What this must never do

| | |
|---|---|
| Destroy a **bound** agent | It is carrying a tenant workload |
| Fire on a tenant scale-down | That is a return to the pool, not a surplus |
| Destroy below target | The target is a floor as well as a ceiling |
| Count a VM mid-scrub as spare | It ran a tenant OS and its disk is not yet reset |

---

## 6 :: Idempotency

Every delete treats a missing object as success. The verification step, not the delete
calls, decides whether the run is done. Re-running after a partial teardown converges.

---

## 7 :: Open questions

1. Does releasing a tenant's VM reset its disk, and does it stop being a registered Agent
   while that happens? If a scrubbing VM lingers as an unbound Agent, the next cluster to
   claim it gets the previous tenant's data. **This is the one open item that is a
   correctness issue rather than a design choice.**
2. Victim selection when several are idle. Longest idle is the obvious default; placement
   spread across hypervisors may be a better one for VM pools.
