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

## CI/CD + GitOps deploy

Reachable in production at **`http://ip-checker.idistance.ir`** (TLS terminated at
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
   outbound access to `ghcr.io` and GitHub.
2. **GHCR package visibility**: after the first push, set the
   `ip-country-api` package to **Public** (GitHub → your profile → Packages →
   ip-country-api → Package settings → Change visibility). Then the cluster pulls
   with no imagePullSecret.
3. **Argo CD**: sync the `root` app once so it creates the `ip-country-api`
   child app, then sync that child (`argocd app sync ip-country-api`).
4. **External nginx**: add a `server` block for `ip-checker.idistance.ir`
   (copy the argo one), proxying to the workers `100.64.231.102/.103` on `:80`.
5. **DNS**: point `ip-checker.idistance.ir` at the external nginx host.

### Verify

```bash
# in-cluster path (from the external nginx host):
curl -H "Host: ip-checker.idistance.ir" http://100.64.231.102/country/8.8.8.8
# once DNS + nginx are live:
curl http://ip-checker.idistance.ir/country/8.8.8.8
```
