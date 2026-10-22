#!/usr/bin/env python3
"""Emit one self-contained animation per workflow.

Every output file is standalone: inline SVG, CSS and vanilla JS, no CDN, no
imports, nothing fetched at runtime. That is deliberate — these have to open by
double-click inside an air-gapped environment.

Run:  python3 _generate.py        (writes *.html beside this script)
"""
from pathlib import Path
import json

HERE = Path(__file__).parent

# --------------------------------------------------------------------------
# Node library. Coordinates are on a 1000 x 560 canvas.
# --------------------------------------------------------------------------
def N(x, y, t, s, w=170, h=54):
    return {"x": x, "y": y, "w": w, "h": h, "t": t, "s": s}

CUST = N(30,  36, "Customer",           "catalog or ticket")
CTL  = N(30,  36, "Inventory Controller", "on this hub")
TMP  = N(400, 36, "Temporal",           "", 200)
GIT  = N(415, 180, "Git",               "desired state", 170)
ARGO = N(415, 300, "ArgoCD",            "sole applicator", 170)
SCAN = N(800, 36, "server-scan",        "fleet inventory")
SEG  = N(30, 172, "Segments Manager",   "VLAN pool")
DHCP = N(30, 290, "DHCP API",           "scope")
KV   = N(800, 172, "KV Cluster",        "KubeVirt")
PXE  = N(30, 470, "PXE Host",           "per site")
HUB  = N(380, 430, "MCE Hub",           "HostedCluster + NodePools", 240)
SEED = N(800, 430, "Seed Cluster",      "hosts KV control planes")


def page(title, subtitle, nodes, group, steps):
    """`group` may be None, one dict, or a list of dicts."""
    groups = group if isinstance(group, list) else ([group] if group else [])
    modes = any(s[2] != "both" for s in steps)
    return TEMPLATE \
        .replace("__TITLE__", title) \
        .replace("__SUB__", subtitle) \
        .replace("__NODES__", json.dumps(nodes, indent=2)) \
        .replace("__GROUPS__", json.dumps(groups, indent=2)) \
        .replace("__STEPS__", json.dumps(steps, indent=2)) \
        .replace("__MODES__", "true" if modes else "false")


# --------------------------------------------------------------------------
# Per-workflow definitions
# --------------------------------------------------------------------------
WORKFLOWS = {}

