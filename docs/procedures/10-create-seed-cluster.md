# Procedure: Create Seed Cluster

**Type:** Infrastructure Setup

## Purpose

The Seed Cluster is the per-zone infrastructure management plane. It hosts the control planes of all KV clusters in the zone as HyperShift hosted clusters and provides MCE and HyperShift capabilities. This flow must be completed once per availability zone before any KV cluster can be created in that zone.

## Pre-requisites

- Bare-metal nodes racked and powered within the target zone's rack boundaries
- DNS entries configured for API endpoint, Ingress, and etcd
- NTP synchronized across all nodes
- OCP pull secret obtained from cloud.redhat.com
- OCP installer binary downloaded (target OCP release version)
- SSH public key available for node access
- Load balancer configured for API (port 6443) and Ingress (ports 80/443)

## Procedure

1. Prepare `install-config.yaml` for the target zone
   - Platform: `baremetal` (UPI)
   - Control plane replicas: 3
   - Worker replicas: 5
   - Configure zone-specific networking (machine CIDR, cluster network, service network)
   - Set pull secret and SSH key

2. Generate ignition configs
   ```
   openshift-install create ignition-configs --dir=<install-dir>
   ```

3. Boot control plane nodes with master ignition config

4. Boot bootstrap node

5. Monitor bootstrap progress
   ```
   openshift-install wait-for bootstrap-complete --dir=<install-dir>
   ```

6. Remove bootstrap node from load balancer once bootstrap completes

7. Boot worker nodes with worker ignition config

8. Approve pending CSRs for worker nodes
   ```
   oc get csr -o name | xargs oc adm certificate approve
   ```

9. Wait for cluster install to complete
   ```
   openshift-install wait-for install-complete --dir=<install-dir>
   ```

10. Install MCE operator
    - Create namespace `multicluster-engine`
    - Create OperatorGroup
    - Create Subscription for `multicluster-engine` channel `stable-2.x`
    - Wait for CSV to reach `Succeeded`

11. Create MCE instance with HyperShift enabled
    ```yaml
    apiVersion: multicluster.openshift.io/v1
    kind: MultiClusterEngine
    metadata:
      name: multiclusterengine
    spec:
      overrides:
        components:
        - name: hypershift
          enabled: true
        - name: hypershift-local-hosting
          enabled: true
    ```

12. Wait for MCE to be Available
    ```
    oc wait multiclusterengine/multiclusterengine \
      --for=condition=Available --timeout=600s
    ```

## Acceptance Criteria

**All nodes Ready**
```
oc get nodes
```
```
NAME       STATUS   ROLES                  AGE
master-0   Ready    control-plane,master   ...
master-1   Ready    control-plane,master   ...
master-2   Ready    control-plane,master   ...
worker-0   Ready    worker                 ...
worker-1   Ready    worker                 ...
worker-2   Ready    worker                 ...
worker-3   Ready    worker                 ...
worker-4   Ready    worker                 ...
```

**All cluster operators available**
```
oc get co
```
Expected: every operator shows `True   False   False` under `AVAILABLE PROGRESSING DEGRADED`

**MCE available**
```
oc get mce multiclusterengine -o jsonpath='{.status.conditions[?(@.type=="Available")].status}'
```
```
True
```

**HyperShift operator running**
```
oc get pods -n hypershift
```
Expected: all pods in `Running` state with `1/1` or `2/2` ready

**API endpoint reachable**
```
curl -k https://<api-endpoint>:6443/healthz
```
```
ok
```
