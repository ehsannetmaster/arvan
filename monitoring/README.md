# kube-prometheus-stack — Prometheus & Grafana

Configuration notes for the **kube-prometheus-stack** deployment (Prometheus,
Alertmanager, Grafana, node-exporter, kube-state-metrics, prometheus-operator)
on the Kubespray lab cluster. This covers the stack itself — how Argo CD manages
the repo is documented separately.

- **Chart:** `kube-prometheus-stack` `86.2.0` (prometheus-operator `v0.91.0`)
- **Values:** `values.yaml` (this directory)
- **Dashboards:** `*-dashboard.yaml` (ConfigMaps, see Grafana below)

---

## Storage — static `local` PVs on node3

Persistence uses a `local-storage` StorageClass with pre-declared `local`
PersistentVolumes on **node3** — Kubespray ships no dynamic provisioner, so the
PVs are static and PVCs bind to them by size.

`local-storage.yaml` defines the StorageClass (`kubernetes.io/no-provisioner`,
`WaitForFirstConsumer`, `reclaimPolicy: Retain`) and three PVs whose sizes match
the PVC requests in `values.yaml` exactly, so the binder pairs each to its one
correct volume:

| Component    | Size  | Path on node3                    |
|--------------|-------|----------------------------------|
| Prometheus   | 10Gi  | `/mnt/local-storage/prometheus`   |
| Grafana      | 5Gi   | `/mnt/local-storage/grafana`      |
| Alertmanager | 2Gi   | `/mnt/local-storage/alertmanager` |

### Create the directories on node3 first

There is no provisioner, so the backing directories must exist before anything
binds. SSH to node3 (`100.64.231.103`):

```bash
sudo mkdir -p /mnt/local-storage/{prometheus,grafana,alertmanager}
sudo chmod 0777 /mnt/local-storage/*
```

The PVs pin to the node named `node3` via `nodeAffinity`; confirm that matches
reality (`kubectl get nodes`) and edit `local-storage.yaml` if not.

> All three stateful pods schedule onto **node3**, because that's where their
> `local` PVs live (`WaitForFirstConsumer` binds the PVC only after the pod is
> placed). A PVC stuck `Pending` means the dir on node3 is missing or the node
> name in the PV's `nodeAffinity` is wrong.

> `local` PVs only support `reclaimPolicy: Retain`. After deleting the stack the
> PVs go to `Released` and the data stays on node3 — to fully reset, clear the
> dirs: `ssh node3 'sudo rm -rf /mnt/local-storage/{prometheus,grafana,alertmanager}/*'`.

---

## Grafana

### Admin credentials

`values.yaml` carries **no** password — this is a public repo. Grafana reads
`admin.existingSecret: grafana-admin`; create that secret on the cluster:

```bash
kubectl -n monitoring create secret generic grafana-admin \
  --from-literal=admin-user=admin \
  --from-literal=admin-password='<your-password>'
```

> **Never change the admin password from the Grafana UI** in this provisioned
> setup — the UI writes only the DB, leaving the sidecar's secret password stale,
> which 401s the provisioning reload and the datasource silently disappears.
> Rotate by updating the secret + `rollout restart`, not in the UI.

### Datasources

The Prometheus datasource is provisioned automatically. `values.yaml` adds a
second one, `ip-lookups` (the ip-country-api request log), reached cross-namespace
via the Pgpool FQDN `postgresql-ha-pgpool.arvan.svc.cluster.local:5432`. Its
password comes from `$PG_DB_PASSWORD`, injected from a secret named `ip-lookups-db`
in the **`monitoring`** namespace. `secretKeyRef` can't cross namespaces, so copy
the password out of the `arvan` namespace's `postgresql-ha-credentials` secret —
create this only once Postgres exists:

```bash
kubectl -n monitoring create secret generic ip-lookups-db \
  --from-literal=password="$(kubectl -n arvan get secret postgresql-ha-credentials \
    -o jsonpath='{.data.password}' | base64 -d)"
```

> Without it the Grafana pod stays in `CreateContainerConfigError`. If you're
> running the stack without Postgres, create a placeholder secret or drop the
> `envValueFrom` + `additionalDataSources` blocks from `values.yaml`.

### Dashboards

Grafana runs the **dashboard sidecar**, which imports any ConfigMap in the
`monitoring` namespace labelled `grafana_dashboard: "1"`. Beyond the built-in
kube-prometheus-stack dashboards (cluster, nodes, namespaces, workloads), three
extra dashboards ship as ConfigMaps with embedded JSON (no grafana.com fetch at
runtime — important on this geo-blocked cluster):

