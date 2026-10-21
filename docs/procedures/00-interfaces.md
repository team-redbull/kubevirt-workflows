# 00 :: Interfaces and Data Exchange

Every call the workflows make, with the real shapes. Taken from the implementation, not
from intent. Where something is designed but unbuilt it says so.

## Nothing below is called by a workflow

A Temporal workflow is replayed from its recorded event history after any crash, so it has
to be deterministic. The sandbox blocks network, clock and randomness. **It holds no
credentials and cannot make a call.**

Every item in this document is therefore an **activity**: a plain function, named by the
workflow, routed by Temporal to a task queue, run by whichever worker polls that queue. The
worker returns a typed result, Temporal records it, and on replay the activity is not
re-executed — the recorded result is read back. That is what makes recovery safe, and it is
why the two halves cannot be merged.

```
workflow  (brain)     decides order, timeouts, retries, what happens next
                      one central deployment, no secrets, no I/O
     |
     |  ExecuteActivity -> task queue
     v
activity  (limb)      one call to one system, with one set of credentials
                      a worker inside the cluster it talks to
```

Three worker identities appear in a single add-server run, and the split is the security
boundary, not a packaging choice:

| Worker | Queue | Holds | Status |
|---|---|---|---|
| `segment-lifecycle-worker` | `segment-lifecycle-activity` | the Segments Manager token | running |
| `server-lifecycle-worker` | `server-lifecycle-activity-<MCE_CLUSTER>` | that hub's service account, per-vendor BMC credentials, the inventory admin token, the PXE token | running, **one deployment per hub, inside it** |
| a worker on each KubeVirt cluster | same pattern | that cluster's service account only | **planned, does not exist** |

The hub worker is the interesting one. The workflow chooses which cluster to write to purely
by which queue it dispatches to, and derives that queue name deterministically from its own
input so it replays identically. The worker **dials out** to Temporal, so the hub never needs
inbound access to a managed cluster, and no cross-cluster kubeconfig exists anywhere. If no
worker polls a queue the run waits rather than acting, and the progress query names the
queue, so it reads as "that hub has no worker" instead of "the run is stuck".

**A worker is per hub, not per pool.** Two axes are easy to conflate:

| Per hub | Per pool (node type and zone) |
|---|---|
| the activity worker | the workflow run |
| the Inventory Controller | the workflow id, the target, the candidate pattern |

Four node types across three zones is twelve pools, so up to twelve concurrent workflows,
all dispatching to one queue served by one worker. A worker per InfraEnv would be twelve
identical deployments with identical credentials talking to the same API server.

Plan for the consequence: that worker's activity concurrency is a **shared throttle across
every pool on the hub**. The real ceiling is the worker's concurrency setting, not the sum
of each pool's `max_parallel`.

The segments worker is separate for a specific reason: the VLAN lookup reads the Segments
Manager, whose credential lives there, and routing one activity to another queue was
preferred over giving the hub worker a second copy of the token. The rule is that a separate
deployment is driven by the dependency and credential set, not by the workflow boundary.

The KubeVirt worker is an extrapolation of the same pattern to the virtual machine path
being built, not something that exists. The credential boundary is identical, so the
alternative would be a cross-cluster kubeconfig, which the platform has already rejected.

Only data crosses back. A worker returns `{"assigned": 14, "spare": 2}`, never a client, a
token or a connection.

---

Three things to know before reading:

- **Credentials are never cross-cluster.** A worker runs inside each cluster, polls only its
  own queue and talks to its own API server as its own service account. "Runs on" in each
  procedure names which worker, and therefore which credentials.
- **Errors are RFC 9457 problem documents** on both HTTP services. Read `code` and `detail`;
  `details` carries the structured extension.
- **Virtual machines will be registered in the inventory**, collected by scanning the
  KubeVirt clusters. That is decided; it is not built. See the last section for the three
  things it still needs.

---

## 1 :: server-scan — the fleet inventory

Base URL in `SERVER_SCAN_URL`, including `/api/v1`. Bearer token.
A **viewer** token suffices to read; the reservation calls need **admin**.

### 1.1 Draw candidates

```
GET /servers/available?pattern=^ocp-<infra_env>&count=3&health=HEALTHY
GET /servers/available?name=<exact-name>&health=HEALTHY
```

`name` and `pattern` are mutually exclusive. `count` only means anything in pattern mode,
defaults to 1, caps at 20. A named lookup is a pool of one.

The endpoint already filters to unclaimed, reachable, not in maintenance, not reserved by
anyone else, and above the health floor. It then **live-rechecks each candidate against the
vendor manager and writes the refreshed document back**, so it is not a pure read and it is
not fast. The client budget is sixty seconds against a ninety second activity timeout.

Response is `{items, mode, requested, returned}`. Each item:

| Field | Use |
|---|---|
| `id` | The key for the reservation calls |
| `name` | Becomes the BareMetalHost name and the node hostname |
| `bmc_vendor` | `DELL`, `HP`, `CISCO`, `INTERSIGHT`. Picks the Ironic driver. |
| `bmc{host, scheme, port, ...}` | Built into the BareMetalHost's BMC address |
| `interfaces[{name, mac, location, os_name, link_state, speed_mbps}]` | **Bond members come from here** |
| `nic_macs[]` | **Do not bond from this.** See the trap below. |
| `site_id`, `health_overall`, `live_recheck_performed` | Context and freshness |

> **The one trap.** For Dell, partitioned cards are reduced to one entry per physical port in
> `interfaces` but the MAC list is left whole. A four-port card can report four interfaces
> and sixteen MACs, so indexing MACs bonds two partitions of the same wire. Always select
> from `interfaces`.

Errors worth handling by name: `AVAILABLE_LOOKUP_CONFLICTING_PARAMS`,
`AVAILABLE_COUNT_TOO_LARGE`, `404 AVAILABLE_SERVER_NOT_FOUND` whose `details.reasons`
carries codes such as `INSTALLED`, `CLAIMED`, `MAINTENANCE`, `UNREACHABLE`, `RESERVED`,
`HEALTH`, `NETWORK`, and `503 SERVICE_UNAVAILABLE` when the vendor manager failed during the
recheck. The last one does **not** mean no server is available; retry.

### 1.2 Take or extend the install lock

```
POST /servers/{id}/reservation
{
  "holder":       "add-server",
  "mce_cluster":  "ocp4-prod-mce-bat-yam",
  "infra_env":    "cisco-m6-64c-512gb",
  "namespace":    "openshift-machine-api",
  "workflow_id":  "add-server-cisco-m6-64c-512gb",
  "ttl_seconds":  7200
}
```

`ttl_seconds` is bounded between 300 and 86400, default 7200. The lease is keyed on
`workflow_id`, so the same run re-posting **extends** rather than racing. That is what makes
activity retries safe.

| Status | Meaning |
|---|---|
| 200 | Held. `expires_at` in the response. |
| 404 | The machine has left the inventory |
| 409 **with** `details.held_by` | Another run owns it. Permanent for this candidate; **skip** it. |
| 409 **without** `held_by` | Lost a revision race against some other write, often a collector. **Retryable.** |

Those two 409s are different answers and conflating them either loops forever or abandons a
machine that was never taken.

```
DELETE /servers/{id}/reservation     body: {"holder": ..., "workflow_id": ...}
```

With a holder, another holder's lock is refused rather than stolen.

### 1.3 What you cannot do

**There is no endpoint that marks a server available again.** That field is written only by
a status job running on each hub, every fifteen minutes, which notices the Agent has gone.
Removal is: tear down, release the lock, let the job observe. Do not try to push it.

---

## 2 :: Segments Manager — the hub's inventory VLAN

Base URL in `SEGMENTS_MANAGER_URL`. Reads are public; writes need a bearer token.

```
GET /api/segments?type=INVENTORY
```

The endpoint accepts `site`, `status`, `type` and `fresh`. **It has no cluster filter**, so
the hub match is made client-side and is not optional:

```python
matches = [e for e in entries
           if e["type"] == "INVENTORY" and e["cluster_name"] == hub]
```

Re-check the type as well as the cluster. An ignored or renamed filter would otherwise hand
back another hub's VLAN with nothing to detect it.

One segment per hub, found by hub name, which is why the hub name is load-bearing. The only
field the provisioning path needs from it is `vlan_id`.

> A list endpoint answers "nothing matched" with `200` and an empty list, so a `404` here
> means the route is missing, not that there is no segment. Treat them differently.

---

## 3 :: PXE map — network boot for machines without virtual media

Base URL per site in `PXE_MAP_URLS[site]`, bearer token.

```
PUT /pxe-map/<boot-mac>
{"infra_env": {"mce_cluster": "...", "name": "..."}}

PUT /pxe-map/<mce_cluster>/<infra_env>
{"ipxe_url": "...", "image_created_at": "..."}
```

The first maps one machine's **boot MAC only** to an InfraEnv's boot entry. The second
publishes that entry, one per InfraEnv rather than one per host.

The publish is a compare-and-set on `image_created_at`: a `409` means the host already holds
a **newer** image, which is success, not failure. That is what stops a slow run rolling an
entry backwards.

Only needed for machines driven over IPMI, which have no virtual media and must boot from
the network.

---

## 4 :: The hub's Kubernetes API

Direct calls through the in-cluster service account. No Git, no ArgoCD in this path.

### 4.1 Written

| Resource | Group / version | Key fields |
|---|---|---|
| **Secret** | core/v1 | BMC credentials. Never sourced from the inventory, which does not hold them. |
| **NMStateConfig** | `agent-install.openshift.io/v1beta1` | Bond with addressing **off**, one VLAN sub-interface with DHCP on. Labelled with the InfraEnv. |
| **BareMetalHost** | `metal3.io/v1alpha1` | Below. |

The BareMetalHost body, with the parts that matter:

