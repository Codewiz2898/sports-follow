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
GitHub Actions ──ssh deploy@──▶ deploy.sh (build, migrate, swap, wait for health)
systemd timers: server checks every 5 minutes (alerts to your phone), Docker clean-up weekly
```

Only Caddy is published. Postgres, Redis, the gateway and the API talk on the compose network. The
build runs licensed sources only (`settings.env`): Sportmonks football and cricket, and Lichess.

| File | What |
|---|---|
| `compose.yaml` | The stack. `x-app` is the one image the API, worker and migration share (`../Dockerfile`). |
| `Caddyfile` | HTTPS for `$DOMAIN`; live streams pass through unbuffered and uncompressed. |
| `gateway/` | The llm-providers gateway image, pinned to a tag and built from its own lockfile, and its config: OpenRouter only, this app only, $5 a day. |
| `settings.env` | Non-secret settings for the public build. |
| `server-setup.sh` | Server setup: updates, firewall, swap, Docker, the `deploy` user, SSH keys only, fail2ban, the timers, this repo in `/opt/sports-follow`. Safe to run again. |
| `push-secrets.sh` | From the Mac: copies the API tokens to the server without printing them (runs `install-secrets.sh` there). |
| `seal-secrets.sh`, `secrets-recipients.txt` | On the server: seals every secret into `secrets.age` for the age keys listed, or puts a sealed copy back. |
| `backup-secrets.sh`, `restore-secrets.sh` | From any machine: download the sealed copy for the password manager, or put it back on a new server. |
| `deploy.sh` | On the server: deploys a commit of main, rolls back, or shows what's live. GitHub Actions runs it. |
| `../.github/workflows/deploy.yml` | Checks that CI passed, then runs `deploy.sh` over SSH, then a health check from outside. |
| `server-check.sh`, `systemd/` | The server checks and their alerts; the weekly Docker clean-up. |
| `seed-registry.sh` | From the Mac: copies the player registry (323,000 athletes, 15 MB) instead of an hour of Wikidata queries. |
| `backup.sh` | The daily database dump. |

## Size and cost

The whole stack uses about 450 MB of memory at rest; the API is the largest (240 MB for two workers).
A **2 vCPU / 4 GB** server leaves room for the weekly registry import, builds and the chess engine:
$24 a month on DigitalOcean or AWS Lightsail, plus weekly backups ($4.80 on DigitalOcean). Any region
works; one near India (Bangalore, Mumbai or Singapore) is only a little snappier for fans there.

## First deploy

**0. Make a recovery key** (you), so the Mac isn't the only way in. Either a hardware key
(`ssh-keygen -t ed25519-sk -C sports-follow-recovery`) or a key kept in your password manager:
```bash
ssh-keygen -t ed25519 -C sports-follow-recovery -f ~/sports-follow-recovery
```
Save `~/sports-follow-recovery` (the private half) in the password manager, keep the `.pub` file for
step 2, then delete the private half from the Mac.

**0b. Make a secrets key** (you), so the server's secrets can be kept off the Mac and the server (see
[Secrets](#secrets)). With the Mac's Docker, nothing installed:
```bash
docker run --rm alpine:3.20 sh -c 'apk add -q age >/dev/null && age-keygen'
```
Save the `AGE-SECRET-KEY-1…` line in the password manager. Add the `age1…` public key as a line of
`deploy/secrets-recipients.txt` and commit it.

**1. Create the server** (you): Ubuntu 24.04, 2 vCPU / 4 GB, with your Mac's SSH key
(`~/.ssh/id_ed25519.pub`) and weekly backups on. Note its IP address.

**2. Set it up** (from the repo on the Mac). This is the last time you log in as root:
```bash
ssh root@<server-ip> "RECOVERY_KEY='$(cat ~/sports-follow-recovery.pub)' bash -s" < deploy/server-setup.sh
```
The repo is private, so the first run stops and prints a key for the server to read it with: add it under
[Settings → Deploy keys](https://github.com/Codewiz2898/sports-follow/settings/keys/new) with write access
off, then run the same command again. Deploys fetch with it too. (It isn't `DEPLOY_KEY`, which lets
GitHub Actions into the server.)

Without a domain the app lives at `https://<ip-with-dashes>.sslip.io`, with a real certificate. For
your own domain, point an A record at the server first and add `DOMAIN=app.example.com` in front of
`bash -s` (or edit `/opt/sports-follow/deploy/.env` later).