- `postgres-dashboard.yaml` — hand-written PostgreSQL HA
- `postgres-dashboard-9628.yaml` — community ID 9628 variant
- `ip-country-api-dashboard.yaml` — ip-country-api `/metrics`

Each uses a `$datasource` template variable that auto-binds to the default
Prometheus datasource, so no manual wiring is needed on import.

### root_url behind the proxy

`grafana.ini.server.root_url` is pinned to `https://grafana.idistance.ir` so
links/redirects are correct even though ingress-nginx hands Grafana a plain-HTTP
request. `deploymentStrategy: Recreate` (the PVC is RWO on a single node).

---

## Prometheus

Key settings in `values.yaml` → `prometheus.prometheusSpec`:

- `externalUrl: https://prometheus.idistance.ir`
- `retention: 15d` plus `retentionSize: "9GB"` — a size cap just under the 10Gi
  PV (~84%), leaving headroom for WAL/compaction.
- `*SelectorNilUsesHelmValues: false` for ServiceMonitor / PodMonitor / Rule /
  Probe / scrapeConfig — so Prometheus picks up **all** of them in the cluster,
  not just resources carrying the chart's release label (handy in a lab).
- `storageSpec` → 10Gi PVC on `local-storage`.

---

## Alertmanager

`alertmanager.alertmanagerSpec`:

- `externalUrl: https://alertmanager.idistance.ir`
- 2Gi PVC on `local-storage`.

---

## Exposure

Grafana / Prometheus / Alertmanager are exposed via ingress-nginx behind the same
external nginx TLS-offload proxy used elsewhere (`../bootstrap/README.md`). Each
Service stays ClusterIP (no MetalLB); the chart Ingresses use
`ingressClassName: nginx` and `*.idistance.ir` hostnames with **no `tls:`** block
— TLS terminates at the external nginx, which forwards plain HTTP to the worker
IPs on `:80` (host-network ingress-nginx), preserving `Host` so ingress-nginx
routes by hostname.

Reuse the `arvan-ingress` upstream (all three worker IPs `100.64.231.101-103`,
default `:80`). One `server` block fronts all the monitoring hostnames at once:

```nginx
upstream arvan-ingress {
    server 100.64.231.101;
    server 100.64.231.102;
    server 100.64.231.103;
    keepalive 10;
    zone upstreams-http 64K;
}

server {
    listen 443 ssl http2;
    ssl_certificate     /home/damavand/fullchain.pem;
    ssl_certificate_key /home/damavand/privkey.pem;
    server_name argo.idistance.ir grafana.idistance.ir prometheus.idistance.ir alertmanager.idistance.ir ip-country.idistance.ir;

    location / {
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_pass http://arvan-ingress;
    }
}
```

**DNS:** point `grafana`, `prometheus`, `alertmanager` `.idistance.ir` at the
external nginx host (A records / CNAME), like `argo.idistance.ir`.



### Log in to Grafana

- URL: `https://grafana.idistance.ir`
- User: `admin`
- Password: whatever you put in the `grafana-admin` secret
  (`kubectl -n monitoring get secret grafana-admin -o jsonpath='{.data.admin-password}' | base64 -d`)

---

## Known gaps (Kubespray-specific)

- **kube-proxy target shows DOWN.** Kubespray binds its metrics to `127.0.0.1`.
  To fix cluster-side, set kube-proxy `metricsBindAddress: 0.0.0.0` in
  `group_vars` and re-run. Until then that panel is empty — everything else
  (kube-controller-manager, kube-scheduler, nodes, pods, kubelet, API server)
  works.


---

## Summary of decisions

| Area              | Decision                                                       |
|-------------------|----------------------------------------------------------------|
| Chart             | kube-prometheus-stack `86.2.0` (operator `v0.91.0`)            |
| Storage           | Static `local` PVs on node3, no provisioner (`local-storage`)  |
| Persistence       | Prometheus 10Gi, Grafana 5Gi, Alertmanager 2Gi (Retain)        |
| Exposure          | Ingress: grafana / prometheus / alertmanager `.idistance.ir`   |
| TLS               | Offloaded at external nginx (no `tls:` in Ingress)             |
| Dashboards        | Postgres HA (+ID 9628) & ip-country-api via sidecar ConfigMaps |
| Extra datasource  | `ip-lookups` Postgres (Pgpool), password from `ip-lookups-db` secret |
