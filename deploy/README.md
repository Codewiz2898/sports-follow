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
backup: pg_dump once a day, 14 kept in /var/backups/sports-follow ──▶ sealed, to object storage hourly
GitHub Actions ──ssh deploy@──▶ deploy.sh (build, migrate, swap, wait for health)
systemd timers: server checks every 5 minutes, off-site backups hourly, Docker clean-up weekly
alloy (Grafana Alloy) ──▶ Grafana Cloud: the server's metrics and the checks'; alert rules there email you
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
| `offsite-backup.sh`, `systemd/sports-follow-offsite.*` | On the server, hourly: seals new dumps and sends them, with the sealed secrets, to object storage; thins out old ones. |
| `fetch-backup.sh` | From any machine: brings a dump back from object storage onto the server, opened, for `pg_restore`. |
| `deploy.sh` | On the server: deploys a commit of main, rolls back, or shows what's live. GitHub Actions runs it. |
| `../.github/workflows/deploy.yml` | Checks that CI passed, then runs `deploy.sh` over SSH, then a health check from outside. |
| `server-check.sh`, `systemd/` | The server checks, whose findings go to Grafana Cloud as metrics; the weekly Docker clean-up. |
| `alloy/` | Grafana Alloy's config: the server's metrics and the checks' findings, to Grafana Cloud every minute. |
| `grafana/` | The alert rules (`rules.json`), and `apply-alerts.sh`, which puts them, an email contact point and the policy into Grafana Cloud. |
| `seed-registry.sh` | From the Mac: copies the player registry (323,000 athletes, 15 MB) instead of an hour of Wikidata queries. |
| `migrate-to-server.sh` | From the Mac: steps 3–6 of the first deploy in one go, with checks before and after ([Moving off the Mac](#moving-off-the-mac)). |
| `backup.sh` | The daily database dump. |

## Size and cost

The whole stack uses about 450 MB of memory at rest; the API is the largest (240 MB for two workers).
A **2 vCPU / 4 GB** server leaves room for the weekly registry import, builds and the chess engine.

It runs on **AWS Lightsail in Sydney** (`ap-southeast-2`): the $24 plan, with 2 vCPU, 4 GB and an 80 GB
SSD.
- **Billing:** an AWS account opened in India is billed in rupees by AWS India. It takes RuPay, Visa and
  Mastercard cards, net banking and UPI. DigitalOcean bills in dollars, and an Indian card was declined
  there.
- **Transfer:** Sydney includes 2 TB a month.
- **CPU:** Lightsail's CPU is burstable. It runs flat out for a while, then slows down. Normal use stays
  well inside that, but watch the CPU graph in Lightsail after launch.
- **Region:** Mumbai would be closest to fans in India, but the AWS organization's service control policy
  denies Lightsail there. Sydney adds a little delay, which barely matters for scores that update every
  8–15 seconds.

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

**1. Create the server** (you), in the Lightsail console, in Sydney (`ap-southeast-2`):
- **Image and plan:** Linux/Unix, OS only, **Ubuntu 24.04 LTS**, on the $24 plan (2 vCPU, 4 GB), with
  dual-stack networking.
- **SSH key:** upload your Mac's `~/.ssh/id_ed25519.pub`.
- **Static IP:** on the Networking tab, attach one. It's free while attached; without it, the address
  changes whenever the server stops.
- **Firewall:** add **HTTPS (TCP 443)** to the IPv4 firewall, and UDP 443 for HTTP/3. Don't restrict them:
  any IPv4 address, `0.0.0.0/0`. SSH (22) and HTTP (80) are open already.
- **Snapshots** (optional): automatic snapshots take one a day, billed per GB stored. The off-site backups
  already cover the data, but a snapshot brings the whole server back faster.

Note the static IP, and check the Mac's key gets in: `ssh ubuntu@<server-ip> true`. If it says
`Permission denied (publickey)`, the server was made with Lightsail's default key instead. Add the Mac's
from the console's browser SSH ("Connect using SSH"), pasting your `~/.ssh/id_ed25519.pub` between the
quotes:
```bash
echo '<the line from ~/.ssh/id_ed25519.pub>' >> ~/.ssh/authorized_keys
```

**2. Set it up** (from the repo on the Mac). Lightsail's first login is `ubuntu`, with sudo:
```bash
ssh ubuntu@<server-ip> "sudo RECOVERY_KEY='$(cat ~/sports-follow-recovery.pub)' bash -s" < deploy/server-setup.sh
```
On a provider whose first login is root, it's `ssh root@<server-ip> "RECOVERY_KEY=… bash -s" < …` instead.

The repo is private, so the first run stops and prints a key for the server to read it with: add it under
[Settings → Deploy keys](https://github.com/Codewiz2898/sports-follow/settings/keys/new) with write access
off. Deploys fetch with it too. (It isn't `DEPLOY_KEY`, which lets GitHub Actions into the server.) Then
run the setup again as `deploy` (root can't log in by then):
```bash
ssh deploy@<server-ip> 'sudo bash -s' < deploy/server-setup.sh
```

Without a domain the app lives at `https://<ip-with-dashes>.sslip.io`, with a real certificate. For
your own domain, point an A record at the server first and pass it on that second run, which is the one
that writes the address: `'sudo DOMAIN=app.example.com bash -s'` (or edit `/opt/sports-follow/deploy/.env`
later).

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

**7. Turn on the alerts, the off-site backups and the GitHub deploys** (below).

### Moving off the Mac

Steps 3–6 in one go, from the repo's root on the Mac with the development stack's database running:
```bash
deploy/migrate-to-server.sh deploy@<server-ip> --check   # checks only; changes nothing
deploy/migrate-to-server.sh deploy@<server-ip>
```
1. **Checks:** the Sportmonks and Lichess tokens, the Mac's registry, and the server's setup. It warns if
   production has no OpenRouter key of its own yet.
2. **Secrets:** `push-secrets.sh`.
3. **First deploy:** `deploy.sh deploy`, run on the server.
4. **Registry:** `seed-registry.sh`.
5. **Checks against the live site:** `/api/health` answers healthy, `/api/credits` names no ESPN (so only
   licensed sources run), and the server's registry has as many athletes as the Mac's.

Each step is one of the scripts above, and running it again is safe.

**Not moved, on purpose:**
- **The Mac's games, results and news.** Much of it comes from ESPN, which only development may use.
- **Its fans and follows.** They're development cookies for `localhost`, which no browser sends to the
  server's address.

The server builds pages again as fans follow. Copying the Mac's cookie or push keys would gain nothing
either: a browser keeps those per address.

## Getting in

Root can't log in over SSH, and nobody can log in with a password. Everyone logs in as `deploy`, which
has sudo without a password (it's in the `docker` group, which is as good as root anyway):

| Key | Can |
|---|---|
| The Mac's (copied from `ubuntu`, or root, by the first setup) | everything; it still opens `ubuntu` too |
| The recovery key (`RECOVERY_KEY`) | everything |
| GitHub Actions' (`DEPLOY_KEY`) | run `deploy.sh` and nothing else: no shell, no forwarding |
| Lightsail's default key, if `ubuntu` had it (copied like the Mac's) | everything. Its private half can be downloaded from the AWS account; to drop it, remove its line from `~deploy/.ssh/authorized_keys` |
| Lightsail's browser SSH ("Connect using SSH" in the console) | the last resort, if no key works: it logs in as `ubuntu` with a key of its own |

Try the recovery key once it's in: `ssh -i <recovery key> deploy@<server-ip> true`. Also open Lightsail's
browser SSH once after the setup, to check it still gets in.

fail2ban bans an address for an hour after 5 failed logins in 10 minutes, and then even a right key can't
get in from there: wait, use another network, or Lightsail's browser SSH (`sudo fail2ban-client status sshd`,
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

## Off-site backups

Every hour, `offsite-backup.sh` sends two things to S3-compatible object storage with rclone: the daily
dumps that aren't there yet, and the sealed secrets. It runs as `deploy` from `sports-follow-offsite.timer`.
Each dump is sealed with age for the keys in `secrets-recipients.txt` before it leaves, so the storage
never holds fan data it can read. Without a key there, nothing is sent, and the alert says so.

What's kept off-site:
- every dump of the last 30 days;
- the first dump of each month, for 12 months;
- always the newest 7.

Other files in the bucket are left alone.

**Setting it up** (you):
1. **The bucket:** make it private, with another company than the server's, so one account problem can't
   take both. Cloudflare R2 and Backblaze B2 each give 10 GB free, which is plenty: a dump is a few MB to a
   few hundred.
2. **A key for that bucket only:** it needs to read, write and delete objects. On R2 that's an "Object Read
   & Write" API token for the bucket.
3. **The settings:** on the Mac, `~/.config/sports-follow/offsite.env` (`chmod 600`):
   ```bash
   RCLONE_CONFIG_OFFSITE_TYPE=s3
   # Cloudflare (R2), DigitalOcean (Spaces), AWS, or Other (Backblaze B2 and the rest)
   RCLONE_CONFIG_OFFSITE_PROVIDER=Cloudflare
   # R2: https://<account id>.r2.cloudflarestorage.com, B2: https://s3.<region>.backblazeb2.com,
   # Spaces: https://<region>.digitaloceanspaces.com
   RCLONE_CONFIG_OFFSITE_ENDPOINT=https://<account id>.r2.cloudflarestorage.com
   RCLONE_CONFIG_OFFSITE_REGION=auto
   RCLONE_CONFIG_OFFSITE_ACCESS_KEY_ID=<key id>
   RCLONE_CONFIG_OFFSITE_SECRET_ACCESS_KEY=<secret>
   # The key can't make buckets, and doesn't need to
   RCLONE_CONFIG_OFFSITE_NO_CHECK_BUCKET=true
   OFFSITE_PATH=<bucket>
   ```
4. **Send it and check it:** `deploy/push-secrets.sh deploy@<server-ip>` sends it, sealed with the other
   secrets. Then, on the server:
   ```bash
   sudo systemctl start sports-follow-offsite && sudo journalctl -u sports-follow-offsite -n 3 -o cat
   deploy/offsite-backup.sh list
   ```

**Getting a dump back**, onto this server or a new one:
```bash
deploy/fetch-backup.sh deploy@<server-ip>   # the newest; or a name from offsite-backup.sh list
```
It asks for the age secret key, or reads it piped in. The key goes over SSH into the server's memory only.
The opened dump lands in `/var/backups/sports-follow/fetched/`. Loading it **replaces the database**, so
the API and worker stop meanwhile. The migrations bring an older dump up to the code. On the server, as
`deploy`, in `/opt/sports-follow`:
```bash
c() { docker compose -f deploy/compose.yaml "$@"; }
c stop api worker && c exec -T postgres pg_restore -U sports -d sports_follow --clean --if-exists \
  < /var/backups/sports-follow/fetched/<file>.dump && c run --rm migrate && c start api worker
```

**A new server from nothing**, with the Mac gone, takes four steps:
1. `server-setup.sh`.
2. `restore-secrets.sh`, from the password manager's sealed copy, or `secrets.age` downloaded from the
   bucket's web page. That brings back `offsite.env` too.
3. `deploy.sh deploy`.
4. `fetch-backup.sh`, then the load above.

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

Alerts come by **email from Grafana Cloud**, on its free tier.
- **Alloy:** on the server, Grafana Alloy (`alloy/config.alloy`) sends the server's own metrics every
  minute. It comes from Grafana's apt repository and starts once `grafana.env` is there.
- **The checks:** Alloy also sends what `server-check.sh` sees every 5 minutes, from its `metrics.prom`:
  backups, off-site backups, containers and the certificate.
- **The rules:** they live in `grafana/rules.json` and run in Grafana Cloud, so they still work when the
  server is down. That's one of the things they catch.

Each one emails when it starts, again every 6 hours while it lasts, and "Resolved" when it clears:

| Rule | Emails when |
|---|---|
| The server stopped sending metrics | nothing has arrived for 10 minutes: the server is down or unreachable, or Alloy stopped |
| The server checks stopped running | `server-check.sh` hasn't run for 15 minutes |
| Disk | any disk is over 80% full for 10 minutes |
| Memory | for 10 minutes, tasks waited for memory over 10% of the time, or under 5% of memory was free |
| Out-of-memory killer | it stopped any process |
| CPU held back | for 30 minutes, Lightsail withheld over 20% of CPU time: its burst allowance is used up |
| Backups | the newest dump is 26 hours old, or there is none |
| Off-site backups | once set up: the last hourly copy that worked is 26 hours old, or none worked in the first 2 hours |
| Docker, containers | Docker, or one of the app's containers, isn't up for 10 minutes; or a container restarted 3 times in an hour |
| Certificate | the one Caddy serves isn't trusted for 10 minutes, or expires within 10 days |

**Setting it up** (you, once):
1. **Sign up** at grafana.com (free) and create a stack.
2. **Metrics:** on the grafana.com portal, open your stack → **Prometheus** → **Details** for the remote
   write URL and username. Create an access policy token with `metrics:write`. On the Mac, write
   `~/.config/sports-follow/grafana.env`:
   ```
   GRAFANA_PROM_URL=https://prometheus-prod-…grafana.net/api/prom/push
   GRAFANA_PROM_USER=<the number>
   GRAFANA_PROM_TOKEN=<the token>
   ```
3. **Rules:** in the stack's Grafana, go to **Administration → Users and access → Service accounts**. Make
   `sports-follow-alerts` with the Admin role, then a token for it. On the Mac, write
   `~/.config/sports-follow/grafana-admin.env`:
   ```
   GRAFANA_URL=https://<stack>.grafana.net
   GRAFANA_SA_TOKEN=<the token>
   ALERT_EMAIL=<where alerts go>
   ```
4. **Turn it on:** run `chmod 600 ~/.config/sports-follow/grafana*.env`, then:
   ```bash
   deploy/push-secrets.sh deploy@<server-ip>   # grafana.env to the server; Alloy starts
   deploy/grafana/apply-alerts.sh --test       # the rules, the email contact point and policy, and a test email
   ```

Run `apply-alerts.sh` again after changing `rules.json`. Its notification policy replaces the stack's
whole policy tree. `grafana-admin.env` only loads rules, so it stays on the Mac. To look at alerts or
silence them, use the stack's Grafana.

**Setting one off on purpose** (on the server, as `deploy`):
- **A late backup:** this runs the checks once as if there were no backups.
  ```bash
  sudo systemd-run --quiet --wait -p User=deploy -p StateDirectory=sports-follow-check -E BACKUP_DIR=/nonexistent /opt/sports-follow/deploy/server-check.sh
  ```
  "The database backup is late" arrives within about 3 minutes. The timer's next normal run puts it
  right, and "Resolved" follows within about 10.
- **The server going quiet:** `sudo systemctl stop alloy`. The email comes after about 11 minutes. Then
  `sudo systemctl start alloy`.

`server-check.sh` can still push to an ntfy topic as well, but the emails cover the same ground. For that,
save the topic's URL in `~/.config/sports-follow/alert-url` and run `push-secrets.sh`. The checks' log:
`sudo journalctl -u sports-follow-check`. Alloy's: `sudo journalctl -u alloy`.

Old images and build cache older than a week are removed early on Sundays (`sports-follow-prune.timer`).

## Operating

On the server, as `deploy`, in `/opt/sports-follow`, with `c() { docker compose -f deploy/compose.yaml "$@"; }`:

| | |
|---|---|
| What's live | `deploy/deploy.sh status` |
| Logs | `c logs -f api worker` (Caddy's certificate work: `c logs caddy`) |
| One request or job's lines | `c logs api worker \| grep <id>`: JSON lines, each with the `rid` of its request or job; a request's id is in its answer's `X-Request-ID` header, and the jobs it queued log under it too |
| Restart after a secret changes | `c up -d --force-recreate` |
| A shell in the database | `c exec postgres psql -U sports sports_follow` |
| Registry import now | `c exec worker python -m sports_follow.registry` |
| Sources' spend by lane | `c exec worker python -m sports_follow.usage` |
| Add or remove a player | `c exec -it worker python -m sports_follow.players list` (`add`, `remove`, `restore`: see the README) |
| Backups | `ls /var/backups/sports-follow` |
| Restore a backup | `c stop api worker && c exec -T postgres pg_restore -U sports -d sports_follow --clean --if-exists < /var/backups/sports-follow/<file>.dump && c run --rm migrate && c start api worker` (off-site ones: [Off-site backups](#off-site-backups)) |
| Off-site backups | `deploy/offsite-backup.sh list`; run now: `sudo systemctl start sports-follow-offsite`; log: `sudo journalctl -u sports-follow-offsite` |

The daily dumps stay on the server for 14 days. Off-site, they're sealed and kept for longer (see
[Off-site backups](#off-site-backups)), next to the sealed secrets. Lightsail's automatic snapshots, if
they're on, are one more copy.

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

The off-site backups were rehearsed on two more machines like that, with Ubuntu's rclone 1.60 and a
shared folder standing in for the bucket (rclone's `local` backend; the S3 settings weren't tried). On
the first machine:
- The Postgres and backup containers made a dump of 5,000 marked rows.
- The hourly service refused to send while `secrets-recipients.txt` had no key.
- With a key, it sent the sealed dump and secrets; no file in the bucket held a marked row.
- A second run sent nothing.
- With 13 months of daily dumps planted, it thinned them out to exactly the 30 days, the 12 monthly
  firsts and nothing else.
- The off-site alert behaved as described.

On the second machine:
- Its secrets came back from the bucket's `secrets.age`.
- `fetch-backup.sh` brought the newest dump back at its exact size.
- `pg_restore` gave a database with the same 5,000 rows and checksum.
- A wrong key and a bad dump name were refused, with nothing left behind.

The Grafana alerts were rehearsed on another such machine, with Alloy 1.20.1 from Grafana's repository.
Grafana Cloud was played by a local Prometheus with its remote-write receiver, Grafana 12 and Mailpit
catching the email.
- Alloy was skipped until `grafana.env` existed, then sent about 500 series, including the checks'
  `metrics.prom`. `install-secrets.sh` installs that file and restarts Alloy.
- `apply-alerts.sh` loaded the 14 rules, twice, all evaluating without errors. `--test` sent a test email.
- With no dump at all, "The database backup is late" emailed within 2 minutes. A fresh dump gave
  "Resolved" 5 minutes later.
- The app's containers and the certificate (this machine had neither) emailed after their 10 minutes.
- With Alloy stopped, "The server stopped sending metrics" emailed after 10½ minutes and resolved once it
  was back. Meanwhile the other alerts kept their state rather than sending false "Resolved" mails.

Not tried there: the real Grafana Cloud, the kernel's memory-pressure figures (the machine has none), and
`server-setup.sh` itself.
