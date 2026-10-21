# 18 :: Migrate a Hosted Cluster to Another Version Band

**Animation:** [`../animations/18-migrate-hosted-cluster-version-band.html`](../animations/18-migrate-hosted-cluster-version-band.html)

| | |
|---|---|
| **Trigger** | A tenant wants a version their hub cannot reach, or their hub is being retired |
| **Workflow id** | `migrate-cluster-<cluster>` |
| **Composed of** | [03 Create](03-create-hosted-cluster.md) on the new hub, then [05 Delete](05-delete-hosted-cluster.md) on the old one |
| **Reality** | This is a **rebuild**, not a move |

---

## 1 :: Why this exists

Hubs come one per OCP version band, and each supports a window of versions below its own. A
tenant eventually wants something outside their hub's window, or their hub ages out of
service. Neither upgrading the cluster nor upgrading the hub helps at that point.

The platform has always assumed several hubs at different versions coexist, but nothing has
ever described how a tenant crosses between them. This is the missing piece.

---

## 2 :: Why it cannot be a move

A hosted cluster's control plane runs on exactly one hub, and its NodePools cannot span
hubs. There is no operation that relocates a control plane. So the cluster is rebuilt on the
new hub and the old one is deleted.

Which means this is tenant-visible, and everything below follows from accepting that rather
than pretending otherwise.

---

## 3 :: Process flow

| # | Phase | Action | On failure |
|---|---|---|---|
| 1 | `validating` | Target hub exists, supports the wanted version, and has pools for every worker type the tenant uses in all three zones | Terminal |
| 2 | `checking-capacity` | The target hub's pools can supply the full worker count **in addition to** the old cluster, which stays up | Terminal, with the shortfall named |
| 3 | `allocating` | A new segment at the target hub's site | Pool exhausted → terminal |
| 4 | `creating` | [03](03-create-hosted-cluster.md) against the new hub, same worker shape, new name | Terminal, nothing of the old touched |
| 5 | `awaiting-ready` | New cluster has a healthy control plane and its full worker count | Bounded |
| 6 | — | **Tenant moves their workloads.** Not a platform step. | Out of band |
| 7 | `confirming` | The tenant confirms the new cluster is serving | Waits indefinitely, by design |
| 8 | `deleting-old` | [05](05-delete-hosted-cluster.md) against the old hub | Retry |

### Both clusters exist at once

Between steps four and eight the tenant has two clusters and the platform is carrying double
their worker capacity. That is the cost of there being no move operation, and step two is
what stops it being discovered halfway through.

### Step seven waits for a human on purpose

Nothing automated should decide a tenant has finished migrating. The run holds until they
say so. A workflow that times out and deletes the old cluster is a data-loss incident with
a progress bar.

---

## 4 :: What the platform owes the tenant here

Their cluster name changes, their API endpoint changes, and their workloads have to be
redeployed. Because tenants are read-only and everything they run comes from Git, that
redeployment is largely repointing their own pipelines, which is the one part of this that
is genuinely easier here than it would be elsewhere.

---

## 5 :: Open questions

1. **Can the name be reused?** Keeping it would spare the tenant most of the pain, but the
   two clusters coexist, so it cannot be reused until the old one is gone. A temporary name
   with a rename at the end is not something the platform supports.
2. Whether persistent data moves, and who owns that. Storage is deliberately decoupled from
   the cluster lifecycle, so this may be re-attachment rather than copying, but it has never
   been tested across hubs.
3. Whether a band is ever retired on a schedule, which would make this routine rather than
   exceptional, and would change how much of it has to be automated.
4. Whether two hubs at one site can be fed from one pool of machines during the overlap, or
   whether the migration needs its own reserved capacity.
