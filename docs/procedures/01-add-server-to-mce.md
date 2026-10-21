# 01 :: Add Server to MCE

**Animation:** [`../animations/01-add-server-to-mce.html`](../animations/01-add-server-to-mce.html)

**Interfaces:** [`00-interfaces.md`](00-interfaces.md) — exact endpoints, payloads and resource bodies

| | |
|---|---|
| **Trigger** | Inventory Controller, when a pool is below target |
| **Workflow id** | `add-server-<infraenv>` — stable per pool, target travels in the signal |
| **Runs on** | The hub's worker queue; the KV cluster's queue for the VM step only |
| **Scope** | Hub-agnostic (Customer MCE or Seed) and pool-agnostic (VM or BM) |
| **Adds** | One Agent per iteration, looping until the pool reaches target |

---

## 1 :: Purpose

Bring a pool up to the size the controller asked for. A pool is one InfraEnv, which is one
hub, one node type and one zone.

The workflow is the same for virtual and bare-metal pools because both end as an Agent. Only
the first step differs: a bare-metal pool **draws** an existing machine from server-scan, a
virtual pool **creates** one on a KubeVirt cluster. Everything after that is identical.

---

## 2 :: Inputs

| Field | Type | Required | Notes |
|---|---|---|---|
| `hub` | str | ✅ | Canonical hub name, `ocp4-` prefixed. Selects the worker queue. |
| `infra_env` | str | ✅ | The pool. Also the workflow id. |
| `namespace` | str | ✅ | Where the InfraEnv lives on the hub |
| `pool_type` | enum | ✅ | `VM` or `BM` — the only branch in the whole run |
| `target_total` | int | ✅ | Absolute desired pool size. Carried by the signal, last write wins. |
| `kv_cluster` | str | VM only | Which KubeVirt cluster to build on |
| `vm_profile` | str | VM only | CPU and memory, e.g. `32-128` |

---

## 3 :: Preconditions

- The InfraEnv exists on the hub, in `namespace`. A missing InfraEnv fails the run rather
  than creating one: a pool is an operator-declared thing.
- The hub has an inventory VLAN allocated in the Segments Manager, resolved by hub name.
- A worker is running on the hub's queue. For a VM pool, one is also running on the KV
  cluster's queue.
- BM pools only: the server-scan token is an **admin** token, because the run takes a lock.

---

## 4 :: Process flow

The run is a loop. Each iteration adds exactly one Agent, then re-reads live state and
decides whether to go again.

### First, what actually crosses the wire

Read this before the loop, because the natural misreading is that the target is sent to the
hub and the hub acts on it. It is not, and there is nothing at the hub that could.

Target of 12, pool currently holding 10:

| # | Hop | What is on the wire |
|---|---|---|
| 1 | controller → Temporal | `setTarget{ target_total: 12 }`. Recorded in history. **12 now lives in the workflow and never leaves it.** |
| 2 | workflow → hub queue | `ExecuteActivity count_pool{ hub, infra_env, namespace }`. Pool identifiers. **Not the target.** |
| 3 | hub worker → hub API | one `LIST` of Agents in that InfraEnv, split by whether `spec.clusterDeploymentName` is set |
| 4 | hub worker → workflow | `{ assigned: 9, spare: 1 }`. Two numbers. The worker then goes back to polling. |
| 5 | workflow, no I/O | `needed = 12 − (9 + 1 + 0) = 2` |
| 6 | workflow → hub queue | the per-machine activities, one task each, twice over: draw, reserve, write config, write secret, write host, await image, await Agent |
| 7 | — | back to hop 2. When the count reaches 12 with nothing in flight, the run exits. |

**A queue is an address, not a component.** It holds no logic and makes no decisions. The
worker that polls it knows nothing about the target, the pool size or the loop: it runs one
named function with typed arguments and returns a typed result.

Every decision is in the workflow, which holds no credentials and cannot make a call,
because it is replayed from event history after a crash and must therefore be
deterministic. Every credential is in the worker, which decides nothing.

### What the workflow does with the target

The signal carries an absolute size, say 18. **The workflow never orders 18.** It orders the
gap, and it recomputes the gap at every decision point:

```
on setTarget(18):          target = 18          (last write wins)

resolve the inventory VLAN once, before the loop

loop:
    assigned, spare = ACTIVITY on the hub's queue: list Agents in this pool
    inFlight        = children this workflow currently has running
    needed          = target - (assigned + spare + inFlight)

    if needed <= 0 and no children running:   complete and exit
    if needed <= 0:                           wait for a child, re-loop
    if attempts_without_progress >= max:      fail, named

    start min(needed, max_parallel) children, one machine each
    wait for any child to finish
    re-loop
```

`assigned` and `spare` are **read live, not taken from the signal**. The controller
contributes the target, which is policy. The workflow contributes the current total, which
is observation. Neither trusts the other's half, and that is what makes a stale controller
reading harmless rather than a pile of machines you then tear down.

`inFlight` is the workflow's own count of running children. It is not read from anywhere,
which is precisely why the controller cannot compute this number and the workflow can.

**Worked through.** The pool holds 14 assigned and 2 spare. The gap is 2, so two children
start, each reserving its own candidate. One succeeds twenty minutes later, taking the pool
to 17. The other never produces an Agent, tears itself down, releases its machine, and the
gap recomputes to 1, so a fresh child starts on a different candidate. When the total
reaches 18 with nothing running, the run completes and exits; the next signal starts a new
one.

Three properties fall out of counting in-flight work:

| Situation | Behaviour |
|---|---|
| Controller re-signals 18 while building | No-op. The gap is already covered by running children. |
| Demand rises, controller signals 22 | Picked up at the next checkpoint. More children start. No restart. |
| Demand falls, controller signals 12 | Stop starting children, let running ones finish. The surplus is removed later by [02](02-remove-server-from-mce.md) after the cooldown, which is cheaper than a half-torn-down machine. |