WORKFLOWS["01-add-server-to-mce"] = dict(
    title="01 :: Add Server to MCE",
    subtitle="One run adds one machine. Filling a pool is N parallel runs in numbered slots. "
             "Both pool types draw the same way; only the empty-draw response differs.",
    nodes={"seg":  N(20, 16, "Segments Manager", "VLAN pool", 165, 46),
           "segw": N(20, 92, "segment worker", "holds the SM token", 165, 46),
           "tmp":  N(400, 16, "Temporal", "bulk route · slot runs", 210, 46),
           "scan": N(815, 16, "server-scan", "fleet inventory", 165, 46),
           "kvw":  N(812, 120, "KV worker", "PLANNED, same pattern", 156, 42),
           "vm":   N(812, 176, "VirtualMachine", "only if draw is empty", 156, 42),
           "pxe":  N(20, 252, "PXE Host", "per site", 165, 46),
           "hubw": N(265, 292, "server-lifecycle worker", "the hub's own SA", 215, 48),
           "ctl":  N(505, 292, "Inventory Controller", "in-cluster", 240, 48),
           "ienv": N(265, 372, "InfraEnv", "the pool", 215, 46),
           "res":  N(505, 372, "BMH + NMState", "+ BMC Secret", 240, 46),
           "agt":  N(350, 452, "Agent", "registered, unbound", 300, 46)},
    group=[{"x": 795, "y": 86, "w": 190, "h": 150, "label": "KV CLUSTER"},
           {"x": 240, "y": 250, "w": 530, "h": 290,
            "label": "MCE HUB"}],
    steps=[
      ("ctl","agt","both","Count the pool","The controller looks. Nothing tells it.",
       {"call":"LIST agent-install.openshift.io/v1beta1 Agent\n"
               "  namespace: <infraenv namespace>\n"
               "  label:     infraenvs.agent-install.openshift.io=<infra_env>",
        "recv":"assigned = those with spec.clusterDeploymentName set   -> 9\n"
               "spare    = those with it empty                         -> 1",
        "warn":"In-cluster read, its own ServiceAccount. NOT from the inventory: that\n"
               "learns a machine is claimed from a job running every 15 minutes, far too\n"
               "stale for this. The Agent resource is the only current source."}),

      ("ctl","tmp","both","Ask how many runs are open","In-flight is discoverable, not private.",
       {"call":"Temporal: list open workflows matching install-server-<infra_env>_*",
        "recv":"running = 0",
        "warn":"This is why there is no parent workflow. Naming the runs by slot turns\n"
               "\"how many are building\" into a question anyone can ask. A parent would\n"
               "have held that count in memory, where only it could see it and where a\n"
               "crash loses it."}),

      ("ctl","ctl","both","Compute what is needed","Policy meets observation. No I/O.",
       {"call":"no call — the controller's own arithmetic",
        "send":"target  = demand + buffer      = 10 + 2 = 12     (from its ConfigMap)\n"
               "needed  = target - (assigned + spare + running)\n"
               "        = 12     - (9        + 1     + 0)       = 2",
        "warn":"The target never touches the agent count: it is demand plus configuration.\n"
               "That is what makes a stale reading harmless. The agent count is used only\n"
               "here, to turn the target into a count, immediately before the call.\n\n"
               "needed <= 0 -> stay silent. No traffic on a steady fleet."}),

      ("ctl","tmp","both","Start N runs","POST install-server/bulk with count = 2.",
       {"call":"POST /workflows/server-lifecycle/install-server/bulk",
        "send":"{ \"infra_env\": \"cisco-m6-64c-512gb\",\n"
               "  \"mce_cluster\": \"ocp4-prod-mce-bat-yam\",\n"
               "  \"count\": 2 }",
        "recv":"202, a per-slot report: started / already_running / failed",
        "warn":"COUNT IS ADDITIVE, not a target. It starts that many NEW runs, and sending\n"
               "the same count again while the batch is installing ADDS to it. That is\n"
               "deliberate for an operator calling by hand. A controller must therefore\n"
               "subtract the running slots first, which is what the previous step did."}),

      ("tmp","tmp","both","Walk the slots","_1 upward, stepping over whatever is busy.",
       {"call":"start workflow  install-server-<infra_env>_1,  then _2, ...",
        "recv":"an already-started error means that slot is busy: count it, move on",
        "warn":"A finished slot is free again, because Temporal reuses the id of a closed\n"
               "run. Two requests at once cannot start one slot twice: one running\n"
               "workflow per id, and the loser moves on.\n\n"
               "candidate_count defaults to min(20, busy + count + 2). Every run draws\n"
               "from the SAME top of the available list, so each needs enough spares to\n"
               "step over what its siblings have locked. Without that headroom most of a\n"
               "parallel batch fails on the lock."}),

      ("tmp","hubw","both","Each run dispatches to the hub","From here on, one run, one machine.",
       {"call":"ExecuteActivity  ->  queue server-lifecycle-activity-ocp4-prod-mce-bat-yam",
        "send":"the activity name and typed arguments",
        "recv":"a typed result, recorded in that run's event history",
        "warn":"A workflow is REPLAYED from its history after a crash, so it must be\n"
               "deterministic: the sandbox blocks network, clock and randomness, and it\n"
               "holds no credentials. Every outside call is an ACTIVITY, run by a worker\n"
               "inside the cluster it talks to. On replay the activity is not re-run; the\n"
               "recorded result is read back.\n\n"
               "Every arrow after this starts at a WORKER, never at Temporal."}),

      ("segw","seg","both","Resolve inventory VLAN","On the segments queue, where that token lives.",
       {"call":"GET /api/segments?type=INVENTORY",
        "recv":"match client-side on cluster_name == hub; take vlan_id",
        "warn":"No cluster filter exists, so the hub match is made client-side and is not\n"
               "optional. Re-check the type too. Once per run, before any candidate."}),

      ("hubw","scan","both","Draw candidates","Both pool types. Virtual machines are in the inventory too.",
       {"call":"GET /servers/available"
               "?pattern=^ocp-<infra_env>&count=<candidate_count>&health=HEALTHY",
        "recv":"items[]: id, name, bmc_vendor, bmc{...}, interfaces[...], nic_macs[],\n"
               "         site_id, health_overall, live_recheck_performed",
        "warn":"Bond from interfaces[], NEVER nic_macs[]: partitioned cards collapse to one\n"
               "entry per port in the first and are left whole in the second.\n\n"
               "Not a pure read. It live-rechecks each candidate against the vendor manager\n"
               "and writes the result back. A 503 means that recheck failed, not that\n"
               "nothing is free."}),

      ("kvw","vm","VM","Create a machine","Virtual pools only, and only when the draw came back empty.",
       {"call":"CREATE kubevirt.io/v1 VirtualMachine   (on the KV cluster's queue)",
        "send":"cores and memory from the node type; labels for zone, node type, pool",
        "recv":"wait for running, then read the interface MAC",
        "warn":"PLANNED. This is the ONE divergence between the two pool types: a virtual\n"
               "pool can manufacture a machine, a bare-metal pool fails as pool-exhausted.\n\n"
               "Open: which KV cluster. The controller sees only its own hub, so it cannot\n"
               "name one. Same gap as deciding when a cell is full.\n\n"
               "Open: a new machine is invisible to the inventory until the collector sees\n"
               "it, so nothing can draw it and nothing can lock it either."}),

      ("hubw","scan","both","Reserve the machine","2h lease, holder is this run id.",
       {"call":"POST /servers/{id}/reservation          (needs an ADMIN token)",
        "send":"{ \"holder\": \"install-server\", \"mce_cluster\": ..., \"infra_env\": ...,\n"
               "  \"workflow_id\": \"install-server-<infra_env>_2\", \"ttl_seconds\": 7200 }",
        "recv":"200 -> expires_at.  ttl bounded 300..86400",
        "warn":"A 409 is TWO answers. With details.held_by a sibling run owns it: skip this\n"
               "candidate. Without it you lost a revision race, often to a collector:\n"
               "retryable. This lock is what stops two slots taking one machine."}),

      ("hubw","pxe","IPMI","Map the boot MAC","Blades have no virtual media, so they must PXE.",
       {"call":"PUT /pxe-map/<boot-mac>",
        "send":"{ \"infra_env\": { \"mce_cluster\": ..., \"name\": ... } }",
        "warn":"The BOOT MAC only, which is bond member one and the MAC the BareMetalHost\n"
               "boots from. Mapping both bond MACs breaks a blade whose firmware PXE-boots\n"
               "from the other NIC."}),

      ("hubw","res","both","Write NMStateConfig","First for IPMI, before anything can power the host on.",
       {"call":"CREATE agent-install.openshift.io/v1beta1 NMStateConfig",
        "send":"bond0        type: bond   ipv4 {enabled: false}\n"
               "bond0.<vlan> type: vlan   ipv4 {enabled: true, dhcp: true,\n"
               "                                auto-dns/gateway/routes: true}\n"
               "labels: {infraenvs.agent-install.openshift.io: <infra_env>}",
        "warn":"The VLAN sub-interface is the ONLY interface with an address, and it is\n"
               "leased. The segment contributes the tag; the DHCP scope decides the address."}),

      ("hubw","res","both","Write BMC Secret","Credentials never come from the inventory.",
       {"call":"CREATE core/v1 Secret",
        "send":"stringData: { username: ..., password: ... }",
        "warn":"The inventory returns BMC endpoints but never credentials; it does not hold\n"
               "them. They live in this worker's own Secret."}),

      ("hubw","res","both","Write BareMetalHost","For a virtual machine, its Redfish endpoint.",
       {"call":"CREATE metal3.io/v1alpha1 BareMetalHost",
        "send":"labels: {infraenvs.agent-install.openshift.io: <infra_env>}\n"
               "spec: online: true, bootMACAddress: <bond member 1>,\n"
               "      customDeploy: {method: start_assisted_install},\n"
               "      automatedCleaningMode: disabled, bootMode: UEFI,\n"
               "      bmc: {address, credentialsName, disableCertificateVerification}",
        "warn":"Create-if-absent with identity verification, NOT server-side apply. An\n"
               "existing object is success once BMC address, boot MAC, InfraEnv and VLAN\n"
               "match, and a conflict for a human when they differ. Nothing is ever\n"
               "overwritten, so an operator's hand correction survives a re-run."}),

      ("res","ienv","both","Image regenerates","Must include this host's config, or it boots an older one.",
       {"call":"GET agent-install.openshift.io/v1beta1 InfraEnv",
        "recv":"status.conditions[ImageCreated].status\n"
               "status.createdTime\n"
               "status.bootArtifacts.ipxeScript",
        "warn":"TWO conditions, not one. ImageCreated must be True, because it goes False\n"
               "when a generation FAILED and its message is the only place the cause\n"
               "appears. AND createdTime must be no older than this host's NMStateConfig.\n"
               "Checking only the timestamp passes an image made before the failure."}),

      ("hubw","pxe","IPMI","Publish the boot entry","Only now the image includes this host.",
       {"call":"PUT /pxe-map/<mce_cluster>/<infra_env>",
        "send":"{ \"ipxe_url\": ..., \"image_created_at\": ... }",
        "recv":"409 means a NEWER image is already published: that is success",
        "warn":"Distinct from mapping the MAC earlier. One entry per InfraEnv, not one per\n"
               "host, so republishing a regenerated image is one write. Compare-and-set on\n"
               "image_created_at, so a slow run can never roll an entry backwards."}),

      ("ienv","agt","both","Agent registers","The only proof BMC, bond, VLAN and DHCP all worked.",
       {"call":"LIST agent-install.openshift.io/v1beta1 Agent",
        "recv":"match on the bmh label, or on any MAC the Agent reports\n"
               "spec.clusterDeploymentName empty -> it is spare inventory",
        "warn":"An hour is budgeted, because bare metal POSTs for longer than a VM boots.\n"
               "The MAC fallback exists because an Agent is named after a hardware UUID\n"
               "and holds no reference back to the BareMetalHost."}),

      ("hubw","scan","both","Hold for 24h","Until something reports the machine claimed.",
       {"call":"POST /servers/{id}/reservation   with ttl_seconds: 86400",
        "warn":"Same workflow id, so this EXTENDS the lease rather than racing it. Needed\n"
               "because the inventory only learns a machine is taken when a cluster reports\n"
               "it, every 15 minutes."}),

      ("agt","ctl","both","Next pass sees it","No parent is waiting. The loop is the controller.",
       {"call":"nothing — the run has completed and exited",
        "recv":"the controller's next pass, within 2 minutes, lists Agents again",
        "warn":"Nothing owns convergence, and that is deliberate. A failed slot simply\n"
               "leaves the gap open and the next pass notices. The evaluation cycle IS the\n"
               "retry, which is why there is no pool-level attempt counter."}),
    ])

