# Procedure: Create Customer MCE

**Type:** Infrastructure Setup

## Purpose

The Customer MCE is a standalone OCP cluster installed via UPI on KubeVirt VMs. It has 3 master VMs (one per zone, AD19) and worker VMs distributed across zones (AD20), all of type 32/128. It runs MCE and HyperShift operators and maintains the agent inventory that tenant NodePools draw from. There is one Customer MCE per OCP version band in service.

## Pre-requisites

- KV Clusters running in all 3 zones (Procedure 02)
- Server Scanner shows at least 1 VM of type 32/128 `Available` per zone for masters
- Server Scanner shows sufficient 32/128 VMs `Available` across zones for workers
- OCP installer binary (target version)
- OCP pull secret
- SSH public key
- DNS entries configured for API endpoint, Ingress, and etcd
- Load balancer configured for API (port 6443) and Ingress (ports 80/443)

## Procedure

1. Query Server Scanner for available 32/128 VMs per zone
   ```
   curl -s <server-scanner-url>/api/vms?type=32-128&zone=zone-a&status=available
   curl -s <server-scanner-url>/api/vms?type=32-128&zone=zone-b&status=available
   curl -s <server-scanner-url>/api/vms?type=32-128&zone=zone-c&status=available
   ```
   Select 1 VM per zone as master nodes (3 total) and the required number of worker VMs per zone.

2. Mark selected VMs as reserved in Server Scanner
   ```
   curl -X PATCH <server-scanner-url>/api/vms/<vm-id> \
     -d '{"status": "reserved", "cluster": "customer-mce"}'
   ```
   Repeat for each selected VM.

3. Prepare `install-config.yaml`
   - Platform: `none` (UPI)
   - Control plane replicas: 3
   - Worker replicas: `<worker-count>`
   - Configure networking (machine CIDR, cluster network, service network)
   - Set pull secret and SSH key

4. Generate ignition configs
   ```
   openshift-install create ignition-configs --dir=<install-dir>
   ```

5. Apply master ignition config to each of the 3 master VMs (one per zone) and boot them

6. Boot bootstrap node

7. Monitor bootstrap progress
   ```
   openshift-install wait-for bootstrap-complete --dir=<install-dir>
   ```

8. Remove bootstrap node from load balancer once bootstrap completes

9. Apply worker ignition config to worker VMs and boot them

10. Approve pending CSRs
    ```
    oc get csr -o name | xargs oc adm certificate approve
    ```

11. Wait for cluster install to complete
    ```
    openshift-install wait-for install-complete --dir=<install-dir>
    ```

12. Install MCE operator on the Customer MCE cluster
    - Create Namespace `multicluster-engine`
    - Create OperatorGroup
    - Create Subscription for `multicluster-engine` channel `stable-2.x`
    - Wait for CSV to reach `Succeeded`

13. Create MCE instance with HyperShift enabled
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

14. Wait for MCE to be Available
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
NAME        STATUS   ROLES                  AGE
master-a    Ready    control-plane,master   ...
master-b    Ready    control-plane,master   ...
master-c    Ready    control-plane,master   ...
worker-...  Ready    worker                 ...
```

**All cluster operators available**
```
oc get co
```
Expected: every operator shows `True   False   False` under `AVAILABLE PROGRESSING DEGRADED`

**MCE available**
```
oc get mce multiclusterengine \
  -o jsonpath='{.status.conditions[?(@.type=="Available")].status}'
```
```
True
```

**HyperShift operator running**
```
oc get pods -n hypershift
```
Expected: all pods in `Running` state
