# Inventory Controller

**What:** Keeps the pool of ready worker nodes matched to what tenant clusters are asking for.
**Where:** One instance on each Customer MCE hub.

---

## Terms

| Term | Meaning |
|---|---|
| **NodePool** | HyperShift resource saying how many workers a tenant cluster wants in one zone |
| **Agent** | A VM that has booted, registered with MCE, and is ready to become a worker. Either spare or assigned. |
| **InfraEnv** | What agents register into. One per node type per zone, shared by all tenants. |
| **Pool** | One InfraEnv's worth of inventory, keyed by hub, node type and zone. The unit this controller works in. |

---

## What it does

NodePools say how many nodes tenants want. Agents are the nodes that exist. Nothing keeps the
two matched today, so inventory is built by hand.

The controller watches demand on its own hub, compares it to a target, and asks Temporal to
build or remove VMs to close the gap.

It does not know or care *why* demand changed. A new tenant cluster, a scale-up, a node
replaced during an upgrade, and a cluster deletion all look identical to it: the pool is the
wrong size. One code path handles all of them.

---

## The loop

Every pass, default two minutes, for each pool on the hub:

1. **Read demand.** Sum the desired replicas of every NodePool using this node type and zone.
2. **Read supply.** Count agents, both spare and assigned.
3. **Compute the target.** Demand plus a buffer.
4. **Signal Temporal** with the target, then move on.

The controller never blocks and never remembers what it asked for. Every pass starts from
scratch.

---

## The formula

Read from the cluster, per pool:

| Name | Meaning |
|---|---|
| `demand` | Sum of desired replicas across the NodePools in this pool |
| `assigned` | Agents assigned to a NodePool |
| `spare` | Agents registered and unassigned. A VM mid-scrub must not appear here. |

Then:

```
total   = assigned + spare          <- what the controller can see

low     = max(minFree, round(lowPct  × demand))
high    = max(maxFree, round(highPct × demand))

target  = demand + low
ceiling = demand + high

grow    if  total < target                      ->  act immediately
hold    if  target <= total <= ceiling
shrink  if  total > ceiling, held for cooldown  ->  act
```

Everything is in the same unit: whole agents in the pool. The signal always carries `target`.
The gap only decides *whether* to speak, never what to say.

**The controller cannot see work in flight, and that is fine.** A machine being built is not
yet an Agent, so for the whole twenty minutes of a build the controller keeps computing the
same shortfall and keeps sending the same target. Every one of those is absorbed, because
the workflow subtracts its own count of running children before acting. In-flight is
workflow state and lives nowhere the controller could read it.

**Notice the target never touches the agent count.** It is a function of demand and
configuration alone. That is what makes a stale reading of the pool harmless: it can cause
an unnecessary signal or a missed one, but it cannot corrupt the number. A delta would carry
the error straight into machines built.

**Attributing demand to a pool is the part that needs care.** A NodePool claims agents
through its label selector, so the only honest test of whether its replicas are demand on
this pool is to match that selector against the labels this InfraEnv stamps. A selector that
resolves to more than one InfraEnv is a configuration error to report, not something to
apportion: guessing a split produces a plausible wrong number, which is worse than refusing.
Today's committed manifests select on zone alone, so this does not work yet.

### Worked example

One zone, node type 32/128, three tenant clusters wanting twenty nodes between them.
Watermarks of ten and twenty percent.

```
demand = 20
low    = 2      ->  target  = 22
high   = 4      ->  ceiling = 24

total = 21   below target          ->  order 1
total = 23   inside the band       ->  do nothing
total = 26   above ceiling, held   ->  remove 4, back down to 22
```

### Three things that are easy to get wrong

- **Size the pool off demand, not off how many agents are currently assigned.** The two agree
  only while every NodePool is full. The moment a NodePool is short, the assigned count hides
  the shortfall and you under-order. Demand always shows it.
- **A VM mid-scrub must not show up as spare.** On scale-down the VM returns to the pool rather
  than being destroyed, which is the point of the band. The controller does not track it while
  its disk is being reset. It simply reappears as a spare agent on a later pass and is counted
  then. That only works if a released VM stops being a registered agent until the reset
  completes. If it lingers as an unassigned agent mid-scrub, the controller will hand tenant
  data to the next cluster that claims it.
- **`minFree` is not optional.** A brand-new node type has `demand = 0`, and any percentage of
  zero is zero, so the pool is never seeded and the first tenant to want that type waits out a
  full VM build. Small pools have the same problem: ten percent of three rounds to nothing.
  Size the floor off the largest single scale request you want to absorb without a wait. The
  percentage protects large pools, the floor protects small ones.

---

## Talking to Temporal

One long-running workflow per pool.

| | |
|---|---|
| Workflow ID | `inventory-{hub}-{nodetype}-{zone}` |
| Started with | `SignalWithStart`, every pass |
| Signal | `setTarget { target }` |

Four rules:

- **The ID is fixed per pool.** Never put the target in the ID. If the ID changes when the
  target changes, Temporal has nothing to match against and every pass creates a new workflow.
- **The signal carries a total, not a delta.** Repeating the same target is a no-op, so a
  two-minute pass costs nothing when nothing has changed.
- **The workflow owns in-flight work.** It knows how many VMs it is already building and
  provisions only `target − total`. This is what stops a two-minute
  pass from double-ordering during a twenty-minute build.
- **One workflow does both directions.** The same workflow grows and shrinks its pool, so a
  build and a teardown can never race.

A Temporal task queue routes work to workers. It does not deduplicate. Deduplication comes
from the workflow ID.

---

## Config

A ConfigMap on the hub, read at startup and re-read on change.

```yaml
defaults:
  lowPct:   15      # buffer, as a percentage of demand
  highPct:  30      # above this the pool is too big
  minFree:   2      # buffer floor, in whole agents
  maxFree:  10      # buffer ceiling, in whole agents
  cooldown: 30m     # how long a surplus must persist before shrinking
pools:
  "32-128/zone-1":
    minFree: 5
```

The values above are placeholders. See the last section.

---

## Why it is built this way

| Decision | Reason |
|---|---|
| One controller per MCE hub | Blast radius is one hub. In-cluster credentials only. No central service to scale. |
| Works per pool, not per tenant | The InfraEnv is already shared per node type and zone. Bulk requests fall out for free. |
| Ignores what caused the demand | Modelled on Kubernetes. One code path instead of one per cause. |
| Reads state, never events | Restart recovery is a single pass. Nothing to replay, nothing to miss. |
| Sends a target, not a delta | A delta means remembering what you sent. A target means the controller holds no state at all. |
| Stable workflow ID per pool | This is the entire deduplication mechanism. |
| Target is total pool size, measured off demand | Sizing off assigned agents hides the shortfall whenever a NodePool is not full. |
| Percentage with an absolute floor | The percentage scales with the pool. The floor keeps small and empty pools usable. |
| Grow now, shrink slowly | Shrinking late wastes a little capacity. Growing late stalls a tenant. |
| One workflow per pool, both directions | Single writer. No grow-versus-shrink race. |
| Never in the tenant's request path | A controller outage slows replenishment. It does not fail tenant requests. |

---

## Still to decide

- **Threshold values.** Cannot be set until we measure how long one VM takes to build, because
  `minFree` has to cover the demand that arrives during a single build.
- **Release behaviour.** Does a released VM stop being a registered agent while its disk is
  reset? The arithmetic above is only safe if it does.
- **Bare-metal pools.** In scope, or VM pools only?
- **Who grows the fleet.** When a hub or KV cluster fills up, something has to provision a new
  one. Not this controller. It only sees its own hub.