WORKFLOWS["02-remove-server-from-mce"] = dict(
    title="02 :: Remove Server from MCE",
    subtitle="Only runs when a pool has been <em>above</em> its ceiling for the cooldown. "
             "A tenant scaling down returns agents to the pool; it does not come here.",
    nodes={"tmp":  N(400, 16, "Temporal", "one run per machine", 210, 46),
           "scan": N(815, 16, "server-scan", "fleet inventory", 165, 46),
           "kvw":  N(812, 120, "KV worker", "PLANNED, same pattern", 156, 42),
           "vm":   N(812, 176, "VirtualMachine", "destroyed", 156, 42),
           "pxe":  N(20, 252, "PXE Host", "per site", 165, 46),
           "hubw": N(265, 292, "server-lifecycle worker", "the hub's own SA", 215, 48),
           "ctl":  N(505, 292, "Inventory Controller", "in-cluster", 240, 48),
           "agt":  N(265, 372, "Agent", "must be unbound", 215, 46),
           "res":  N(505, 372, "BMH + NMState", "+ BMC Secret", 240, 46),
           "gone": N(350, 452, "Verified gone", "nothing survives", 300, 46)},
    group=[{"x": 795, "y": 86, "w": 190, "h": 150, "label": "KV CLUSTER"},
           {"x": 240, "y": 250, "w": 530, "h": 290, "label": "MCE HUB"}],
    steps=[
      ("ctl","agt","both","Count the pool","Same read as adding. The controller looks.",
       {"call":"LIST agent-install.openshift.io/v1beta1 Agent",
        "recv":"assigned = 20,  spare = 6,  total = 26",
        "warn":"A machine being torn down is STILL an Agent until the teardown finishes, so\n"
               "it is counted here. That is why the next step exists."}),

      ("ctl","tmp","both","Ask which removals are open","The mirror of the in-flight problem.",
       {"call":"Temporal: list open workflows matching remove-server-*",
        "recv":"map each run's machine back to a pool, by its name",
        "warn":"Without this the controller over-removes. A removal in flight leaves its\n"
               "agent visible as spare, so the next pass sees the same surplus and picks a\n"
               "DIFFERENT machine to remove. Two minutes later, another.\n\n"
               "Harder than the add side: a removal id is remove-server-<machine>, keyed on\n"
               "the machine rather than the pool, so you cannot match by id pattern. The\n"
               "mapping is by machine name."}),

      ("ctl","ctl","both","Compute the surplus","Above the ceiling, and held there.",
       {"call":"no call — the controller's own arithmetic",
        "send":"ceiling = demand + high      = 20 + 4 = 24\n"
               "surplus = total - removing - ceiling\n"
               "        = 26    - 0        - 24       = 2",
        "warn":"Only fires above the CEILING, not below the target, and only after the\n"
               "surplus has persisted for the whole cooldown. Grow fast, shrink slowly:\n"
               "shrinking late wastes a little capacity, growing late stalls a tenant.\n\n"
               "Never shrink below target. The target is a floor as well as a ceiling."}),

      ("ctl","agt","both","Pick the victims","Unbound only, longest idle first.",
       {"call":"filter the Agents already listed",
        "recv":"spec.clusterDeploymentName empty, and no removal already open for it",
        "warn":"Never pick a bound agent: it is carrying a tenant workload. Never pick one\n"
               "mid-scrub either, which is the same rule that keeps a scrubbing machine out\n"
               "of the spare count."}),

      ("ctl","tmp","both","Start one run per machine","No slots needed here.",
       {"call":"POST /workflows/server-lifecycle/remove-server   (once per machine)",
        "send":"{ \"server_name\": \"ocp-cisco-m6-bat-yam-64c-512gb-J3K9L2\",\n"
               "  \"mce_cluster\": \"ocp4-prod-mce-bat-yam\" }",
        "recv":"202 per call, or 409 if a removal for that machine is already in flight",
        "warn":"Unlike adding, the id is remove-server-<machine>, so N removals are N\n"
               "distinct ids and run in parallel by construction. No slot mechanism is\n"
               "needed, and a repeat for the same machine deduplicates for free.\n\n"
               "The distinct prefix also keeps a removal from colliding with an install of\n"
               "the same machine."}),

      ("tmp","hubw","both","Each run dispatches to the hub","From here on, one run, one machine.",
       {"call":"ExecuteActivity  ->  queue server-lifecycle-activity-ocp4-prod-mce-bat-yam",
        "warn":"The workflow holds no credentials and makes no calls; it is replayed from\n"
               "history and must be deterministic. Every arrow below starts at a worker."}),

      ("hubw","agt","both","Re-check it is unbound","The race, and the last chance to catch it.",
       {"call":"GET agent-install.openshift.io/v1beta1 Agent/<name>",
        "recv":"spec.clusterDeploymentName must STILL be empty",
        "warn":"Read it again immediately before the destructive step. The controller\n"
               "chose this machine seconds or minutes ago; a NodePool can claim it at any\n"
               "instant in between."}),

      ("hubw","res","both","Detach the host","Before any delete. This one is load-bearing.",
       {"call":"PATCH metal3.io/v1alpha1 BareMetalHost/<name>",
        "send":"metadata.annotations:\n  baremetalhost.metal3.io/detached: \"\"",
        "warn":"Delete first and Metal3's finalizer tries to deprovision through a BMC that\n"
               "is no longer answering, and blocks forever. Measured at 14 hours with no\n"
               "error reported."}),

      ("hubw","res","both","Delete NMState, BMH, Secret","In that order. A missing object is success.",
       {"call":"DELETE NMStateConfig, then BareMetalHost, then Secret",
        "recv":"404 on any of them is success",
        "warn":"A successful delete call does NOT mean the object is gone; finalizers can\n"
               "hold it. Confirming is the next two steps' job."}),

      ("res","gone","both","Clear the one finalizer","Only metal3's. Never the whole list.",
       {"call":"PATCH BareMetalHost finalizers, after a bounded wait",
        "send":"remove only: baremetalhost.metal3.io",
        "warn":"Clearing the whole list strips finalizers belonging to other controllers and\n"
               "leaks whatever they were protecting."}),

      ("hubw","gone","both","Verify all three gone","Verification ends the run, not the deletes.",
       {"call":"GET each of the three",
        "recv":"all three must 404",
        "warn":"The machine is NOT returned to inventory while a BareMetalHost still points\n"
               "at it."}),

      ("hubw","pxe","IPMI","Unmap the boot MAC","Mirror of the mapping on the way in.",
       {"call":"DELETE /pxe-map/<boot-mac>",
        "recv":"404 is success",
        "warn":"Only its own site's PXE host. A removal does not touch the others."}),

      ("kvw","vm","VM","Delete the machine","On the KV cluster, by that cluster's worker.",
       {"call":"DELETE kubevirt.io/v1 VirtualMachine",
        "warn":"PLANNED. With no overcommit at the hypervisor layer this is what actually\n"
               "returns the CPU and memory; until it runs they stay reserved for a guest\n"
               "that no longer exists."}),

      ("hubw","scan","both","Release the lock","And then wait, because you cannot push the rest.",
       {"call":"DELETE /servers/{id}/reservation",
        "send":"{ \"holder\": \"remove-server\", \"workflow_id\": \"...\" }",
        "warn":"There is NO endpoint that marks a machine available again. A status job on\n"
               "the hub notices the Agent has gone, every 15 minutes, and sets it. Do not\n"
               "design a step that tries to push it."}),

      ("gone","ctl","both","Next pass sees it","No parent is waiting. The loop is the controller.",
       {"call":"nothing — the run has completed and exited",
        "recv":"the controller's next pass, within 2 minutes, counts the pool again",
        "warn":"Nothing owns convergence, deliberately. A failed removal leaves the surplus\n"
               "in place and the next pass notices. The evaluation cycle IS the retry."}),
    ])

