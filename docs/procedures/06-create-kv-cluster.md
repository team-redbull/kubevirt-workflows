# Procedure: Create KV Cluster

**Type:** Infrastructure Setup

## Purpose

A KV Cluster is a HyperShift hosted cluster whose control plane runs as pods on the Seed Cluster and whose BM worker nodes run OpenShift Virtualization (KubeVirt). It is the physical compute layer where tenant workload VMs run. One or more KV clusters exist per zone; a new one is created when the existing cluster approaches its VM capacity threshold.

## Pre-requisites

- Seed Cluster for the target zone is running and healthy (Procedure 01)
- BM worker nodes racked, powered, and network-reachable within the zone
- OCP release image available for the target KV cluster version
- Pull secret available
- SSH public key available
- NetApp Trident configured and accessible from the BM worker nodes (required for KubeVirt live migration)
- Sufficient CPU and memory on the Seed Cluster workers to host the KV cluster control plane pods

## Procedure

1. Create a namespace on the Seed Cluster for the KV cluster resources
   ```
   oc create namespace kv-<zone>-<cluster-id>
   ```

2. Create pull secret in the namespace
   ```
   oc create secret generic pull-secret \
     --from-file=.dockerconfigjson=<pull-secret.json> \
     --type=kubernetes.io/dockerconfigjson \
     -n kv-<zone>-<cluster-id>
   ```

3. Create SSH key secret
   ```
   oc create secret generic ssh-key \
     --from-file=id_rsa.pub=<path-to-public-key> \
     -n kv-<zone>-<cluster-id>
   ```

4. Create the HostedCluster resource on the Seed Cluster
   ```yaml
   apiVersion: hypershift.openshift.io/v1beta1
   kind: HostedCluster
   metadata:
     name: kv-<zone>-<cluster-id>
     namespace: kv-<zone>-<cluster-id>
   spec:
     release:
       image: <ocp-release-image>
     platform:
       type: Agent
       agent:
         agentNamespace: kv-<zone>-<cluster-id>
     pullSecret:
       name: pull-secret
     sshKey:
       name: ssh-key
     networking:
       clusterNetwork:
       - cidr: 10.132.0.0/14
       serviceNetwork:
       - cidr: 172.31.0.0/16
       networkType: OVNKubernetes
   ```

5. Create a shared InfraEnv for BM workers in the zone (skip if `bm-<zone>` InfraEnv already exists from a previous KV cluster in this zone)
   ```yaml
   apiVersion: agent-install.openshift.io/v1beta1
   kind: InfraEnv
   metadata:
     name: bm-<zone>
     namespace: kv-<zone>-<cluster-id>
   spec:
     pullSecretRef:
       name: pull-secret
     sshAuthorizedKey: <ssh-public-key>
   ```
   > The InfraEnv is scoped to `bm-<zone>` and shared across all KV clusters in the same zone. Multiple KV cluster NodePools draw BM workers from the same zone pool.

6. Boot BM worker nodes using the generated discovery ISO
   ```
   oc get infraenv bm-<zone> \
     -n kv-<zone>-<cluster-id> \
     -o jsonpath='{.status.isoDownloadURL}'
   ```

7. Wait for BM nodes to register as agents
   ```
   oc get agent -n kv-<zone>-<cluster-id> --watch
   ```
   Expected: agents appear with `APPROVED=true` and `READY=true`

8. Create NodePool to claim BM agents as KV cluster workers
   ```yaml
   apiVersion: hypershift.openshift.io/v1beta1
   kind: NodePool
   metadata:
     name: kv-<zone>-<cluster-id>-workers
     namespace: kv-<zone>-<cluster-id>
   spec:
     clusterName: kv-<zone>-<cluster-id>
     replicas: <worker-count>
     platform:
       type: Agent
       agent:
         agentLabelSelector:
           matchLabels:
             zone: <zone>
     release:
       image: <ocp-release-image>
     management:
       upgradeType: InPlace
   ```

9. Wait for the KV cluster control plane to become available
   ```
   oc wait hostedcluster kv-<zone>-<cluster-id> \
     -n kv-<zone>-<cluster-id> \
     --for=condition=Available --timeout=900s
   ```

10. Wait for all worker nodes to be Ready
    ```
    oc get nodepool kv-<zone>-<cluster-id>-workers \
      -n kv-<zone>-<cluster-id> --watch
    ```
    Expected: `CURRENT NODES` equals `DESIRED NODES`

11. Install OpenShift Virtualization operator on the KV cluster
    - Create Namespace `openshift-cnv`
    - Create OperatorGroup
    - Create Subscription for `kubevirt-hyperconverged` operator
    - Wait for CSV to reach `Succeeded`

12. Create HyperConverged instance
    ```yaml
    apiVersion: hco.kubevirt.io/v1beta1
    kind: HyperConverged
    metadata:
      name: kubevirt-hyperconverged
      namespace: openshift-cnv
    spec: {}
    ```

13. Install and configure NetApp Trident on the KV cluster for shared storage

## Acceptance Criteria

**KV cluster control plane available**
```
oc get hostedcluster kv-<zone>-<cluster-id> -n kv-<zone>-<cluster-id>
```
```
NAME                    VERSION   KUBECONFIG   PROGRESS   AVAILABLE   PROGRESSING
kv-<zone>-<cluster-id>  4.x.x     ...          Complete   True        False
```

**All BM worker nodes Ready**
```
oc get nodes --kubeconfig=<kv-cluster-kubeconfig>
```
Expected: all worker nodes show `Ready` status

**OpenShift Virtualization available**
```
oc get hco kubevirt-hyperconverged -n openshift-cnv \
  --kubeconfig=<kv-cluster-kubeconfig> \
  -o jsonpath='{.status.conditions[?(@.type=="Available")].status}'
```
```
True
```

**KubeVirt operational**
```
oc get pods -n openshift-cnv --kubeconfig=<kv-cluster-kubeconfig>
```
Expected: all pods in `Running` state
