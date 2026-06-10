# Exposing the Argo CD Web Panel via Ingress

How to reach the Argo CD UI at **`https://argo.idistance.ir`** through an external
TLS-terminating nginx proxy that load-balances to the cluster's worker nodes.

> Kubespray installs Argo CD (`argocd_enabled: true`) but does **not** create an
> Ingress for it — that part is manual. This doc covers it.

---

## Traffic flow

```
Client ──HTTPS (argo.idistance.ir)──► External nginx        (TLS terminated here)
                                         │  proxy_pass http://arvan-ingress
                                         │  Host: argo.idistance.ir
                                         │  X-Forwarded-Proto: https
                                         ▼
                  Worker nodes :80  100.64.231.101 / .102 / .103   (ingress-nginx, host-network)
                                         │  routes by Host = argo.idistance.ir
                                         │  backend-protocol: HTTPS
                                         ▼
                       argocd-server :443   (namespace: argocd)
```

- **TLS is offloaded at the external nginx.** Traffic from nginx → workers is plain HTTP on port 80.
- ingress-nginx runs on host network, so it answers directly on each worker's `:80`.
- ingress-nginx re-encrypts to `argocd-server` over HTTPS (`backend-protocol: HTTPS`),
  which makes Argo CD see a secure connection and **avoids its HTTP→HTTPS redirect loop**
  without modifying Argo CD itself.

---

## Prerequisites

- Cluster is up; `ingress_nginx_enabled: true` with `ingress_nginx_host_network: true`.
- Argo CD is installed in the `argocd` namespace (`argocd_enabled: true`).
- `argo.idistance.ir` resolves (publicly or via DNS) to the **external nginx** host.

---

## Step 1 — Create the Argo CD Ingress

The `host` **must** equal `argo.idistance.ir`, because the external nginx forwards
`Host: $host`, and ingress-nginx routes by that header.

Paste with a quoted heredoc (preserves indentation, no vim autoindent issues):

```bash
cat > argocd-ingress.yaml <<'EOF'
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: argocd-server
  namespace: argocd
  annotations:
    # Talk to argocd-server over HTTPS so it doesn't force a redirect loop:
    nginx.ingress.kubernetes.io/backend-protocol: "HTTPS"
spec:
  ingressClassName: nginx
  rules:
    - host: argo.idistance.ir
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: argocd-server
                port:
                  name: https        # argocd-server's 443 port
EOF

kubectl apply -f argocd-ingress.yaml
```

> **No `tls:` block here on purpose** — TLS is terminated at the external nginx, and
> the connection from nginx to ingress is plain HTTP. ingress-nginx still re-encrypts
> to the Argo CD backend via the `backend-protocol: HTTPS` annotation.

---

## Step 2 — External nginx proxy (SSL offload + load balance)

Your working config, annotated. TLS terminates here; requests are forwarded as HTTP
to all three workers on port 80 (where ingress-nginx listens on host network).

One `server` block fronts every `*.idistance.ir` service — Argo CD alongside the
monitoring hosts (see `../monitoring/README.md` for those Ingresses). They all share
the `arvan-ingress` upstream and route by `Host`.

```nginx
upstream arvan-ingress {
    server 100.64.231.101;       # worker node1  (ingress-nginx :80)
    server 100.64.231.102;       # worker node2  (ingress-nginx :80)
    server 100.64.231.103;       # worker node3  (ingress-nginx :80)
    keepalive 10;
    zone upstreams-http 64K;
}

server {
    listen 443 ssl http2;
    ssl_certificate     /home/damavand/fullchain.pem;
    ssl_certificate_key /home/damavand/privkey.pem;
    server_name argo.idistance.ir grafana.idistance.ir prometheus.idistance.ir alertmanager.idistance.ir ip-country.idistance.ir;

    location / {
        proxy_set_header Host              $host;              # preserved → matches Ingress rule
        proxy_set_header X-Forwarded-Proto https;             # tells Argo CD the original scheme
        proxy_pass http://arvan-ingress;
    }
}
```

---

## Step 3 — DNS

Point `argo.idistance.ir` at the **external nginx** host's public IP (an A record, or
your edge/CDN). The external nginx is the only public entry point; the worker IPs
(`100.64.231.x`) stay private/behind it.

---

## Step 4 — Log in

Get the auto-generated initial admin password:

```bash
kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath="{.data.password}" | base64 -d; echo
```

- URL: `https://argo.idistance.ir`
- Username: `admin`
- Password: (output above)

Change/disable the initial admin password after first login, then delete the secret:
```bash
kubectl -n argocd delete secret argocd-initial-admin-secret
```

---

## Verify

```bash
# Ingress object exists and has the host
kubectl -n argocd get ingress argocd-server

# argocd-server is running and its service has http/https ports
kubectl -n argocd get svc argocd-server
kubectl -n argocd get pods -l app.kubernetes.io/name=argocd-server

# From the external nginx host, test the path to a worker (expect HTTP 200/302, not 403/404):
curl -kI -H "Host: argo.idistance.ir" http://100.64.231.102/
```