WORKFLOWS["03-create-hosted-cluster"] = dict(
    title="03 :: Create Hosted Cluster",
    subtitle="Creates demand and stops. Workers appear because the controller sees the new "
             "demand and fills the pools. The run ends at control plane available.",
    nodes={"cust": CUST, "tmp": TMP, "seg": SEG, "git": GIT, "argo": ARGO, "hub": HUB,
           "ctl": N(800, 300, "Inventory Controller", "sees new demand"),
           "pool": N(800, 430, "Pools fill", "via workflow 01")},
    group=None,
    steps=[
      ("cust","tmp","both","Request a cluster","Name, hub, release by digest, worker types per zone."),
      ("tmp","tmp","both","Validate","Hub known, pools exist for every type in all three zones."),
      ("tmp","seg","both","Allocate a segment","An HC segment at the hub's site.",
       {"call":"POST /api/segments/allocate            (bearer token)",
        "send":"{ \"cluster_name\": \"tenant-prod-01\",\n"
               "  \"site\": \"bat-yam\",\n"
               "  \"type\": \"HC\" }",
        "recv":"{ \"vlan_id\": 112, \"segment\": \"10.20.94.0/24\",\n"
               "  \"cluster_name\": \"tenant-prod-01\", \"type\": \"HC\" }",
        "warn":"Idempotent server-side per (cluster, site, type), so a retry returns the\n"
               "same allocation. A 503 means the site's pool is EMPTY: terminal, a human\n"
               "creates segments, waiting will not help.\n\n"
               "cluster_name may come back as a LIST for shared segments."}),

      ("seg","tmp","both","Verify the allocation","Status, cluster, VLAN and type must all match.",
       {"call":"GET /api/segments/by-segment?segment=<CIDR>",
        "recv":"status == \"Allocated\"  and  cluster_name == ours\n"
               "vlan_id == the one just handed back  and  type == \"HC\"",
        "warn":"Read back BEFORE committing anything to Git, so the repository can never\n"
               "record a VLAN the Segments Manager does not confirm."}),

      ("tmp","git","both","Commit manifests","HostedCluster plus one NodePool per zone per type.",
       {"call":"git clone --depth 1 -b <values_branch>, edit, commit, push",
        "send":"sites/<site>/mces/<hub>/hostedClusters/<cluster>.yaml\n"
               "  vlanId: 112\n"
               "  dhcp_values: {network: \"10.20.94.0\", exclusions: [...]}\n"
               "plus HostedCluster and one NodePool per zone per worker type",
        "warn":"The commit message carries [skip ci], or the push restarts the pipeline that\n"
               "called this workflow. The token is injected in memory and scrubbed from\n"
               "every message before the exception object exists, because activity errors\n"
               "land verbatim in Temporal history."}),
      ("git","argo","both","Human merges to main","ArgoCD only reads main. The one manual step."),
      ("argo","hub","both","Apply","Control plane pods start on the hub."),
      ("hub","tmp","both","Await control plane","Run completes here. Not at workers ready."),
      ("hub","ctl","both","Demand is now visible","NodePools want agents that may not exist yet."),
      ("ctl","pool","both","Controller fills the pools","Deep pool looks instant. Empty pool means a full build."),
    ])

WORKFLOWS["04-update-nodepool"] = dict(
    title="04 :: Update NodePool",
    subtitle="The only place a customer's desired worker count enters the system. "
             "Everything downstream is a consequence. This never provisions anything.",
    nodes={"cust": CUST, "tmp": TMP, "git": GIT, "argo": ARGO, "hub": HUB,
           "ctl": N(800, 180, "Inventory Controller", "sees the change"),
           "up":  N(800, 300, "Scale up", "pool below target"),
           "dn":  N(800, 430, "Scale down", "agents return to pool")},
    group=None,
    steps=[
      ("cust","tmp","both","Request a total","Absolute across all three zones, never a delta."),
      ("tmp","tmp","both","Validate","At least three, so every zone keeps a node."),
      ("tmp","git","both","Read current replicas","All three zones in one clone.",
       {"call":"git clone --depth 1 -b <values_branch>",
        "recv":"nodepools/<cluster>/<node_type>/zone-1.yaml  spec.replicas\n"
               "                               /zone-2.yaml  spec.replicas\n"
               "                               /zone-3.yaml  spec.replicas",
        "warn":"One clone for all three zones, so the three values are read at a single\n"
               "point in time and cannot disagree with each other."}),

      ("tmp","tmp","both","Spread across zones","Evenly; earlier zones take the remainder.",
       {"call":"pure function, no I/O",
        "send":"base  = desired_total / 3\n"
               "extra = desired_total % 3\n"
               "the first `extra` zones get base + 1\n\n"
               "10 -> 4,3,3      9 -> 3,3,3      5 -> 2,2,1",
        "warn":"Deterministic, so the same total always produces the same layout regardless\n"
               "of what is currently deployed. That is what makes a replay safe."}),

      ("tmp","git","both","Commit, or skip","No change means no commit at all.",
       {"call":"patch spec.replicas on changed zones, one commit, push",
        "send":"commit: scale <cluster>/<node_type> -> total=10 "
               "(zone-1=4, zone-2=3, zone-3=3) [skip ci]",
        "warn":"Zones whose value is unchanged are not staged. If nothing changed there is\n"
               "no commit, which keeps the repository readable by humans rather than\n"
               "filling it with no-op churn."}),
      ("git","argo","both","Human merges to main","Out of band."),
      ("argo","hub","both","Apply to the hub","NodePool replicas updated."),
      ("hub","ctl","both","Controller observes","Demand moved. It does not get told; it looks."),
      ("ctl","up","both","If demand rose","Pool below target, workflow 01 runs."),
      ("ctl","dn","both","If demand fell","Agents unbind and wait in the pool. Nothing is destroyed."),
    ])

WORKFLOWS["05-delete-hosted-cluster"] = dict(
    title="05 :: Delete Hosted Cluster",
    subtitle="Order matters more here than anywhere else. Each step exists because the next "
             "one is unsafe without it.",
    nodes={"cust": CUST, "tmp": TMP, "git": GIT, "argo": ARGO, "hub": HUB,
           "seg": SEG, "dhcp": DHCP,
           "pool": N(800, 300, "Pool", "capacity returns")},
    group=None,
    steps=[
      ("cust","tmp","both","Request deletion","Explicit confirmation. This is not reversible."),
      ("tmp","hub","both","Drain workloads","Bounded. A decommission is not blocked by its own workloads."),
      ("tmp","git","both","Remove the manifests","Deleting the files IS the decommission."),
      ("git","argo","both","Human merges to main","Out of band."),
      ("argo","hub","both","Prune every resource","Nothing below is safe while one still exists."),
      ("hub","pool","both","Agents unbind","Worker capacity returns to the pool after a disk reset."),
      ("tmp","dhcp","both","Delete the scope","After the prune. A live application re-creates it in a minute.",
       {"call":"GET  /api/v1/scopes/<network>     then\n"
               "DELETE /api/v1/scopes/<network>   then GET again to confirm absence",
        "recv":"204 whether or not the scope existed",
        "warn":"Runs PowerShell against a remote Windows DHCP server, so budget 150s.\n"
               "If the GitOps application is still alive it re-POSTs the scope about a\n"
               "minute later, which is why this comes after the prune, not before."}),

      ("tmp","seg","both","Release the segment","After the scope. Re-read the owner first.",
       {"call":"GET /api/segments/by-segment   then   POST /api/segments/release",
        "send":"{ \"segment\": \"10.20.94.0/24\" }",
        "warn":"Re-read the owner immediately first. The release endpoint does not check\n"
               "who holds it, so without that check this can release a segment another\n"
               "cluster now owns. An already-available segment answers success."}),

      ("seg","tmp","both","Verify released","Available, no cluster, no type.",
       {"call":"GET /api/segments/by-segment?segment=<CIDR>",
        "recv":"status == \"Available\",  cluster_name is null,  type is null"}),
    ])

