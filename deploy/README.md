# Deploying Sports Follow

One Ubuntu server runs everything with Docker Compose. Caddy terminates HTTPS and gets its certificate
by itself; the API serves the web app, so pages, API, live streams, the service worker and the Android
app's asset links share one origin.

```
            443 / 80
phone ─────────────────▶ caddy ──▶ api (uvicorn, 2 workers) ──┬──▶ postgres
                                                              ├──▶ redis ◀── worker (arq, Stockfish)
                                   gateway (llm-providers) ◀──┴───────────────┘
                                        └──▶ OpenRouter
backup: pg_dump once a day, 14 kept in /var/backups/sports-follow
```

Only Caddy is published. Postgres, Redis, the gateway and the API talk on the compose network. The
build runs licensed sources only (`settings.env`): Sportmonks football and cricket, and Lichess.

| File | What |
|---|---|
| `compose.yaml` | The stack. `x-app` is the one image the API, worker and migration share (`../Dockerfile`). |
| `Caddyfile` | HTTPS for `$DOMAIN`; live streams pass through unbuffered and uncompressed. |
| `gateway/` | The llm-providers gateway image, pinned to a tag and built from its own lockfile, and its config: OpenRouter only, this app only, $5 a day. |
| `settings.env` | Non-secret settings for the public build. |
| `server-setup.sh` | One-time server setup: updates, firewall, swap, Docker, this repo in `/opt/sports-follow`. |
| `push-secrets.sh` | From the Mac: copies the API tokens to the server without printing them (runs `install-secrets.sh` there). |
| `seed-registry.sh` | From the Mac: copies the player registry (323,000 athletes, 15 MB) instead of an hour of Wikidata queries. |
| `backup.sh` | The daily database dump. |

## Size and cost

The whole stack uses about 450 MB of memory at rest; the API is the largest (240 MB for two workers).
A **2 vCPU / 4 GB** server leaves room for the weekly registry import, builds and the chess engine:
$24 a month on DigitalOcean (Bangalore) or AWS Lightsail (Mumbai), plus weekly backups ($4.80 on
DigitalOcean).

## First deploy

**1. Create the server** (you): Ubuntu 24.04, 2 vCPU / 4 GB, in Bangalore or Mumbai, with your Mac's
SSH key (`~/.ssh/id_ed25519.pub`) and weekly backups on. Note its IP address.

**2. Set it up** (from the repo on the Mac):
```bash
ssh root@<server-ip> 'bash -s' < deploy/server-setup.sh
```
Without a domain the app lives at `https://<ip-with-dashes>.sslip.io`, with a real certificate. For
your own domain, point an A record at the server first and run `DOMAIN=app.example.com bash -s` instead
(or edit `/opt/sports-follow/deploy/.env` later).

**3. Send the secrets:**
```bash
deploy/push-secrets.sh root@<server-ip>
```
Sends the Sportmonks and Lichess tokens from `~/.config/sports-follow` and the OpenRouter key from the
gateway's keys file (`OPENROUTER_KEY_FILE=<file>` for a separate production key). The server makes its
own database password, gateway token and push key; they are kept when you run this again.

**4. Start it** (the first build takes a few minutes on the server):
```bash
ssh root@<server-ip> 'cd /opt/sports-follow && docker compose -f deploy/compose.yaml up -d --build'
```

**5. Copy the registry:**
```bash
deploy/seed-registry.sh root@<server-ip>
```

**6. Check it:** `https://<domain>/api/health` answers `{"ok":true,"db":true,"redis":true}`, the app
opens, search finds players, and `/credits` lists Sportmonks and Lichess only.

## Updating

```bash
ssh root@<server-ip> 'cd /opt/sports-follow && git pull && docker compose -f deploy/compose.yaml up -d --build'
```
Migrations run first (the `migrate` service); the API starts only when they succeed. Stopping the
worker mid-build leaves that card "building" until its lock expires (15 minutes).

## Operating

On the server, in `/opt/sports-follow`, with `c() { docker compose -f deploy/compose.yaml "$@"; }`:

| | |
|---|---|
| Status | `c ps` |
| Logs | `c logs -f api worker` (Caddy's certificate work: `c logs caddy`) |
| Restart after a secret changes | `c up -d --force-recreate` |
| A shell in the database | `c exec postgres psql -U sports sports_follow` |
| Registry import now | `c exec worker python -m sports_follow.registry` |
| Backups | `ls /var/backups/sports-follow` |
| Restore a backup | `c stop api worker && c exec -T postgres pg_restore -U sports -d sports_follow --clean --if-exists < /var/backups/sports-follow/<file>.dump && c start api worker` |

Backups stay on the server; the provider's weekly server backup is the copy elsewhere. Off-site dumps
(object storage) are a later step.

## Rehearsing locally

The same stack runs on the Mac on other ports, with Caddy's local certificate authority:
```bash
DOMAIN=localhost HTTP_PORT=8080 HTTPS_PORT=8443 SECRETS_DIR=<dir> BACKUP_DIR=<dir> \
  docker compose -p sf-prodtest -f deploy/compose.yaml up -d --build
curl -k https://localhost:8443/api/health
```
`<dir>` needs the same files `install-secrets.sh` writes: `app.env`, `gateway.env`, `postgres.env` and
`files/{sportmonks-token,lichess-token,vapid-private.pem}`.
