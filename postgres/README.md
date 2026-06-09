# PostgreSQL HA — Argo CD deployment

A highly-available PostgreSQL deployed via the **Bitnami `postgresql-ha`** chart
(PostgreSQL 17.6 + repmgr + Pgpool-II) through this repo's app-of-apps.

| Piece | File |
|-------|------|
| Argo CD Application | [apps/postgresql-ha.yaml](../apps/postgresql-ha.yaml) |
| Chart values | [postgres/values.yaml](values.yaml) |
| Static local PVs | [postgres/local-storage.yaml](local-storage.yaml) |

## Topology

- All **3 nodes are control-plane + worker** (schedulable; control-plane taint
  removed).
- **3 PostgreSQL replicas** (1 primary + 2 standbys), one per node (node1/2/3),
  forced apart by **hard pod anti-affinity**. repmgr auto-promotes a standby if
  the primary dies → survives **one node failure**, with a real **3-node quorum**
  (no 2-node split-brain risk).
- **2 Pgpool-II replicas** (stateless) — the proxy that routes writes→primary,
  reads→replicas, and re-routes after a failover. Reach the DB via the Pgpool
  Service `postgresql-ha-pgpool.arvan.svc:5432`.

> ℹ️ **Running a DB on control-plane nodes** is fine for a lab, but in production
> you'd avoid co-locating Postgres with etcd/apiserver to prevent resource
> contention. Give the nodes enough CPU/RAM headroom.

> ⚠️ **Maintenance caveat.** With hard anti-affinity + 3 nodes, draining one node
> leaves that replica's pod unschedulable until the node returns (no spare node).

## Bitnami 2025 changes — chart AND images

On **2025-08-28** Bitnami stopped publishing to Docker Hub the usual way. Two
separate fallouts, both handled here:

1. **Chart distribution → OCI only.** The old HTTP repo
   (`https://charts.bitnami.com/bitnami`) no longer serves tarballs; its
   `index.yaml` now points at OCI refs, so `helm pull --repo …` fails with
   `invalid_reference: invalid tag`. The app pulls the chart from the **OCI
   registry** `registry-1.docker.io/bitnamicharts` instead (see step 3 below).
2. **Container images → archived.** The newest `docker.io/bitnami/*` tags now
   404 (`ImagePullBackOff`). All four images in [values.yaml](values.yaml) are
   repointed to the frozen `bitnamilegacy/*` archive at the last pre-cutoff tags
   (verified present). They get **no further updates** — mirror them to your own
   registry if this cluster is long-lived.

## Prerequisites — do these BEFORE syncing

### 1. Create the local PV directories on each worker

No provisioner will `mkdir` these for you:

```bash
ssh node1 'sudo mkdir -p /mnt/local-storage/postgres && sudo chmod 0777 /mnt/local-storage/postgres'
ssh node2 'sudo mkdir -p /mnt/local-storage/postgres && sudo chmod 0777 /mnt/local-storage/postgres'
ssh node3 'sudo mkdir -p /mnt/local-storage/postgres && sudo chmod 0777 /mnt/local-storage/postgres'
```

### 2. Create the credential Secrets out of band

Never commit passwords to this **public** repo (same pattern as `grafana-admin`).
Both Secrets live in the `arvan` namespace.

```bash
kubectl create namespace arvan   # or rely on CreateNamespace=true, then create secrets after

# PostgreSQL data tier: superuser + app-user + repmgr passwords
kubectl -n arvan create secret generic postgresql-ha-credentials \
  --from-literal=postgres-password='<superuser-password>' \
  --from-literal=password='<appuser-password>' \
  --from-literal=repmgr-password='<repmgr-password>'

# Pgpool admin (PCP) password
kubectl -n arvan create secret generic pgpool-credentials \
  --from-literal=admin-password='<pgpool-admin-password>'
```

The key names above are exactly what the chart reads (`postgresql.existingSecret`
expects `postgres-password` / `password` / `repmgr-password`;
`pgpool.existingSecret` expects `admin-password`). If a pod logs a missing-key
error, recheck these names.

### 3. Register the Bitnami OCI registry in Argo CD

The chart is pulled via OCI (see "Bitnami 2025 changes" above), so Argo CD must
have the registry registered with `enableOCI=true`. This is cluster config, so
apply it out of band (it must exist before the app can pull the chart):

```bash
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: Secret
metadata:
  name: bitnamicharts-oci
  namespace: argocd
  labels:
    argocd.argoproj.io/secret-type: repository
stringData:
  name: bitnamicharts
  type: helm
  url: registry-1.docker.io/bitnamicharts
  enableOCI: "true"
EOF
```

No credentials needed — these charts are public (anonymous pull). If you hit
Docker Hub rate limits, add `username`/`password` keys with a Docker Hub token.

## Deploy

```bash
# Register the app via the root app-of-apps, then sync it:
argocd app sync root
argocd app sync postgresql-ha
# (or use the Argo CD UI Sync buttons — sync is manual throughout this repo)
```

## Verify

```bash
kubectl -n arvan get pods -o wide          # 3 postgresql + 2 pgpool, spread across node1/2/3
kubectl -n arvan get pvc,pv                 # 3 PVCs Bound to postgres-local-pv-node1/2/3

# Who is primary? (repmgr cluster view)
kubectl -n arvan exec -it postgresql-ha-postgresql-0 -- \
  repmgr -f /opt/bitnami/repmgr/conf/repmgr.conf cluster show

# Connect through Pgpool from inside the cluster:
kubectl -n arvan run psql --rm -it --restart=Never \
  --image=docker.io/bitnamilegacy/postgresql-repmgr:17.6.0-debian-12-r2 \
  --env=PGPASSWORD='<appuser-password>' -- \
  psql -h postgresql-ha-pgpool -U appuser -d appdb -c '\conninfo'
```

## Teardown note

`local` PVs are `Retain`. After deleting the app, clear the data dirs before a
fresh install or the PVs stay `Released` and won't rebind:

```bash
ssh node1 'sudo rm -rf /mnt/local-storage/postgres/*'
ssh node2 'sudo rm -rf /mnt/local-storage/postgres/*'
ssh node3 'sudo rm -rf /mnt/local-storage/postgres/*'
```