WORKFLOWS["07-add-bm-to-kv-cluster"] = dict(
    title="07 :: Add Bare-Metal Node to a KV Cluster",
    subtitle="No steps of its own. This is workflow 04 then workflow 01, with the Seed as "
             "the hub, plus one wait that only matters here.",
    nodes={"op": N(30, 36, "Operator", "or capacity policy"), "tmp": TMP,
           "git": GIT, "argo": ARGO, "seed": N(380, 430, "Seed Cluster", "the hub here", 240),
           "ctl": N(800, 180, "Controller on Seed", "BM pool"),
           "scan": SCAN, "kvn": N(800, 430, "New hypervisor", "KubeVirt schedulable")},
    group=None,
    steps=[
      ("op","tmp","both","Raise the node count","Workflow 04 with hub = Seed, pool type = BM."),
      ("tmp","git","both","Commit the NodePool","Same manifest shape as any tenant cluster."),
      ("git","argo","both","Merge and apply","Out of band, then ArgoCD."),
      ("argo","seed","both","Seed claims free agents","Its NodePool controller takes what is already there."),
      ("seed","ctl","both","Controller sees the gap","Demand now exceeds supply in the BM pool."),
      ("ctl","scan","both","Draw and install a machine","Workflow 01 on the Seed."),
      ("scan","kvn","both","Node joins","Reports Ready."),
      ("kvn","kvn","both","Await KubeVirt","A joined node is not capacity until KubeVirt runs on it."),
    ])

WORKFLOWS["08-remove-bm-from-kv-cluster"] = dict(
    title="08 :: Remove Bare-Metal Node from a KV Cluster",
    subtitle="The evacuation is the whole difficulty. A hypervisor carries other people's "
             "running machines, so it cannot simply be removed.",
    nodes={"op": N(30, 36, "Operator", "maintenance or shrink"), "tmp": TMP,
           "cell": N(760, 36, "Cell headroom", "can it absorb?", 200),
           "node": N(400, 180, "Hypervisor", "being removed", 200),
           "guests": N(760, 180, "Tenant VMs", "must move first", 200),
           "git": GIT, "argo": ARGO, "seed": N(380, 430, "Seed Cluster", "the hub", 240)},
    group=None,
    steps=[
      ("op","tmp","both","Request removal","For maintenance, decommissioning, or a shrinking cell."),
      ("tmp","cell","both","Check headroom","The rest of the cell must hold these guests, margin intact."),
      ("tmp","node","both","Cordon","No new guest lands here."),
      ("node","guests","both","Live-migrate every guest","Possible only because guest storage is shared."),
      ("guests","tmp","both","Verify empty","A guest that will not move stops the run, named. Never force."),
      ("tmp","git","both","Shrink the NodePool","Workflow 04 against the Seed, total minus one."),
      ("git","argo","both","Merge and apply","Out of band."),
      ("argo","seed","both","Node leaves","Then workflow 02 tears down its host resources."),
    ])

WORKFLOWS["09-decommission-kv-cluster"] = dict(
    title="09 :: Decommission a KV Cluster",
    subtitle="Cell contraction. The most disruptive workflow in the platform, because every "
             "guest on the cell has to go somewhere else first.",
    nodes={"pol": N(30, 36, "Capacity policy", "or operator"), "tmp": TMP,
           "cell": N(380, 170, "This cell", "under threshold", 200),
           "tgt":  N(760, 170, "Target cells", "same zone, with room", 200),
           "seed": N(380, 310, "Seed Cluster", "hosts its control plane", 200),
           "bm":   N(760, 310, "Hypervisors", "return to inventory", 200),
           "seg": SEG},
    group=None,
    steps=[
      ("pol","tmp","both","Sustained under threshold","A quiet afternoon is not a reason. Cooldown in days."),
      ("tmp","tgt","both","Find destinations","Same zone, enough room for every guest, margin intact."),
      ("tmp","cell","both","Mark draining","Nothing new is placed here."),
      ("cell","tgt","both","Evacuate every hypervisor","Cross-cluster migration. Verify this works before building it."),
      ("tmp","cell","both","Verify empty","Any guest left stops the run."),
      ("tmp","seed","both","Delete the cluster","Workflow 05 with hub = Seed."),
      ("seed","bm","both","Hypervisors return","To inventory, or destroyed if the pool is over its ceiling."),
      ("tmp","seg","both","Release the segment","Handled inside the delete."),
    ])

WORKFLOWS["11-add-bm-to-seed-cluster"] = dict(
    title="11 :: Add Bare-Metal Node to a Seed Cluster",
    subtitle="The one cluster that is not hosted, so there is no NodePool to change. "
             "Blocked on a decision: which join mechanism.",
    nodes={"op": N(30, 36, "Operator", "rare"), "tmp": TMP, "scan": SCAN,
           "seg": SEG,
           "a": N(330, 300, "Agent flow", "reuses workflow 01", 200),
           "b": N(610, 300, "Ignition flow", "classic UPI", 200),
           "seed": N(400, 440, "Seed Cluster", "new worker joins", 200)},
    group={"x": 300, "y": 262, "w": 540, "h": 92, "label": "UNDECIDED — PICK ONE"},
    steps=[
      ("op","tmp","both","Request capacity","The Seed is short of room for the control planes it hosts."),
      ("tmp","scan","both","Draw and reserve a machine","Same draw and lock as workflow 01."),
      ("tmp","seg","both","Resolve the Seed's network","Its own network, not a tenant VLAN."),
      ("tmp","a","both","Option A: Agent flow","Nearly all of 01 is reusable. The Seed stops being special."),
      ("tmp","b","both","Option B: ignition","No dependency on the Seed's own operators. Manual."),
      ("a","seed","both","Node joins","Then approve certificates if the path needs it."),
      ("seed","seed","both","Await ready","Workloads schedule onto it."),
    ])

WORKFLOWS["13-upgrade-hosted-cluster-workers"] = dict(
    title="13 :: Upgrade Hosted Cluster Workers",
    subtitle="Separate from the control plane so unpredictable workload drain cannot delay "
             "API availability. Run 14 first.",
    nodes={"cust": CUST, "tmp": TMP, "git": GIT, "argo": ARGO,
           "pool": N(800, 180, "Warm pool", "supplies surge nodes"),
           "z1": N(300, 430, "Zone 1", "rolls first", 150),
           "z2": N(470, 430, "Zone 2", "then", 150),
           "z3": N(640, 430, "Zone 3", "then", 150)},
    group={"x": 280, "y": 394, "w": 530, "h": 110, "label": "ONE ZONE AT A TIME"},
    steps=[
      ("cust","tmp","both","Choose a window","The tenant picks when."),
      ("tmp","pool","both","Check surge capacity","Each surging node comes out of the warm pool."),
      ("tmp","tmp","both","Preflight","Healthy, nothing else in flight, control plane already upgraded."),
      ("tmp","git","both","Patch the release image","By digest. Set surge explicitly; do not inherit a default."),
      ("git","argo","both","Merge and apply","Out of band."),
      ("argo","z1","both","Roll zone 1","New node up before the old one drains."),
      ("z1","z2","both","Roll zone 2","Only after zone 1 settles."),
      ("z2","z3","both","Roll zone 3","A failure mid-roll then affects one zone, not three."),
    ])

