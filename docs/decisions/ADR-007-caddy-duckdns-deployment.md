# ADR-007 · Caddy + DuckDNS for the public competition URL

## Context

The competition demo must be reachable as `https://<name>.duckdns.org` on a
single Ubuntu 24.04 box behind a home/cloud NAT, with no ops team and no budget
for extra infrastructure. The app itself is a single container serving both the
built web UI and `/api`.

## Decision

1. **DuckDNS** provides the name; the token lives in a root-only file
   (`/etc/duckdns/duckdns.ini`, mode 600) or a systemd unit — never in git, the
   frontend, the logs or `/api/health`.
2. **Caddy** terminates TLS and proxies to the app (`deploy/Caddyfile`):
   automatic Let's Encrypt certificates, HTTP→HTTPS redirect by default,
   `Host`/`X-Real-IP`/`X-Forwarded-*` preserved, 32 MB body cap, 60 s upstream
   read/write timeout, HSTS, bounded JSON access log.
3. The app container publishes **only on loopback** (`127.0.0.1:8012:8012`);
   only Caddy is exposed on 80/443.
4. The app accepts the public hostname only when it is listed in
   `LEEXTRACTOR_PUBLIC_HOSTS`; unset means local-only (unchanged default).

## Alternatives

* **nginx + certbot** — works, but adds a renewal timer and more configuration
  to get wrong; kept as an option in DEPLOYMENT.md for hosts already running
  nginx.
* **No TLS, IP:port only** — the competition expects an https URL, and browsers
  treat plain HTTP origin as insecure for the fetch API.
* **Cloudflare Tunnel** — extra dependency and account; not needed for a single
  host with a public IP.

## Reason

Caddy removes the two moving parts that usually break a demo (certificate
renewal and http→https redirect) and its configuration is short enough to audit
in one screen. DuckDNS is free, scriptable and already the plan.

## Consequences

* Ports 80 and 443 must be reachable for the ACME challenge; the app port must
  not be.
* Every short timeout is a deliberate choice: a slow backend must surface as a
  slow job, not as a proxy that hides it (prompt §15).
* Rotating the DuckDNS token means editing one root-owned file and restarting
  the updater — no application redeploy.

## Rollback

Stop Caddy (`docker compose --profile proxy down` or `systemctl stop caddy`) and
reach the app through an SSH tunnel:

```
ssh -N -L 8012:127.0.0.1:8012 ubuntu@<server>
```

That is also the documented fallback if the domain or certificate fails during
the demo.