### Concurrency

| Pool | `max_parallel` | Why |
|---|---|---|
| **BM** | 1 | Candidates come from a pool shared across hubs, so parallel draws contend. |
| **VM** | higher | Bounded by KubeVirt cluster headroom, not by the pool. |

| Concurrency | |
|---|---|
| **BM pool** | Serial, one machine at a time. Candidates come from a pool shared across hubs, so parallel draws contend. |
| **VM pool** | Parallel up to `max_parallel`. We create the machines, so nothing contends. |

### Steps

| # | Phase | Runs on | Action | Success | On failure |
|---|---|---|---|---|---|
| 1 | `resolving-vlan` | segments queue | Read the hub's `INVENTORY` segment by hub name | A VLAN id | Fail the run. No candidate can fix a missing VLAN. |
| 2a | `acquiring-server` | hub queue | **BM only.** Draw candidates from server-scan, pattern built from the hardware class, `HEALTHY` only | ≥1 candidate | Pool exhausted → fail with reasons grouped |
| 2b | `creating-vm` | **KV queue** | **VM only.** Create the VM, wait for it to run and report a MAC | VM running, MAC known | Retry; after `max_attempts` on one KV cluster, raise — the host is suspect |
| 3 | `selecting-server` | hub queue | Pick bond members: two link-up NICs on distinct physical ports. Check name, BMC driver and address, MAC syntax, and that no BareMetalHost already exists on this hub | A usable candidate | Every reason is a **skip** to the next candidate, never a failed run |
| 4 | `reserving-server` | hub queue | **BM only.** Lock the machine in server-scan. Holder is this workflow id, so a retry extends rather than races | Lock held | A lock another run holds is a skip |
| 5 | `registering-pxe` | hub queue | **IPMI only.** Map the boot MAC to the InfraEnv boot entry on the site's PXE host | Mapped | No PXE entry for the site is a skip |
| 6 | `creating-nmstate` | hub queue | Write the NMStateConfig: bond with addressing off, VLAN sub-interface with DHCP on | Created or matching | A differing resource is a conflict for a human |
| 7 | `creating-secret` | hub queue | Write the BMC credential Secret | Created or matching | As above |
| 8 | `creating-bmh` | hub queue | Write the BareMetalHost pointing at the BMC address. For a VM this is its Redfish endpoint | Created or matching | As above |
| 9 | `awaiting-image` | hub queue | Wait for the InfraEnv image to be regenerated **including this host's NMStateConfig** | Image newer than the config | Timeout → tear down, next candidate |
| 10 | `awaiting-agent` | hub queue | Wait for an Agent to register, matched by bond MAC | Agent exists | Timeout → tear down, next candidate |
| 11 | `holding-server` | hub queue | **BM only.** Extend the lock to 24h | Lock extended | Non-fatal; log |
| 12 | — | — | Back to loop control | | |

Steps 3 to 10 are a **loop, not a pipeline**. A machine cannot be shown to be installable
without creating its resources and watching what happens, so the candidate list is a list of
things to try.

---

## 5 :: Failure handling

### Per-candidate rollback

Reached from any of steps 9, 10 or a lapsed lock. Order is load-bearing:

```
1. annotate the BareMetalHost detached      <- before anything is deleted
2. delete the NMStateConfig
3. delete the BareMetalHost
4. bounded wait, then clear ONLY the metal3 finalizer
5. delete the Secret
6. verify all three are gone                <- else raise, retryable
7. unmap the PXE entry (IPMI only)
8. VM pool: delete the VM.  BM pool: release the server-scan lock
```

Detaching before deleting is not optional. Metal3's finalizer will otherwise block forever
trying to deprovision through a BMC that is not answering.

### Progress counter

Each iteration that ends without the pool total rising increments a counter. On
`max_attempts` the run fails rather than looping. Without this, a bad hypervisor or a dead
machine is retried indefinitely and silently consumes the pool's serial slot.

### Classification

| Condition | Treatment |
|---|---|
| Candidate unusable for any reason | **Skip**, try the next |
| Lock held elsewhere | **Skip** |
| Resource exists but differs | **Terminal** — a human must look |
| InfraEnv or namespace missing, RBAC refused | **Terminal** |
| Anything else | Retry with capped backoff |

---

## 6 :: Idempotency

- The workflow id is the pool, so a duplicate trigger while running is rejected.
- The signal carries an absolute target, so a repeated identical target is a no-op and the
  controller can signal every cycle for free.
- All three creates are create-if-absent with identity verification. An existing resource is
  success once its BMC address, boot MAC, InfraEnv and VLAN match, and is **never
  overwritten**, so an operator's hand correction survives a re-run.
- Taking a lock the run already holds extends it instead of failing.

---

## 7 :: Outputs

| Field | Notes |
|---|---|
| `added` | How many Agents this run produced |
| `final_total` | Pool size at exit |
| `reached_target` | Whether the pool met the target |
| `skipped` | Candidate reasons, grouped, for diagnosis |

---

## 8 :: Open questions

1. **Decided:** virtual machines are registered in the inventory, collected by scanning the
   KubeVirt clusters. Both pool types then draw the same way and only the empty-pool
   response differs. Three things still need settling before the draw can be trusted, and
   collection latency is the sharp one. See
   [00-interfaces.md §5.1](00-interfaces.md#51-virtual-machines-in-the-inventory--decided-not-built).
2. `max_parallel` for VM pools. Bounded by KubeVirt cluster headroom, not by the pool.
3. Where the progress counter lives if the controller, rather than this workflow, should own
   giving up on a pool.