WORKFLOWS["14-upgrade-hosted-cluster-control-plane"] = dict(
    title="14 :: Upgrade Hosted Cluster Control Plane",
    subtitle="Pods on a hub, no tenant workloads to drain, so it is fast and predictable. "
             "Gated by what the hub supports.",
    nodes={"cust": CUST, "tmp": TMP, "git": GIT, "argo": ARGO,
           "hub": N(760, 36, "Hub version", "the ceiling", 200),
           "etcd": N(760, 180, "etcd backup", "verified, not just taken", 200),
           "cp": N(400, 430, "Control plane", "3 replicas, one at a time", 240)},
    group=None,
    steps=[
      ("cust","tmp","both","Choose a window","Or the platform does; it should be invisible."),
      ("tmp","hub","both","Check the ceiling","A cluster may never be newer than its hub supports."),
      ("tmp","tmp","both","Preflight","API up, quorum intact, no degraded operators."),
      ("tmp","etcd","both","Back up and verify","An unverified backup produces confidence, not safety."),
      ("tmp","git","both","Patch the release image","By digest."),
      ("git","argo","both","Merge and apply","Out of band."),
      ("argo","cp","both","Roll one replica at a time","Two of three stay up throughout."),
      ("cp","tmp","both","Verify between replicas","Losing quorum mid-roll is the hard failure."),
    ])

WORKFLOWS["15-upgrade-mce-hub"] = dict(
    title="15 :: Upgrade an MCE Hub",
    subtitle="The skew check is the real work here. The upgrade itself is ordinary.",
    nodes={"op": N(30, 36, "Operator", "platform"), "tmp": TMP,
           "hcs": N(760, 36, "Hosted clusters", "every one on this hub", 200),
           "ctl": N(760, 180, "Controller", "paused during the roll", 200),
           "cp": N(330, 430, "Hub masters", "one at a time", 200),
           "wk": N(570, 430, "Hub workers", "with surge", 200)},
    group=None,
    steps=[
      ("op","tmp","both","Request the upgrade","Raises the ceiling for everything hosted here."),
      ("tmp","hcs","both","Inventory what is hosted","Every cluster, with its current version."),
      ("tmp","hcs","both","Compute skew both ways","Too far forward strands the oldest clusters."),
      ("tmp","tmp","both","Preflight and back up","Healthy, quorum intact, nothing in flight anywhere."),
      ("tmp","ctl","both","Pause the controller","Rolling workers churns pods; do not act on transient state."),
      ("tmp","cp","both","Roll the masters","Quorum held throughout."),
      ("cp","wk","both","Roll the workers","They carry the hosted control planes."),
      ("wk","ctl","both","Resume the controller","Must happen on the failure path too."),
    ])

WORKFLOWS["16-upgrade-kv-cluster"] = dict(
    title="16 :: Upgrade a KV Cluster",
    subtitle="The most careful upgrade in the platform. Every node being rolled is carrying "
             "other people's running machines.",
    nodes={"op": N(30, 36, "Operator", "platform"), "tmp": TMP,
           "seed": N(760, 36, "Seed", "hosts the control plane", 200),
           "mark": N(760, 180, "Cell marked", "upgrading", 200),
           "hv": N(330, 430, "Hypervisor", "one at a time", 200),
           "tgt": N(570, 430, "Rest of cell", "absorbs its guests", 200)},
    group=None,
    steps=[
      ("op","tmp","both","Request the upgrade","Blast radius is every tenant VM on the cell."),
      ("tmp","tmp","both","Preflight","Cell healthy, shared storage healthy, nothing in flight."),
      ("tmp","mark","both","Mark the cell upgrading","Placement must honour it, or the next evacuation fails."),
      ("tmp","seed","both","Upgrade the control plane","Workflow 14 with the Seed as hub."),
      ("tmp","hv","both","Cordon one hypervisor","Only one, ever."),
      ("hv","tgt","both","Evacuate its guests","Live migration. A guest that will not move stops the roll."),
      ("tgt","hv","both","Upgrade and wait","Ready, then KubeVirt schedulable, then uncordon."),
      ("hv","mark","both","Next hypervisor, or done","Clear the mark at the end."),
    ])

WORKFLOWS["17-upgrade-seed-cluster"] = dict(
    title="17 :: Upgrade a Seed Cluster",
    subtitle="First in a zone. Its workers carry the control planes of every KubeVirt "
             "cluster there, so infrastructure management pauses — tenant VMs do not.",
    nodes={"op": N(30, 36, "Operator", "rare"), "tmp": TMP,
           "kvs": N(760, 36, "KV clusters", "their control planes live here", 200),
           "vms": N(760, 180, "Tenant VMs", "unaffected throughout", 200),
           "cp": N(330, 430, "Seed masters", "one at a time", 200),
           "wk": N(570, 430, "Seed workers", "with surge", 200)},
    group=None,
    steps=[
      ("op","tmp","both","Request the upgrade","First step in the zone's upgrade order."),
      ("tmp","kvs","both","Compute skew","Against the KV clusters hosted here, not tenant clusters."),
      ("tmp","tmp","both","Preflight","No cell operation anywhere in the zone may be in flight."),
      ("tmp","cp","both","Roll the masters","Quorum held."),
      ("cp","wk","both","Roll the workers with surge","Hosted control planes move before the old node goes."),
      ("wk","kvs","both","KV APIs return","Creating, scaling and upgrading cells was blocked until now."),
      ("kvs","vms","both","Tenant VMs never noticed","The data plane is independent of the control plane."),
    ])

