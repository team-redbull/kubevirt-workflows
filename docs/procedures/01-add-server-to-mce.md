# 01 :: Add Server to MCE

**Interfaces:** [`00-interfaces.md`](00-interfaces.md) — exact endpoints, payloads and resource bodies
**Animation:** [`../animations/01-add-server-to-mce.html`](../animations/01-add-server-to-mce.html)

| | |
|---|---|
| **Trigger** | Inventory Controller, when a pool is below target |
| **Run id** | `install-server-<infraenv>_<slot>` — one run per machine, slots walked from `_1` |
| **Runs on** | The hub's worker queue; the segments queue for the VLAN; the KV cluster's queue for a VM create |
| **Scope** | Hub-agnostic (Customer MCE or Seed) and pool-agnostic (VM or BM) |
| **Adds** | Exactly **one** Agent per run. Filling a pool is N runs, not one run doing N. |

> **This supersedes a parent-and-children design.** Parallel fill is done with numbered
> sibling runs rather than one workflow fanning out. See §4.2 for why, because the reasoning
> matters more than the mechanism.

---

## 1 :: Purpose

Put one machine into a pool, where a pool is one InfraEnv: one hub, one node type, one zone.

The run is the same for virtual and bare-metal pools because both end as an Agent. The only
divergence is what happens when the draw comes back empty: a virtual pool can manufacture a
machine, a bare-metal pool cannot.

---

## 2 :: Inputs

### Decided by the controller, not passed in

| | How |
|---|---|
| `target` | demand plus the watermark buffer. Never sent to this workflow; the controller converts it to a count. See §4.1. |

### Carried in the request

| Field | Type | Required | Notes |
|---|---|---|---|
| `infra_env` | str | ✅ | The pool. Also the run id, and the hardware class the draw matches on. |
| `mce_cluster` | str | ✅ | Canonical hub name, `ocp4-` prefixed. Selects the worker queue, and resolves the inventory VLAN. |
| `count` | int | bulk only | How many **new** runs to start. See the warning in §4.1. |
| `candidate_count` | int | ❌ | Defaults to `busy + count + 2`, capped at 20. Load-bearing: see §4.3. |
| `labels` | dict | ❌ | Extra labels for the created resources |

### Derived or configured, deliberately not inputs

| | Where it comes from | Why not an input |
|---|---|---|
| `namespace` | worker configuration, with a default | A property of the hub, not of the request. The caller should not have to know where InfraEnvs live. |
| pool type, VM or BM | a label on the InfraEnv | The worker already reads that object for the image check. Reading a label beats parsing a name. |
| VM size | the node type, which is in the InfraEnv name | The node type **is** the profile. Anything beyond cores and memory is a template looked up by node type. |
| KV cluster | **undecided** | The controller has no view of KubeVirt clusters at all. See open question 1. |

---

## 3 :: Preconditions

- The InfraEnv exists on the hub. A missing one fails the run rather than creating it: a pool
  is operator-declared.
- The hub has an inventory VLAN allocated, resolved by hub name.
- A worker polls the hub's queue. For a VM pool, one also polls the KV cluster's queue.
- The inventory token is an **admin** token, because the run takes a lock.

---

## 4 :: Process flow

### 4.0 First, what crosses the wire

The natural misreading is that the target is sent to the hub and the hub acts on it. It is
not, and there is nothing at the hub that could.

Target of 12, pool holding 10, nothing running:

| # | Hop | What is on the wire |
|---|---|---|
| 1 | controller → hub API | one `LIST` of Agents in this pool |
| 2 | controller → Temporal | one query: how many `install-server-<infraenv>_*` runs are open |
| 3 | controller, no I/O | `needed = 12 − (9 + 1 + 0) = 2` |
| 4 | controller → bulk route | `{ infra_env, mce_cluster, count: 2 }` |
| 5 | route → Temporal | start `_1` and `_2`. A slot already running raises, is counted busy, and is stepped over. |
| 6 | each run → hub queue | its own activities, one machine each |

A queue is an address, not a component. The worker polling it runs one named function with
typed arguments and returns a typed result. It knows nothing about the target or the pool.

### 4.1 How a target becomes runs

The controller holds the policy and does the conversion at the last moment:

```
target  = demand + buffer                        (policy, from its ConfigMap)
running = open runs matching install-server-<infraenv>_*
needed  = target - (assigned + spare + running)

if needed <= 0:  do nothing, stay silent
else:            POST install-server/bulk { infra_env, mce_cluster, count: needed }
```

> **`count` is additive, not a target.** The route starts that many **new** runs. Sending
> the same count again while the first batch is still installing **adds** to it. That is
> deliberate, because an operator calling it by hand wants exactly that. A controller must
> therefore subtract the running slots before calling, which is what the third line does.

The staleness window is one HTTP call rather than minutes, because the controller reads live
state immediately before converting. A slot finishing inside that window over-provisions by
at most one, which the deadband absorbs.

### 4.2 Why slots rather than a parent workflow

Run ids are `install-server-<infraenv>_<slot>`. The route walks slots upward, treats an
already-started error as "busy", and starts runs in free slots until it has started `count`.
A finished slot is free again, because Temporal reuses the id of a closed run.

| | Consequence |
|---|---|
| **In-flight is discoverable, not private** | Anyone can ask Temporal how many slots are open. A parent would have held that count in memory, where only it could see it and where a crash loses it. |
| **No parent to fail** | Each run is independently pollable, independently retryable, and failure of one says nothing about the others. |
| **Nothing owns convergence, and that is fine** | A failed slot simply leaves the gap open, and the controller's next pass sees it. The two-minute cycle is the retry. |
| **Two requests cannot start one slot twice** | Temporal allows one running workflow per id; the loser moves on. |

### 4.3 Candidate contention, which is easy to miss

