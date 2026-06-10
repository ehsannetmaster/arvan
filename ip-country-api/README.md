# IP Country API

A tiny FastAPI service that resolves an IP address to its country, fully
offline using the [`geoip2fast`](https://pypi.org/project/geoip2fast/) dataset
(no external API calls, no rate limits).

## Run locally

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

The API is then available at http://127.0.0.1:8000 with interactive docs at
http://127.0.0.1:8000/docs.

## Endpoints

| Method | Path                | Example                              |
|--------|---------------------|--------------------------------------|
| GET    | `/country/{ip}`     | `/country/8.8.8.8`                   |
| GET    | `/country?ip=...`   | `/country?ip=8.8.8.8`                |
| GET    | `/health`           | liveness probe                       |
| GET    | `/metrics`          | Prometheus metrics (scraped)         |

### Example

```bash
curl http://127.0.0.1:8000/country/8.8.8.8
```

```json
{
  "ip": "8.8.8.8",
  "country_code": "US",
  "country_name": "United States",
  "is_private": false
}
```

Invalid input returns `400`; private/reserved ranges are flagged via
`is_private`.

## Docker

```bash
docker build -t ip-country-api .
docker run -p 8000:8000 ip-country-api
```

## Request logging (PostgreSQL)

Every successful lookup is recorded to the cluster's `postgresql-ha` database
(reached via the Pgpool proxy `postgresql-ha-pgpool.arvan.svc:5432`, database
`appdb`). The write runs as a FastAPI **background task**, so it never delays the
response, and it is **fault-tolerant** — if Postgres is unreachable the API still
serves lookups and logging silently degrades to a no-op.

Table (auto-created on startup):

```sql
CREATE TABLE IF NOT EXISTS ip_lookups (
    id           BIGSERIAL PRIMARY KEY,
    ts           TIMESTAMPTZ NOT NULL DEFAULT now(),  -- date/time of the request
    ip           TEXT NOT NULL,
    country_code TEXT,
    country_name TEXT
);
```

Connection comes from standard `PG*` env vars set in
[k8s/deployment.yaml](k8s/deployment.yaml); `PGPASSWORD` is sourced from the
existing `postgresql-ha-credentials` Secret (key `password` = the `appuser`
password), so no new secret is introduced.

Inspect the recorded rows:

```bash
kubectl -n arvan exec -it postgresql-ha-postgresql-0 -- \
  env PGPASSWORD="$(kubectl -n arvan get secret postgresql-ha-credentials -o jsonpath='{.data.password}' | base64 -d)" \
  psql -U appuser -d appdb -c \
  "SELECT ts, ip, country_code, country_name FROM ip_lookups ORDER BY ts DESC LIMIT 20;"
```

## Metrics (Prometheus)

The app exposes Prometheus metrics at **`GET /metrics`**, scraped by the
kube-prometheus-stack Operator via [k8s/servicemonitor.yaml](k8s/servicemonitor.yaml).
No `release` label is needed — Prometheus runs with
`serviceMonitorSelectorNilUsesHelmValues: false` (see `monitoring/values.yaml`).

| Metric | Type | Notes |
|--------|------|-------|
| `ip_country_lookups_total{country_code}` | counter | lookups per resolved country (custom) |
| `http_requests_total{handler,method,status}` | counter | requests per endpoint/status |
| `http_request_duration_seconds_*` | histogram | request latency |
| `http_requests_inprogress` | gauge | in-flight requests |

### Useful PromQL

```promql
# request rate per endpoint
sum by (handler) (rate(http_requests_total[5m]))

# top countries looked up (last hour)
topk(10, sum by (country_code) (increase(ip_country_lookups_total[1h])))

# p95 latency
histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket[5m])))

# error ratio
sum(rate(http_requests_total{status=~"4xx|5xx"}[5m]))
  / sum(rate(http_requests_total[5m]))
```

After deploy, confirm Prometheus is scraping at
`https://prometheus.idistance.ir` → **Status → Targets** (look for
`serviceMonitor/arvan/ip-country-api`).

> `/metrics` is also reachable through the public ingress (path `/`). For a lab
> that's fine; to hide it, block `/metrics` at the external nginx or split it to
> a separate non-ingressed port.

## CI/CD + GitOps deploy

Live in production at **`https://ip-country.idistance.ir`** (TLS terminated at
the external nginx, same model as `argocd-ingress.md`).

### Flow

```
push to master (ip-country-api/**)
   └─ GitHub Actions (self-hosted runner)  .github/workflows/ip-country-api.yml
        ├─ docker build
        ├─ push ghcr.io/ehsannetmaster/ip-country-api:<sha>  (+ :latest)
        └─ rewrite k8s/deployment.yaml image tag → commit back  [skip ci]
              └─ Argo CD app `ip-country-api` goes OutOfSync
                    └─ you sync manually → Deployment/Service/Ingress in `arvan` ns
```

- Pipeline: [.github/workflows/ip-country-api.yml](../.github/workflows/ip-country-api.yml)
- Manifests: [k8s/](k8s/) — Deployment + Service + Ingress
- Argo CD app: [apps/ip-country-api.yaml](../apps/ip-country-api.yaml)

### One-time setup

1. **Runner**: the `self-hosted` runner needs `docker` and `git` on PATH and
   outbound access to `ghcr.io` and GitHub. The runner's user must be able to
   talk to the Docker daemon **without sudo** — add it to the `docker` group,
   then restart the runner so the new group takes effect:
   ```bash
   sudo usermod -aG docker <runner-user>
   sudo systemctl restart actions.runner.*   # or stop/start ./run.sh
   sudo -u <runner-user> docker info >/dev/null && echo "docker OK"
   ```
2. **GHCR package visibility**: after the first push, set the
   `ip-country-api` package to **Public** (GitHub → your profile → Packages →
   ip-country-api → Package settings → Change visibility). Then the cluster pulls
   with no imagePullSecret.
3. **Argo CD**: sync the `root` app once so it creates the `ip-country-api`
   child app, then sync that child (`argocd app sync ip-country-api`).
4. **External nginx**: add a `server` block for `ip-country.idistance.ir`
   (copy the argo one), proxying to the workers `100.64.231.102/.103` on `:80`:
   ```nginx
   upstream ip-country-upstream {
       server 100.64.231.102;       # worker node2 (ingress-nginx :80)
       server 100.64.231.103;       # worker node3 (ingress-nginx :80)
       keepalive 10;
   }

   server {
       listen 443 ssl http2;
       ssl_certificate     /home/damavand/fullchain.pem;   # must cover this host
       ssl_certificate_key /home/damavand/privkey.pem;
       server_name ip-country.idistance.ir;

       location / {
           proxy_set_header Host             $host;          # → matches Ingress rule
           proxy_set_header X-Forwarded-Proto https;
           proxy_set_header X-Real-IP        $remote_addr;
           proxy_set_header X-Forwarded-For  $proxy_add_x_forwarded_for;
           proxy_pass http://ip-country-upstream;
       }
   }
   ```
5. **DNS**: point `ip-country.idistance.ir` at the external nginx host.

### Verify

```bash
# 1. Service directly (in-cluster, bypasses ingress/DNS):
kubectl -n arvan port-forward svc/ip-country-api 8080:80
curl http://127.0.0.1:8080/country/8.8.8.8

# 2. Through ingress-nginx (from the external nginx host, bypasses DNS):
curl -H "Host: ip-country.idistance.ir" http://100.64.231.102/country/8.8.8.8

# 3. Public URL (once DNS + nginx are live):
curl https://ip-country.idistance.ir/country/8.8.8.8
```

> On Windows PowerShell, `curl` is an alias for `Invoke-WebRequest`. Use
> `curl.exe https://ip-country.idistance.ir/country/8.8.8.8` for raw output, or
> `Invoke-RestMethod https://ip-country.idistance.ir/country/8.8.8.8` to get a
> parsed object.

**Status:** live and verified — `GET /country/1.1.1.1` → `200 OK`
`{"ip":"1.1.1.1","country_code":"AU","country_name":"Australia","is_private":false}`.
