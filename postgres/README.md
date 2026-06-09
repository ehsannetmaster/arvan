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

### 1. Create the local PV directories on each node

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

# Pgpool: admin (PCP) password AND the streaming-replication check password.
# sr-check-password is REQUIRED — the postgresql pods read it from THIS secret
# too (POSTGRESQL_SR_CHECK_PASSWORD); omitting it makes Postgres refuse to start.
kubectl -n arvan create secret generic pgpool-credentials \
  --from-literal=admin-password='<pgpool-admin-password>' \
  --from-literal=sr-check-password='<sr-check-password>'
```

The key names above are exactly what the chart reads:
- `postgresql.existingSecret` → `postgres-password`, `password`, `repmgr-password`
- `pgpool.existingSecret` → `admin-password`, `sr-check-password`

If a pod logs a missing-key error (or `POSTGRESQL_SR_CHECK_PASSWORD ... empty`),
recheck these names.

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

> ⚠️ **Editing `apps/postgresql-ha.yaml` does nothing until `root` is synced.**
> The root app-of-apps owns the live `postgresql-ha` Application; a change to the
> Application manifest (e.g. the chart `repoURL`) only reaches the cluster when
> you sync `root`. And because Argo CD caches the Git revision (~3 min poll), a
> `root` that already shows *Synced/Healthy* may be sitting on an old commit —
> hard-refresh it so it sees your latest push first:
> ```bash
> argocd app get root --hard-refresh && argocd app sync root
> argocd app get postgresql-ha -o yaml | grep repoURL   # confirm the live spec updated
> ```

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

## Grafana dashboard

Metrics come from the chart's **postgres_exporter** (`metrics.enabled` +
`metrics.serviceMonitor.enabled` in [values.yaml](values.yaml)); Prometheus
scrapes the ServiceMonitor cluster-wide.

The dashboard is shipped as a ConfigMap —
[monitoring/postgres-dashboard.yaml](../monitoring/postgres-dashboard.yaml) —
labeled `grafana_dashboard: "1"`, which the kube-prometheus-stack Grafana
**sidecar** auto-imports from the `monitoring` namespace. The JSON is embedded
(no grafana.com fetch at runtime — this cluster is geo-blocked) and uses a
`$datasource` variable that binds to the default Prometheus automatically.

It is rendered by the **kube-prometheus-stack** Application (not postgresql-ha),
so deploying it means syncing *that* app:

```bash
# The include-glob change in apps/kube-prometheus-stack.yaml only reaches the
# live app after root is synced (see the Deploy warning), then sync the stack:
argocd app get root --hard-refresh && argocd app sync root
argocd app sync kube-prometheus-stack
```

Then in Grafana: **Dashboards → "PostgreSQL HA (postgres_exporter)"** (appears
within ~1 min of the ConfigMap landing). Verify the data path first if panels
are empty:

```bash
kubectl -n arvan get servicemonitor                       # postgresql-ha metrics SM exists
# In Prometheus UI (prometheus.idistance.ir) run:  pg_up   -> should return 1 per instance
```

> The "Replication lag (s)" panel uses `pg_replication_lag`; some exporter
> versions name it `pg_replication_lag_seconds`. If that panel is empty, edit
> the query — the rest of the dashboard uses rock-stable `pg_stat_database_*`.

## Alerting

Alert rules ship as a `PrometheusRule` —
[postgres/prometheus-rules.yaml](prometheus-rules.yaml) — rendered by the
postgresql-ha Application into the `arvan` namespace. Because
`ruleSelectorNilUsesHelmValues: false` is set in
[monitoring/values.yaml](../monitoring/values.yaml), Prometheus loads **every**
`PrometheusRule` in **all** namespaces automatically — no special label or
namespace placement needed (unlike the Grafana dashboards, which must sit in
`monitoring` for the sidecar).

| Alert | Fires when | Severity |
|-------|-----------|----------|
| `PostgresqlInstanceDown` | `pg_up == 0` for 1m | critical |
| `PostgresqlNoPrimary` | no instance reports primary for 2m | critical |
| `PostgresqlSplitBrain` | >1 instance reports primary for 2m | critical |
| `PostgresqlStandbyMissing` | fewer than 2 standbys online for 5m | warning |
| `PostgresqlReplicationLagHigh` | standby >60s behind for 5m | warning |
| `PostgresqlTooManyConnections` | >80% of `max_connections` for 5m | warning |
| `PostgresqlHighRollbackRate` | >10% transactions rolling back for 10m | warning |
| `PostgresqlLowCacheHitRatio` | buffer cache hit <90% for 30m | warning |
| `PostgresqlDeadlocksDetected` | >5 deadlocks in 5m | warning |

The replication-based alerts (`NoPrimary`, `SplitBrain`, `StandbyMissing`,
`ReplicationLagHigh`) depend on the exporter's `pg_replication_is_replica` /
`pg_replication_lag` metrics — same caveat as the dashboard. Verify in Prometheus
and adjust the metric name if your exporter differs.

Verify after sync:
```bash
kubectl -n arvan get prometheusrule postgresql-ha-rules
# In Prometheus UI (prometheus.idistance.ir) -> Alerts: the postgresql-ha.* groups appear.
# Routing to receivers (email/Slack/etc.) is Alertmanager config — separate from these rules.
```

## Troubleshooting

Errors hit during the initial bring-up, and their fixes:

**`error fetching chart … helm pull --repo https://charts.bitnami.com/bitnami … invalid_reference: invalid tag`**
The live app is still using the old HTTP repo. Causes, in order of likelihood:
1. `root` not synced (or stale) → the live `postgresql-ha` still has the old
   `repoURL`. Hard-refresh + sync `root` (see the Deploy warning), confirm with
   `argocd app get postgresql-ha -o yaml | grep repoURL`.
2. OCI registry not registered → apply the `bitnamicharts-oci` Secret (step 3).
3. Cached manifest after fixing the above → `argocd app get postgresql-ha --hard-refresh`.

**`MountVolume.SetUp failed … secret "postgresql-ha-credentials"/"pgpool-credentials" not found`**
The out-of-band Secrets aren't in the `arvan` namespace yet → create them (step 2).
kubelet retries the mount automatically once they exist.

**`The POSTGRESQL_SR_CHECK_PASSWORD environment variable is empty or not set`**
The `pgpool-credentials` Secret is missing the `sr-check-password` key (the
postgresql pods read it from the *pgpool* secret, not their own). Recreate it
with **both** `admin-password` and `sr-check-password`, then restart the pods:
```bash
kubectl -n arvan delete pod -l app.kubernetes.io/name=postgresql-ha
```

**`password authentication failed`** (as opposed to the "empty" error above)
A data dir on a `local` PV was initialized with a *different* password than the
Secret now holds. Wipe the dirs (see Teardown) and delete the pods to re-init.
Note: passwords are baked into the PV data on first boot — **don't change a
Secret after a successful init** expecting it to take effect.

## Teardown note

`local` PVs are `Retain`. After deleting the app, clear the data dirs before a
fresh install or the PVs stay `Released` and won't rebind:

```bash
ssh node1 'sudo rm -rf /mnt/local-storage/postgres/*'
ssh node2 'sudo rm -rf /mnt/local-storage/postgres/*'
ssh node3 'sudo rm -rf /mnt/local-storage/postgres/*'
```