Every run on a pool draws from the **same top** of the available list. Run two will be handed
the machine run one just locked, skip it, and try the next. So each run needs enough spare
candidates to step over everything its siblings have taken:

```
candidate_count = min(20, busy + count + 2)
```

Without that headroom most of a parallel batch fails on the lock. A run that finds every
candidate locked fails with a reserved error, which means either the pool had fewer free
machines than are installing, or the count needs raising.

### 4.4 Steps, for one run

| # | Phase | Runs on | Action | On failure |
|---|---|---|---|---|
| 1 | `resolving-vlan` | segments queue | Read the hub's `INVENTORY` segment by hub name | Fail. No candidate can fix a missing VLAN. |
| 2 | `acquiring-server` | hub queue | Draw candidates from the inventory, pattern from the hardware class, healthy only | See step 3 |
| 3 | `creating-vm` | **KV queue** | **VM pools only, and only if the draw came back empty.** Create the machine and wait for it to run and report a MAC. | BM pool → fail, pool exhausted. VM pool → retry, then fail. |
| 4 | `selecting-server` | hub queue | Pick bond members: two link-up NICs on distinct physical ports, from `interfaces` and never from the MAC list. Check name, BMC driver and address, MAC syntax, and that no BareMetalHost already exists here. | Every reason is a **skip** to the next candidate |
| 5 | `reserving-server` | hub queue | Lock the machine. Holder is this run id, so a retry extends rather than races. | A lock another run holds is a skip |
| 6 | `registering-pxe` | hub queue | **IPMI only.** Map the **boot MAC only** to the InfraEnv's boot entry on the site's PXE host | No PXE entry for the site is a skip |
| 7 | `creating-nmstate` | hub queue | Write the NMStateConfig: bond with addressing off, VLAN sub-interface on DHCP. **First for IPMI**, before anything can power the host on. | A differing resource is a conflict for a human |
| 8 | `creating-secret` | hub queue | Write the BMC credential Secret | As above |
| 9 | `creating-bmh` | hub queue | Write the BareMetalHost. For a VM this points at its Redfish endpoint. | As above |
| 10 | `awaiting-image` | hub queue | Wait for the InfraEnv image to regenerate **including this host's config**. Two conditions, not one. | Timeout → tear down, next candidate |
| 11 | `publishing-boot` | hub queue | **IPMI only.** Publish the InfraEnv's iPXE entry, now that the image includes this host. Compare-and-set; a newer entry already there is success. | Timeout → tear down |
| 12 | `awaiting-agent` | hub queue | Wait for an Agent, matched by the bond MAC | Timeout → tear down, next candidate |
| 13 | `holding-server` | hub queue | Extend the lock to 24h | Non-fatal, log |

Steps 4 to 12 are a **loop over candidates, not a pipeline**. A machine cannot be shown to
be installable without creating its resources and watching what happens.

---

## 5 :: Failure handling

### Per-candidate rollback

Reached from a timeout at step 10, 11 or 12, or from a lapsed lock. Order is load-bearing:

```
1. annotate the BareMetalHost detached      <- before anything is deleted
2. delete the NMStateConfig
3. delete the BareMetalHost
4. bounded wait, then clear ONLY the metal3 finalizer
5. delete the Secret
6. verify all three are gone                <- else raise, retryable
7. unmap the PXE entry (IPMI only)
8. VM pool: delete the machine.  BM pool: release the lock
```

Detaching before deleting is not optional. Metal3's finalizer will otherwise block forever
trying to deprovision through a BMC that is not answering, measured at fourteen hours with
no error reported.

### Classification

| Condition | Treatment |
|---|---|
| Candidate unusable for any reason | **Skip**, try the next |
| Lock held elsewhere | **Skip** |
| Resource exists but differs | **Terminal** — a human must look |
| InfraEnv or namespace missing, RBAC refused | **Terminal** |
| Every candidate locked | **Terminal** — pool too small, or raise `candidate_count` |
| Anything else | Retry with capped backoff |

There is no pool-level attempt counter, because there is no pool-level run. A run that
exhausts its candidates fails, and the controller's next pass decides whether to try again.

---

## 6 :: Idempotency

- One running workflow per slot id, so two requests cannot start the same slot twice.
- All three creates are create-if-absent **with identity verification**. An existing resource
  is success once its BMC address, boot MAC, InfraEnv and VLAN match, and is never
  overwritten, so an operator's hand correction survives a re-run. This is not server-side
  apply, and the difference is the point.
- Taking a lock the run already holds extends it instead of failing.
- Publishing a boot entry older than the one already there is success, not a rollback.

---

## 7 :: Outputs

| Field | Notes |
|---|---|
| per-slot report | `started`, `already_running` or `failed`, with the run id |
| per-run result | the Agent produced, or the grouped reasons every candidate was skipped |

---

## 8 :: Open questions

1. **Which KubeVirt cluster a new machine is built on.** The controller cannot answer: it
   sees NodePools, InfraEnvs and Agents on its own hub and nothing else. This is the same
   gap as deciding when a cell is full, surfacing in a second place. Once the inventory
   scans the KubeVirt clusters it will know which exist and how loaded each is, which makes
   it the least-bad home for the decision, but that is a proposal.
2. **Protecting a freshly created machine.** A new VM is invisible to the inventory until
   the collector sees it, so nothing else can draw it and nothing can lock it either. Once
   it becomes visible it reads as available until something reports it claimed. Whether that
   window needs a lock, or whether the Agent binding closes it in time, is untested.
3. **Collection latency.** Whether the KubeVirt collector runs on a cadence of minutes, or
   the run proceeds with the machine it just created and lets the collector catch up. The
   second matches the precedent already set for Dell provisioning.
4. Where the pool-type label is stamped, and by what.
