# Kubespray — 3-Node Cluster Setup

Deployment notes for a **3-node Kubernetes cluster** built with [Kubespray](https://github.com/kubernetes-sigs/kubespray) `v2.30.0`.

> **Environment:** testing / lab — **HA control plane** (3 stacked control-plane + etcd nodes), and every node also runs workloads.

---

## Topology

| Node  | Address           | Roles                                  |
|-------|-------------------|----------------------------------------|
| node1 | `100.64.231.101`  | control plane + etcd + worker          |
| node2 | `100.64.231.102`  | control plane + etcd + worker          |
| node3 | `100.64.231.103`  | control plane + etcd + worker          |

- **All 3 nodes are control plane + etcd + worker** (stacked etcd, 3 members → odd quorum for HA).
- This is **HA**: the cluster survives losing any single node. Kubespray runs a localhost API load balancer on each node (`loadbalancer_apiserver_localhost`), so in-cluster traffic balances across all three apiservers.
- The control-plane `NoSchedule` taint is **removed** so workloads schedule on all nodes (see §3).
- Nodes have a **single network interface**, so the `ip=` inventory variable is left unset (kubespray auto-detects the default-route interface).

---

## 1. Install prerequisites & Kubespray

```bash
# System dependency for Python virtualenvs
apt install python3.12-venv

# Clone Kubespray
git clone https://github.com/kubernetes-sigs/kubespray.git

VENVDIR=kubespray-venv
KUBESPRAYDIR=kubespray

# Create & activate a virtualenv
python3 -m venv $VENVDIR
source $VENVDIR/bin/activate

cd $KUBESPRAYDIR

# IMPORTANT: check out the release tag BEFORE installing requirements.
# requirements.txt differs per branch — the default branch pins a newer
# ansible (core 2.18.x) that the v2.30.0 playbooks reject. Installing before
# checkout gives you the wrong ansible version.
git checkout v2.30.0

# Now install Python requirements from the v2.30.0 tag (pins ansible==10.7.0 / core 2.17.x)
pip install -r requirements.txt

# Create an inventory from the sample
cp -rfp inventory/sample inventory/mycluster
```

> **If you already installed requirements on the wrong branch** (the cause of the
> `Ansible must be between 2.17.3 and 2.18.0` error), fix it after checking out the tag:
> ```bash
> pip uninstall -y ansible ansible-core
> pip install -r requirements.txt
> ansible --version   # expect: ansible [core 2.17.x]
> ```

---

## 2. Inventory — `inventory/mycluster/inventory.ini`

> **Why this layout (all-in-one):** every node is control-plane + etcd + worker.
> Three stacked control-plane/etcd members give an **HA** control plane (odd
> quorum), and with the control-plane taint removed (§3) the same nodes also run
> workloads — maximizing usable capacity on a small cluster.

```ini
# 3-node cluster: every node is control plane + etcd + worker (HA, stacked etcd).

# 'ip=' is left unset: nodes have a single interface, so kubespray
# auto-detects the default-route interface.

[kube_control_plane]
node1 ansible_host=100.64.231.101
node2 ansible_host=100.64.231.102
node3 ansible_host=100.64.231.103

[etcd:children]
kube_control_plane

[kube_node]
node1 ansible_host=100.64.231.101
node2 ansible_host=100.64.231.102
node3 ansible_host=100.64.231.103

[all:vars]
ansible_user=arvan
ansible_become=true
ansible_become_method=sudo
```

> **etcd is on all three control-plane nodes** via `[etcd:children]
> kube_control_plane` — 3 members, an odd count for quorum. Keep etcd at an odd
> number (1/3/5).

> **`ansible_become=true` is required** — Kubespray must run as root. `ansible_become_method=sudo` only specifies *how* to escalate; `ansible_become=true` is what actually enables it. The `arvan` user needs passwordless sudo on all nodes (or run the playbook with `-K` to be prompted).

---

## 3. Cluster options — `inventory/mycluster/group_vars/k8s_cluster/k8s-cluster.yml`

```yaml
# Automatically renew control-plane certificates before expiry
auto_renew_certificates: true
# First Monday of each month at 03:00
auto_renew_certificates_systemd_calendar: "Mon *-*-1,2,3,4,5,6,7 03:00:00"

# Expose kube-proxy metrics on all interfaces so Prometheus can scrape :10249
# (default is 127.0.0.1:10249, which is unreachable from the monitoring pod).
# Set BEFORE first deploy — applied by kubeadm at init.
kube_proxy_metrics_bind_address: "0.0.0.0:10249"

# Make the control-plane nodes schedulable (run workloads on all 3 nodes) by
# removing the default control-plane NoSchedule taint.
# NOTE: confirm the exact var name for v2.30.0:
#   grep -rn "inventory_node_taints\|control-plane:NoSchedule" roles/
kube_control_plane_inventory_node_taints: []
```

- **CNI:** left at the default — **Calico** (`kube_network_plugin` unchanged).
- **kube-proxy:** default mode (iptables), kube-proxy deployed normally.
- **Control-plane taint removed** so all 3 nodes run workloads (set above). If a
  node still shows the `node-role.kubernetes.io/control-plane:NoSchedule` taint,
  clear it manually: `kubectl taint nodes --all node-role.kubernetes.io/control-plane:NoSchedule-`.

---

## 4. Global vars — `inventory/mycluster/group_vars/all/all.yml`

```yaml
upstream_dns_servers:
  - 8.8.8.8
  - 1.1.1.1
```

---

## 5. Addons — `inventory/mycluster/group_vars/k8s_cluster/addons.yml`

```yaml
# Gateway API — disabled (using classic Ingress instead)
gateway_api_enabled: false

# Helm
helm_enabled: true

# Metrics Server (kubectl top, HPA)
metrics_server_enabled: true

# Argo CD (GitOps)
argocd_enabled: true
argocd_namespace: argocd

# NGINX Ingress Controller
ingress_nginx_enabled: true
ingress_nginx_host_network: true
```

### Notes on the Ingress choice

- **Gateway API was disabled** in favour of classic **Ingress**. On the default Calico CNI, kubespray does not deploy a Gateway *controller* (only CRDs), so Gateway API would have needed a controller installed manually. Ingress is simpler for this cluster.
- **`ingress_nginx_host_network: true`** makes the ingress-nginx controller bind **ports 80/443 directly on the node IPs** (hostPort), instead of via a `LoadBalancer` service. Kubespray sets `dnsPolicy: ClusterFirstWithHostNet` automatically.
- **No MetalLB needed** — host-network ingress doesn't use `LoadBalancer`-type services. (Add MetalLB only if other workloads need external IPs.)
- **All three nodes are workers**, so host-network ingress-nginx runs on every node and listens on `:80`/`:443` on each node IP. No node-selector pinning is needed — there are no control-plane-only nodes to exclude.
- **Client access:** point external DNS / an upstream L4 LB at **all three** node IPs (`100.64.231.101`, `100.64.231.102`, `100.64.231.103`) on 80/443. Round-robin or front them with your own proxy for redundancy.

---

## 6. Container registry mirror — bypass `registry.k8s.io` geo-block

`registry.k8s.io` geo-blocks some regions (sanctions) and returns **`403 Forbidden`**
on image pulls, e.g.:

```
https://registry.k8s.io/v2/pause/manifests/3.10.1: 403 Forbidden
```

Fix: tell containerd to transparently pull `registry.k8s.io` images from the
**DaoCloud** mirror (works from the blocked region; image names are left unchanged).
Add to `inventory/mycluster/group_vars/all/containerd.yml`:

```yaml
containerd_registries_mirrors:
  - prefix: registry.k8s.io
    mirrors:
      - host: https://k8s.m.daocloud.io
        capabilities: ["pull", "resolve"]
        skip_verify: false
```

This is the approach that was used and confirmed working.

**Notes:**
- DaoCloud mirrors the other upstreams too if you hit more blocks — add extra
  `prefix`/`mirrors` entries for `gcr.io` → `gcr.m.daocloud.io`,
  `ghcr.io` → `ghcr.m.daocloud.io`, `quay.io` → `quay.m.daocloud.io`,
  `docker.io` → `docker.m.daocloud.io`.
- Quick test on a node: `crictl pull k8s.m.daocloud.io/pause:3.10.1`.
- If **binary** downloads (`dl.k8s.io`, GitHub) also get blocked, set `files_repo`
  in `group_vars/all/offline.yml` as well.

---

## 7. Pre-flight checklist

Before running the playbook, confirm on all 3 nodes:

- [ ] SSH key auth works from the Ansible host as `arvan`.
- [ ] `arvan` has passwordless sudo (or plan to use `-K`).
- [ ] Unique hostnames; time synced (chrony/NTP).
- [ ] Outbound internet (or a configured registry mirror) for image pulls.
- [ ] `ansible_become=true` present in inventory.

Connectivity test:

```bash
ansible all -i inventory/mycluster/inventory.ini -m ping -b
```

---

## 8. Deploy

```bash
ansible-playbook -i inventory/mycluster/inventory.ini --become cluster.yml
```

---

## Summary of decisions

| Area            | Decision                                                        |
|-----------------|-----------------------------------------------------------------|
| Topology        | 3× control plane + etcd + worker (HA, taint removed)            |
| Kubespray       | `v2.30.0`                                                       |
| Privilege esc.  | `ansible_become=true` + `sudo`                                  |
| CNI             | Calico (default)                                                |
| DNS upstream    | `8.8.8.8`, `1.1.1.1`                                            |
| Cert renewal    | Auto, first Monday monthly at 03:00                            |
| Gateway API     | Disabled                                                        |
| Ingress         | ingress-nginx, host network (binds 80/443 on worker node IPs)  |
| LoadBalancer    | None (no MetalLB)                                               |
| Addons          | Helm, Metrics Server, Argo CD                                  |