**3. Send the secrets:**
```bash
deploy/push-secrets.sh deploy@<server-ip>
```
Sends the Sportmonks and Lichess tokens (and the PandaScore, Liquipedia and PUBG ones, if there are) from
`~/.config/sports-follow`, and production's OpenRouter key from `~/.config/sports-follow/openrouter-key`
(see [Secrets](#secrets); without it, the Mac's development key, with a warning). The server makes its own
database password, gateway token, push key and fan cookie key; they are kept when you run this again.
Then it seals all of them in `/var/backups/sports-follow/secrets.age`. Download that sealed copy into the
password manager:
```bash
deploy/backup-secrets.sh deploy@<server-ip>
```

**4. Start it** (the first build takes a few minutes on the server):
```bash
ssh deploy@<server-ip> /opt/sports-follow/deploy/deploy.sh deploy
```

**5. Copy the registry:**
```bash
deploy/seed-registry.sh deploy@<server-ip>
```

**6. Check it:** `https://<domain>/api/health` answers `{"ok":true,"db":true,"redis":true}`, the app
opens, search finds players, and `/credits` lists Sportmonks and Lichess only.

**7. Turn on the alerts and the GitHub deploys** (below).

## Getting in

Root can't log in over SSH, and nobody can log in with a password. Everyone logs in as `deploy`, which
has sudo without a password (it's in the `docker` group, which is as good as root anyway):

| Key | Can |
|---|---|
| The Mac's (copied from root by the first setup) | everything |
| The recovery key (`RECOVERY_KEY`) | everything |
| GitHub Actions' (`DEPLOY_KEY`) | run `deploy.sh` and nothing else: no shell, no forwarding |
| The provider's web console | the last resort, if no key works: reset root's password in the provider's panel and log in there |

Try the recovery key once it's in: `ssh -i <recovery key> deploy@<server-ip> true`.

fail2ban bans an address for an hour after 5 failed logins in 10 minutes, and then even a right key can't
get in from there: wait, use another network, or the console (`sudo fail2ban-client status sshd`,
`sudo fail2ban-client unban <address>`). Run the setup again after changing it, or to add a key
(keys are added, never removed; remove one by editing `~deploy/.ssh/authorized_keys`):
```bash
ssh deploy@<server-ip> "sudo RECOVERY_KEY='$(cat <new key>.pub)' bash -s" < deploy/server-setup.sh
```

## Secrets

Everything secret lives in `/etc/sports-follow` on the server. Some of it can't be made again: a new
**fan cookie key** signs every fan out of their follows, and a new **push key** stops notifications on
every device until each one subscribes again. So all of it is also kept, sealed, off both the Mac and the
server:

| | Where |
|---|---|
| The API tokens (Sportmonks, Lichess, Resend, Google, …) | `~/.config/sports-follow` on the Mac, sent by `push-secrets.sh` |
| The database password, gateway token, push key and fan cookie key | made by the server, once |
| All of the above, sealed | `/var/backups/sports-follow/secrets.age` on the server, and a copy in the password manager (`backup-secrets.sh`); next to the off-site dumps once they exist |
| The age secret key that opens it | the password manager only |

`seal-secrets.sh` seals for every `age1…` key in `secrets-recipients.txt`. The server only ever has the
public half. `push-secrets.sh` seals again after each change. Download a new copy for the password manager
after any change (`deploy/backup-secrets.sh deploy@<server-ip>`). The file is text, so a secure note
works. Neither script needs `age` on your machine, only `ssh`.

**Production's own OpenRouter key** (you): make a separate key in OpenRouter with its own credit limit,
then save it on the Mac and send it:
```bash
pbpaste > ~/.config/sports-follow/openrouter-key && chmod 600 ~/.config/sports-follow/openrouter-key
deploy/push-secrets.sh deploy@<server-ip>
```
After that, the Mac's development key can be revoked without taking the app down.

**On a new server**, after `server-setup.sh` and before its first deploy, restore from the sealed copy
instead of running `push-secrets.sh`:
```bash
deploy/restore-secrets.sh deploy@<server-ip> sports-follow-secrets-<date>.age
```
It asks for the age secret key, or reads it from a password manager's command line piped in. The key goes
over SSH into the server's memory and is never written to a disk here or there. The secrets go back with
the same owners and modes, and the sealed copy becomes the server's own.

If the new server was deployed before the restore, its database was made with another password, and the
restore says so. With no data in it yet, start that database again (on the server, as `deploy`, in
`/opt/sports-follow`):
```bash
docker compose -f deploy/compose.yaml down && docker volume rm sports-follow_pgdata && deploy/deploy.sh deploy
```

**Keys:** add a line to `secrets-recipients.txt` (a second person, or a spare key), then seal again
(`backup-secrets.sh`). Removing a line doesn't unseal copies made before. If an age secret key leaks,
replace the tokens it could open: `push-secrets.sh` for the API tokens; the server's own secrets are
harder, see the warnings above.

## Deploying

**From GitHub:** Actions → Deploy → Run workflow, with `deploy` (the tip of main) or `rollback`. A deploy
goes ahead only if the CI workflow passed for that commit; then it runs `deploy.sh` on the server and
fetches `/api/health` from outside. It never runs for pull requests: a fork's PR must not reach the deploy
key. Once it's trusted, uncomment `workflow_run` in the workflow so
every commit on main that CI passes deploys by itself.

**By hand:** `ssh deploy@<server-ip> /opt/sports-follow/deploy/deploy.sh deploy` (or `deploy <commit>`,
`rollback`, `status`).

What a deploy does: fetches main from GitHub (a commit that isn't on main is refused), builds the image
on the server and tags it with the commit, runs the migrations on their own, then swaps the containers
and waits until the API is healthy and the worker hasn't crashed. Fans see about two seconds of errors
during the swap.

- **A migration fails:** nothing is replaced; the old version keeps serving.
- **The new version doesn't get healthy:** the previous one comes back by itself, and the deploy fails.
- **It's healthy but wrong:** `rollback` starts the previous deploy's image again. The newer migrations
  stay in the database; if the older code can't run with them, undo them first, while the newer image
  is still live: `docker compose -f deploy/compose.yaml run --rm migrate alembic downgrade <revision>`.

The live image and the previous one are kept; older ones are removed.

### Setting up the GitHub deploys (you, once)

1. A key for GitHub, and the server allowing it to run `deploy.sh` only:
   ```bash
   ssh-keygen -t ed25519 -N '' -C github-actions -f deploy_key
   ssh deploy@<server-ip> "sudo DEPLOY_KEY='$(cat deploy_key.pub)' bash -s" < deploy/server-setup.sh
   ```
2. The server's host key, so the workflow knows it's talking to your server. Check that the fingerprint
   from `ssh-keyscan` matches the one the server reports:
   ```bash
   ssh-keyscan -t ed25519 <server-ip> > known_hosts && ssh-keygen -lf known_hosts
   ssh deploy@<server-ip> ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
   ```
3. In GitHub, Settings → Environments → New environment `production`. Deployment branches: `main` only.
   Then its secrets and variable (or with `gh`, as below):
   ```bash
   gh secret set DEPLOY_SSH_KEY --env production < deploy_key
   gh secret set DEPLOY_KNOWN_HOSTS --env production < known_hosts
   gh secret set DEPLOY_HOST --env production --body <server-ip>
   gh variable set APP_URL --env production --body https://<domain>
   ```
4. Delete `deploy_key` from the Mac: GitHub has it, and a new one is quick to make.

## Alerts

`server-check.sh` runs every 5 minutes as `deploy` and sends an alert when one of these starts, again
every 6 hours while it lasts, and once more when it clears:

| Check | Alerts when |
|---|---|
| Disk | `/`, Docker's or the backups' disk is 80% full |
| Memory | tasks spent more than 10% of the last 5 minutes waiting for memory |
| Out-of-memory killer | it stopped any process since the last check |
| Backups | the newest dump is 26 hours old, or there is none |
| Containers | one isn't running, is unhealthy, or restarted 3 times in an hour |
| Certificate | the one Caddy serves isn't trusted, or expires within 10 days |

**Setting it up** (you): install the ntfy app on your phone and subscribe to a topic with a long random
name (`echo sports-follow-$(openssl rand -hex 12)`). Anyone who knows the name can read and post, so keep
it with your other secrets. Then:
```bash
echo https://ntfy.sh/<topic> > ~/.config/sports-follow/alert-url
curl -d "Sports Follow alerts work" https://ntfy.sh/<topic>   # a push should arrive
deploy/push-secrets.sh deploy@<server-ip>
```
Optionally, a heartbeat for when the whole server is down (the checks can't send anything then): a free
Healthchecks.io check with a 5 minute period, its ping URL in `~/.config/sports-follow/heartbeat-url`,
and `push-secrets.sh` again. Its own alerts can go to the same ntfy topic.

**Setting each one off on purpose** (on the server; each sends an alert, and the plain `check` after it
sends "Cleared"):
```bash
check() { sudo systemd-run --quiet --wait --pipe -p User=deploy -p EnvironmentFile=/etc/sports-follow/alerts.env \
  -p StateDirectory=sports-follow-check "$@" /opt/sports-follow/deploy/server-check.sh; }
check -E DISK_MAX_PCT=1; check                     # disk
check -E MEMORY_PRESSURE_MAX=-1; check             # memory
check -E BACKUP_MAX_HOURS=0; check                 # backups
check -E CERT_MIN_DAYS=365; check                  # certificate
docker run --rm -m 64m --memory-swap 64m sports-follow:latest python -c "x = bytearray(512 * 1024 * 1024)"; check   # out-of-memory killer
docker stop sports-follow-worker-1; check; docker start sports-follow-worker-1; check                               # a container down
for i in 1 2 3; do sudo kill -9 "$(docker inspect -f '{{.State.Pid}}' sports-follow-backup-1)"; sleep 5; done; check  # restarts
```
The checks' log: `sudo journalctl -u sports-follow-check`.

Old images and build cache older than a week are removed early on Sundays (`sports-follow-prune.timer`).

## Operating

On the server, as `deploy`, in `/opt/sports-follow`, with `c() { docker compose -f deploy/compose.yaml "$@"; }`:

| | |
|---|---|
| What's live | `deploy/deploy.sh status` |
| Logs | `c logs -f api worker` (Caddy's certificate work: `c logs caddy`) |
| Restart after a secret changes | `c up -d --force-recreate` |
| A shell in the database | `c exec postgres psql -U sports sports_follow` |
| Registry import now | `c exec worker python -m sports_follow.registry` |
| Sources' spend by lane | `c exec worker python -m sports_follow.usage` |
| Add or remove a player | `c exec -it worker python -m sports_follow.players list` (`add`, `remove`, `restore`: see the README) |
| Backups | `ls /var/backups/sports-follow` |
| Restore a backup | `c stop api worker && c exec -T postgres pg_restore -U sports -d sports_follow --clean --if-exists < /var/backups/sports-follow/<file>.dump && c start api worker` |

Backups stay on the server; the provider's weekly server backup is the copy elsewhere. Off-site dumps
(object storage) are a later step. They should take the sealed secrets (`secrets.age`, next to the
dumps) with them.

A server set up before fan cookies were signed has no cookie key yet: run `deploy/push-secrets.sh
deploy@<server-ip>` once before deploying, or the API stops at startup and says so. The key is kept from
then on; a new one would sign every fan out of their follows.

## Rehearsing locally

The same stack runs on the Mac on other ports, with Caddy's local certificate authority:
```bash
DOMAIN=localhost HTTP_PORT=8080 HTTPS_PORT=8443 SECRETS_DIR=<dir> BACKUP_DIR=<dir> \
  docker compose -p sf-prodtest -f deploy/compose.yaml up -d --build
curl -k https://localhost:8443/api/health
```
`<dir>` needs the same files `install-secrets.sh` writes: `app.env`, `gateway.env`, `postgres.env` and
`files/{sportmonks-token,lichess-token,vapid-private.pem,cookie-secret}`.

The server scripts were rehearsed on an OrbStack Ubuntu 24.04 machine (`orb create ubuntu:noble`) with
SSH and a stand-in for ntfy: the setup and its lockdown, a fail2ban ban, deploys through the restricted
key, a failed migration, a version that doesn't start, rollback, and the alerts. Two weren't possible
there: OrbStack's kernel has no memory pressure figures (the check fell back to free memory, which
these kernels use), and a localhost certificate is never trusted (the expiry test ran against a real one).

The sealed secrets were rehearsed on two fresh machines, each with a `deploy` user and the folders
`server-setup.sh` makes. One server sealed its secrets. A copy went down with `backup-secrets.sh`, and
`restore-secrets.sh` put it on the second server, which had already made its own secrets and database.
Afterwards every file on the second server matched the first in content, mode and owner. The restore
warned about the database, and `docker compose config` read the files as `deploy`. A wrong key changed
nothing and left nothing behind.
