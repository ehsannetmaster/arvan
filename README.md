# Arvan — Kubernetes Lab on Proxmox

A self-hosted **3-node Kubernetes cluster** running on Proxmox VMs, provisioned
from scratch and managed with GitOps. This README is the big picture; each
subsystem keeps its own detailed README in its directory.

The journey, end to end:

```
Proxmox template ──► Terraform VMs ──► Kubespray K8s ──► Argo CD (GitOps) ──► Workloads
   (terraform/)        (terraform/)     (kubespray/)     (bootstrap/, apps/)   (monitoring/, postgres/, ip-country-api/)
```

---

## Steps

### 1. Create a Proxmox VM template
Build a reusable Ubuntu 24.04 cloud-init template on the Proxmox host — the
golden image every node is cloned from.
→ [terraform/README.md](terraform/README.md) (Part 1)

### 2. Deploy the VMs with Terraform
Clone that template into three VMs with static IPs. These become the cluster
nodes (`kuber1`–`kuber3`, `100.64.231.101`–`.103`).
→ [terraform/README.md](terraform/README.md) (Part 2)

### 3. Install Kubernetes with Kubespray
Run Kubespray against the three VMs to bring up an HA Kubernetes cluster
(stacked control-plane + etcd on every node). Argo CD is installed as part of
this step.
→ [kubespray/README.md](kubespray/README.md)

### 4. Bootstrap Argo CD (GitOps)
Everything on the cluster is described in this repo and applied by Argo CD's
app-of-apps. One root Application creates the rest; the Argo CD UI is exposed
through an external nginx proxy.
→ [bootstrap/](bootstrap/) · [apps/](apps/)

### 5. Deploy the workloads
Argo CD rolls out the actual services:

- **Monitoring** — kube-prometheus-stack (Prometheus, Grafana, Alertmanager).
  → [monitoring/README.md](monitoring/README.md)
- **PostgreSQL HA** — Bitnami `postgresql-ha` (repmgr + Pgpool-II).
  → [postgres/README.md](postgres/README.md)
- **ip-country-api** — a small FastAPI demo service, built and pushed by CI,
  then synced by Argo CD.
  → [ip-country-api/README.md](ip-country-api/README.md)

---

## Repo map

| Directory | What it holds |
|-----------|---------------|
| [terraform/](terraform/) | Proxmox template guide + Terraform VM provisioning |
| [kubespray/](kubespray/) | Kubespray inventory & group vars for the K8s install |
| [bootstrap/](bootstrap/) | Argo CD app-of-apps root + UI ingress notes |
| [apps/](apps/) | Argo CD `Application` definitions (one per workload) |
| [monitoring/](monitoring/) | kube-prometheus-stack values, storage, dashboards |
| [postgres/](postgres/) | PostgreSQL HA chart values, storage, alert rules |
| [ip-country-api/](ip-country-api/) | The demo app source, Dockerfile, and k8s manifests |
| [.github/workflows/](.github/workflows/) | CI: build/push the app image and bump its tag |

---

## Conventions

- **GitOps:** the repo is the source of truth; Argo CD applies it. Sync is
  **manual** — apps show *OutOfSync* until you sync them.
- **TLS:** terminated at an external nginx proxy; in-cluster traffic is plain
  HTTP. Services are reached at `*.idistance.ir`.
- **`.gitignore`:** denies everything at the repo root and re-includes only the
  GitOps directories, so loose root files won't be committed by accident.