WORKFLOWS["18-migrate-hosted-cluster-version-band"] = dict(
    title="18 :: Migrate a Hosted Cluster to Another Version Band",
    subtitle="A rebuild, not a move. No operation relocates a control plane, and a cluster's "
             "NodePools cannot span hubs.",
    nodes={"cust": CUST, "tmp": TMP,
           "old": N(330, 430, "Old hub", "stays up throughout", 200),
           "new": N(610, 430, "New hub", "newer version band", 200),
           "cap": N(760, 36, "Capacity check", "both clusters at once", 200),
           "ten": N(760, 180, "Tenant", "moves their workloads", 200)},
    group=None,
    steps=[
      ("cust","tmp","both","Request a newer version","Beyond what their hub can reach."),
      ("tmp","cap","both","Check capacity for both","The old cluster stays up, so the pools carry double."),
      ("tmp","new","both","Create the new cluster","Workflow 03 on the target hub, same worker shape."),
      ("new","tmp","both","Await full readiness","Control plane healthy and every worker present."),
      ("tmp","ten","both","Tenant moves their workloads","Not a platform step. Mostly repointing their pipelines."),
      ("ten","tmp","both","Wait for confirmation","Indefinitely. A timeout here is a data-loss incident."),
      ("tmp","old","both","Delete the old cluster","Workflow 05. Only now."),
    ])


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>__TITLE__</title>
<style>
  :root{
    --bg:#0e0e11; --panel:#16161b; --line:#2c2c35; --ink:#e6e6ec; --dim:#8b8b99;
    --accent:#4da3ff; --ok:#3ecf8e; --warn:#e8b339; --vm:#b57bff; --bm:#58c7d8;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
       font:14px/1.5 "Segoe UI",Roboto,Helvetica,Arial,sans-serif}
  header{padding:18px 24px 10px;border-bottom:1px solid var(--line)}
  h1{margin:0;font-size:19px;font-weight:600;letter-spacing:.2px}
  .sub{color:var(--dim);font-size:13px;margin-top:4px;max-width:900px}
  .wrap{display:flex;align-items:stretch;min-height:700px}
  .stage{flex:1;padding:14px 10px 12px 18px;display:flex;flex-direction:column;min-width:0}
  #svg{width:100%;height:400px;flex:0 0 auto}
  .detail{margin-top:10px;border:1px solid var(--line);border-radius:8px;background:#121217;
          padding:11px 14px;flex:1;min-height:210px;max-height:350px;overflow:auto}
  .dh{color:var(--dim);font-size:10.5px;text-transform:uppercase;letter-spacing:1.2px;
      margin:0 0 8px;font-weight:600}
  .k{color:var(--dim);font-size:10px;text-transform:uppercase;letter-spacing:1px;
     display:block;margin:9px 0 3px}
  .k:first-of-type{margin-top:0}
  .detail pre{margin:0;white-space:pre-wrap;word-break:break-word;
       font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
       font-size:11.5px;line-height:1.55;color:#cfd3da}
  pre.call{color:var(--accent)}
  pre.warn{color:var(--warn)}
  .empty{color:var(--dim);font-style:italic;font-size:12.5px}
  .side{width:366px;border-left:1px solid var(--line);background:var(--panel);
        padding:14px 16px;overflow-y:auto;max-height:770px}
  .side h2{font-size:12px;text-transform:uppercase;letter-spacing:1.2px;
           color:var(--dim);margin:0 0 10px;font-weight:600}
  .step{padding:8px 10px;border-radius:6px;cursor:pointer;display:flex;gap:9px;
        border:1px solid transparent;margin-bottom:3px}
  .step:hover{background:#1e1e25}
  .step.on{background:#1b2838;border-color:var(--accent)}
  .step.off{opacity:.3}
  .num{color:var(--dim);font-variant-numeric:tabular-nums;min-width:22px;font-size:12px}
  .step.on .num{color:var(--accent)}
  .txt{flex:1}
  .nm{font-weight:500;display:block}
  .nt{color:var(--dim);font-size:12px;margin-top:2px;display:block}
  .tag{font-size:10px;padding:1px 6px;border-radius:99px;border:1px solid var(--line);
       color:var(--dim);align-self:flex-start;margin-top:2px}
  .tag.VM{color:var(--vm);border-color:var(--vm)}
  .tag.BM{color:var(--bm);border-color:var(--bm)}
  .tag.IPMI{color:var(--warn);border-color:var(--warn)}
  footer{display:flex;gap:10px;align-items:center;padding:12px 18px;
         border-top:1px solid var(--line);flex-wrap:wrap}
  button{background:#1e1e25;color:var(--ink);border:1px solid var(--line);
         border-radius:6px;padding:7px 14px;cursor:pointer;font-size:13px}
  button:hover{border-color:var(--accent)}
  button.pri{background:var(--accent);border-color:var(--accent);color:#06121f;font-weight:600}
  .seg{display:inline-flex;border:1px solid var(--line);border-radius:6px;overflow:hidden}
  .seg button{border:0;border-radius:0}
  .seg button.sel{background:#2a3a4d;color:var(--accent)}
  .spacer{flex:1}
  .caption{color:var(--dim);font-size:12px}
  text{font-family:"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
  .node rect{fill:#191920;stroke:var(--line);stroke-width:1.5;rx:8}
  .node.hot rect{stroke:var(--accent);stroke-width:2.5;fill:#1b2838}
  .node.done rect{stroke:#2f5d48}
  .node text{fill:var(--ink);font-size:13px;font-weight:500}
  .node tspan.s{fill:var(--dim);font-size:11px;font-weight:400}
  .group rect{fill:none;stroke:var(--line);stroke-dasharray:4 4;rx:10}
  .group text{fill:var(--dim);font-size:11px;letter-spacing:1px}
  .edge{stroke:#3a3a46;stroke-width:1.5;fill:none}
  .edge.hot{stroke:var(--accent);stroke-width:2.5}
  .elab{fill:var(--accent);font-size:11.5px;font-weight:500}
  #dot{fill:var(--accent);filter:drop-shadow(0 0 6px var(--accent))}
</style>
</head>
<body>
<header>
  <h1>__TITLE__</h1>
  <div class="sub">__SUB__</div>
</header>

<div class="wrap">
  <div class="stage">
    <svg id="svg" viewBox="0 0 1000 560"
         xmlns="http://www.w3.org/2000/svg" aria-label="Workflow diagram"></svg>
    <div class="detail" id="detail"></div>
  </div>
  <aside class="side">
    <h2>Steps</h2>
    <div id="steps"></div>
  </aside>
</div>

<footer>
  <button class="pri" id="play">Play</button>
  <button id="prev">Prev</button>
  <button id="next">Next</button>
  <button id="rst">Restart</button>
  <span id="modes" style="display:none">
    <span class="caption">Pool</span>
    <span class="seg">
      <button id="mBM" class="sel">Bare metal</button>
      <button id="mVM">Virtual</button>
    </span>
  </span>
  <div class="spacer"></div>
  <span class="caption" id="cap"></span>
</footer>

<script>
const NODES = __NODES__;
const GROUPS = __GROUPS__;
const RAW   = __STEPS__;
const HAS_MODES = __MODES__;
const STEPS = RAW.map(r => ({f:r[0], t:r[1], b:r[2], n:r[3], d:r[4], x:r[5]||null}));
const esc = s => String(s).replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));

function showDetail(s){
  const box=document.getElementById("detail");
  if(!s){ box.innerHTML='<div class="dh">Exchange</div>'+
      '<div class="empty">Pick a step, or press play.</div>'; return; }
  if(!s.x){ box.innerHTML='<div class="dh">Exchange &mdash; '+esc(s.n)+'</div>'+
      '<div class="empty">Not documented yet for this step.</div>'; return; }
  let h='<div class="dh">Exchange &mdash; '+esc(s.n)+'</div>';
  if(s.x.call) h+='<span class="k">Call</span><pre class="call">'+esc(s.x.call)+'</pre>';
  if(s.x.send) h+='<span class="k">Sends</span><pre>'+esc(s.x.send)+'</pre>';
  if(s.x.recv) h+='<span class="k">Receives</span><pre>'+esc(s.x.recv)+'</pre>';
  if(s.x.warn) h+='<span class="k">Watch out</span><pre class="warn">'+esc(s.x.warn)+'</pre>';
  box.innerHTML=h;
}

const svg = document.getElementById("svg");
const NS = "http://www.w3.org/2000/svg";
const el = (n,a={}) => { const e=document.createElementNS(NS,n);
  for(const k in a) e.setAttribute(k,a[k]); return e; };
const cx = n => NODES[n].x + NODES[n].w/2;
const cy = n => NODES[n].y + NODES[n].h/2;

let mode="BM", idx=-1, playing=false, timer=null;
const vis = s => s.b==="both" || s.b===mode || (s.b==="IPMI" && mode==="BM");
const active = () => STEPS.map((s,i)=>i).filter(i=>vis(STEPS[i]));

function drawStatic(){
  svg.innerHTML="";
  GROUPS.forEach(G=>{
    const g = el("g",{class:"group"});
    g.appendChild(el("rect",{x:G.x,y:G.y,width:G.w,height:G.h}));
    const gt = el("text",{x:G.x+14,y:G.y+20}); gt.textContent=G.label;
    g.appendChild(gt); svg.appendChild(g);
  });
  svg.appendChild(el("g",{id:"edges"}));
  for(const k in NODES){
    const d=NODES[k];
    const n=el("g",{class:"node",id:"n_"+k});
    n.appendChild(el("rect",{x:d.x,y:d.y,width:d.w,height:d.h}));
    const t=el("text",{x:d.x+d.w/2,y:d.y+d.h/2-3,"text-anchor":"middle"});
    t.textContent=d.t;
    if(d.s){ const s=el("tspan",{x:d.x+d.w/2,dy:"15",class:"s"}); s.textContent=d.s;
             t.appendChild(s); }
    n.appendChild(t); svg.appendChild(n);
  }
  svg.appendChild(el("circle",{id:"dot",r:7,opacity:0}));
}

function path(a,b){
  if(a===b){ // self-loop: below the node when it sits near the top edge
    const d=NODES[a], x=cx(a);
    if(d.y < 72){ const y=d.y+d.h;
      return `M ${x-28} ${y} C ${x-40} ${y+46} ${x+40} ${y+46} ${x+28} ${y}`; }
    const y=d.y;
    return `M ${x-28} ${y} C ${x-40} ${y-46} ${x+40} ${y-46} ${x+28} ${y}`;
  }
  const x1=cx(a), y1=cy(a), x2=cx(b), y2=cy(b);
  const mx=(x1+x2)/2, my=(y1+y2)/2;
  const dx=x2-x1, dy=y2-y1, len=Math.hypot(dx,dy)||1;
  const bow=Math.min(46,len*0.12);
  return `M ${x1} ${y1} Q ${mx - dy/len*bow} ${my + dx/len*bow} ${x2} ${y2}`;
}

function render(){
  document.querySelectorAll(".node").forEach(n=>n.classList.remove("hot","done"));
  const edges=document.getElementById("edges"); edges.innerHTML="";
  document.getElementById("dot").setAttribute("opacity",0);

  const act=active(), here=act.indexOf(idx);
  act.forEach((si,pos)=>{ if(here>=0 && pos<here)
    document.getElementById("n_"+STEPS[si].t)?.classList.add("done"); });

  if(idx>=0 && STEPS[idx]){
    const s=STEPS[idx];
    const p=el("path",{class:"edge hot",d:path(s.f,s.t)});
    edges.appendChild(p);
    document.getElementById("n_"+s.f).classList.add("hot");
    document.getElementById("n_"+s.t).classList.add("hot");

    const total=p.getTotalLength();
    const mid=p.getPointAtLength(total*0.5);
    const a=p.getPointAtLength(Math.max(0,total*0.5-6));
    const b=p.getPointAtLength(Math.min(total,total*0.5+6));
    const tx=b.x-a.x, ty=b.y-a.y, tl=Math.hypot(tx,ty)||1;
    const off=16, lx=mid.x+(-ty/tl*off), ly=mid.y+(tx/tl*off);
    const w=s.n.length*6.4+14;
    edges.appendChild(el("rect",{x:lx-w/2,y:ly-11,width:w,height:18,rx:4,
      fill:"#0e0e11",stroke:"#2c2c35","stroke-width":1}));
    const lab=el("text",{x:lx,y:ly+2,"text-anchor":"middle",class:"elab"});
    lab.textContent=s.n; edges.appendChild(lab);

    const dot=document.getElementById("dot");
    dot.setAttribute("opacity",1);
    const t0=performance.now(), dur=900;
    (function anim(now){
      const k=Math.min(1,(now-t0)/dur);
      const pt=p.getPointAtLength(total*k);
      dot.setAttribute("cx",pt.x); dot.setAttribute("cy",pt.y);
      if(k<1) requestAnimationFrame(anim);
    })(t0);
  }

  document.querySelectorAll(".step").forEach(n=>{
    const i=+n.dataset.i;
    n.classList.toggle("on", i===idx);
    n.classList.toggle("off", !vis(STEPS[i]));
  });
  showDetail(idx>=0 ? STEPS[idx] : null);
  document.getElementById("cap").textContent =
    idx<0 ? act.length+" steps" : "Step "+(act.indexOf(idx)+1)+" of "+act.length;
}

function buildList(){
  const box=document.getElementById("steps"); box.innerHTML="";
  STEPS.forEach((s,i)=>{
    const d=document.createElement("div");
    d.className="step"; d.dataset.i=i;
    d.innerHTML='<span class="num">'+(i+1)+'</span><span class="txt">'+
      '<span class="nm">'+s.n+'</span><span class="nt">'+s.d+'</span></span>'+
      (s.b!=="both"?'<span class="tag '+s.b+'">'+s.b+'</span>':'');
    d.onclick=()=>{ if(vis(s)){ idx=i; stop(); render(); } };
    box.appendChild(d);
  });
}

function go(dir){
  const act=active(); if(!act.length) return;
  let p=act.indexOf(idx);
  p = p<0 ? (dir>0?0:act.length-1) : p+dir;
  if(p>=act.length){ stop(); idx=act[act.length-1]; render(); return; }
  if(p<0) p=0;
  idx=act[p]; render();
  document.querySelector('.step[data-i="'+idx+'"]')
    ?.scrollIntoView({block:"nearest",behavior:"smooth"});
}
function stop(){ playing=false; clearInterval(timer);
  document.getElementById("play").textContent="Play"; }
function play(){
  if(playing){ stop(); return; }
  playing=true; document.getElementById("play").textContent="Pause";
  const act=active();
  if(idx===act[act.length-1]) idx=-1;
  go(1);
  timer=setInterval(()=>{ const a=active();
    if(idx===a[a.length-1]){ stop(); return; } go(1); },1600);
}
function setMode(m){
  mode=m; stop(); idx=-1;
  document.getElementById("mBM").classList.toggle("sel",m==="BM");
  document.getElementById("mVM").classList.toggle("sel",m==="VM");
  render();
}

document.getElementById("play").onclick=play;
document.getElementById("next").onclick=()=>{stop();go(1)};
document.getElementById("prev").onclick=()=>{stop();go(-1)};
document.getElementById("rst").onclick=()=>{stop();idx=-1;render()};
if(HAS_MODES){
  document.getElementById("modes").style.display="";
  document.getElementById("mBM").onclick=()=>setMode("BM");
  document.getElementById("mVM").onclick=()=>setMode("VM");
}
document.addEventListener("keydown",e=>{
  if(e.key==="ArrowRight"){stop();go(1)} if(e.key==="ArrowLeft"){stop();go(-1)}
  if(e.key===" "){e.preventDefault();play()}
});

// Build FIRST. setMode() calls render(), which needs the SVG and the step list
// to exist — doing this earlier throws and aborts the rest of the script.
drawStatic(); buildList();

// Deep link: #step=7, and #vm to open on the virtual branch. Mode before step,
// because switching mode resets the position.
(function(){
  const h=location.hash.slice(1);
  if(HAS_MODES && /vm/i.test(h)) setMode("VM");
  const m=h.match(/step=(\d+)/);
  if(m){ const i=+m[1]-1; if(STEPS[i] && vis(STEPS[i])) idx=i; }
})();

render();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    for name, cfg in WORKFLOWS.items():
        html = page(cfg["title"], cfg["subtitle"], cfg["nodes"],
                    cfg["group"], cfg["steps"])
        (HERE / f"{name}.html").write_text(html, encoding="utf-8")
        print(f"wrote {name}.html")
    print(f"\n{len(WORKFLOWS)} animations generated.")