```yaml
metadata:
  labels:
    infraenvs.agent-install.openshift.io: <infra_env>   # binds host to InfraEnv
  annotations:
    inspect.metal3.io: disabled
    bmac.agent-install.openshift.io/hostname: <dns-cased name>
spec:
  online: true
  bootMACAddress: <first bond member's MAC>
  customDeploy: {method: start_assisted_install}
  automatedCleaningMode: disabled
  bootMode: UEFI
  bmc:
    address: <driver>://<host>...        # from bmc_vendor + bmc{}
    credentialsName: <the Secret above>
    disableCertificateVerification: true
```

Inspection and cleaning are disabled deliberately: the Assisted Installer does its own
inventory through `customDeploy`, so Ironic doing it again only costs a reboot cycle.

**Writes are create-if-absent with identity verification.** An existing object is success
once its BMC address, boot MAC, InfraEnv and VLAN match, and a conflict for a human when
they differ. Nothing is ever overwritten, so an operator's hand correction survives a re-run.
This is not server-side apply, and the difference is the point.

### 4.2 Read — and this is the part you asked about

**The InfraEnv is where the discovery image comes from.** It is an operator-declared object,
one per pool. Nothing in these workflows creates one; a missing InfraEnv is a failed
precondition.

When an NMStateConfig carrying the InfraEnv's label appears or changes, assisted-service
**regenerates the discovery image** and stamps `status.createdTime` with the generation
time. Critically, the image carries the static network config of only those NMStateConfigs
it was generated from. So a host booting an image older than its own config comes up with
the wrong networking.

The freshness check is therefore two conditions, not one:

| Read | Condition |
|---|---|
| `status.conditions[ImageCreated]` | Must be `True`. It goes `False` when a generation **failed**, usually on an NMStateConfig it could not validate, and the message is the only place the cause appears. |
| `status.createdTime` vs the NMStateConfig's `metadata.creationTimestamp` | The image must be no older than the config |

Checking only the timestamp passes an image created before a failure. Checking only the
condition passes a successful image that predates this host.

**The Agent is how a machine reports back.** It is created by assisted-service when the
machine boots the discovery image and phones home. Its existence is the only proof that the
BMC accepted the boot, the bond formed, the VLAN was right and DHCP answered, which is why
it is worth waiting an hour for on bare metal.

Matching an Agent to the host that produced it, in order:

```python
agent.metadata.labels["bmac.agent-install.openshift.io/bmh"] == bmh_name
  or  any MAC the Agent reports is one of this host's bond MACs
```

The label is set by the bare-metal agent controller. The MAC fallback exists because an
Agent is named after the machine's inventory UUID and its spec holds no reference back to
the BareMetalHost.

Bound or free is read from one field:

```
spec.clusterDeploymentName   set -> a cluster or NodePool claimed it
                             empty -> it is spare inventory
```

That field is the entire supply side of the controller's arithmetic.

---

## 5 :: The KubeVirt cluster's API

One call, on a different cluster, through that cluster's own worker.

| Operation | Resource | Status |
|---|---|---|
| Create a virtual machine, wait for it to run and report a MAC | `kubevirt.io/v1` VirtualMachine | **Being implemented** |
| Delete it | same | **Being implemented** |

The machine is then exposed as a virtual BMC over Redfish, so the hub treats it exactly like
a physical host and everything in section 4 applies unchanged. The BMC address builder
already handles that shape: a DNS host with a port and a path, behind a route.

### 5.1 Virtual machines in the inventory — decided, not built

A new collector scans the KubeVirt clusters and registers their virtual machines as servers.
Both pool types then ask the same question of the same service, and the branch narrows to
one thing: **when nothing is available, a virtual pool can manufacture more and a bare-metal
pool cannot.**

Three things it needs before the draw can be trusted.

**Collection latency, which is the awkward one.** The vendor collectors run on a six-hour
cycle, and the live recheck on the draw endpoint only refreshes machines already stored. A
machine created ten minutes ago is invisible until the next sweep. There is precedent here:
the Dell provisioning workflow used to wait for the inventory to list a machine and the team
removed it, declaring done to be configured rather than inventoried. The same wait would be
worse in this path, because it is in the critical path of filling a pool.

Two workable answers: run the KubeVirt collector on a cadence of minutes, or have the
workflow proceed directly with the machine it just created and let the collector catch up
afterwards. The second is simpler and matches the existing precedent.

**Correlation.** Machines are matched on vendor plus normalised serial, with a BMC host
fallback. A virtual machine has no serial in that sense, so the collector must supply
something stable — most naturally the machine's own identifier, with its Redfish route as
the BMC host.

**Naming.** The site is parsed from the hostname, and the candidate draw is a prefix pattern
built from the InfraEnv name. Virtual machines must therefore follow the same naming
convention as physical ones, or they will neither land in a site nor match a draw.

Two closed enumerations also gain a member, vendor and manager type, each of which has a
decision record behind it in that repository.
