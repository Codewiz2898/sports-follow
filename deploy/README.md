# Deploying Sports Follow

One Ubuntu server runs everything with Docker Compose. Caddy terminates HTTPS and gets its certificate
by itself; the API serves the web app, so pages, API, live streams, the service worker and the Android
app's asset links share one origin.

```
            443 / 80
phone ─────────────────▶ caddy ──▶ api (uvicorn, 2 workers) ──┬──▶ postgres
                                                              ├──▶ redis ◀── worker (arq, Stockfish)
                                                              ├──▶ cache ◀────┤  the sources' answers, shared
                                   gateway (llm-providers) ◀──┴───────────────┘
                                        └──▶ OpenRouter
backup: pg_dump once a day, 14 kept in /var/backups/sports-follow ──▶ sealed, to object storage hourly
GitHub Actions ──your approval──ssh deploy@──▶ deploy.sh (build, migrate, swap, wait for health)
systemd timers: server checks every 5 minutes, off-site backups hourly, Docker clean-up weekly
alloy (Grafana Alloy) ──▶ Grafana Cloud: the server's metrics and the checks'; alert rules there email you
```

Only Caddy is published. Postgres, the two Redis (`redis`: jobs, locks, budgets, cards; `cache`: what
the data sources answered), the gateway and the API talk on the compose network. The
build runs licensed sources (`settings.env`): Sportmonks football and cricket, Lichess, PandaScore, PUBG and
YouTube, plus OpenDota by choice (BHA-208). Never ESPN, which is for development.

| File | What |
|---|---|
| `compose.yaml` | The stack. `x-app` is the one image the API, worker and migration share (`../Dockerfile`). |
| `caddy/Caddyfile` | HTTPS for `$DOMAIN`; live streams pass through unbuffered and uncompressed. |
| `gateway/` | The llm-providers gateway image, pinned to a tag and built from its own lockfile, and its config: OpenRouter only, this app only, $5 a day. |
| `settings.env` | Non-secret settings for the public build. |
| `server-setup.sh` | Server setup: updates, firewall, swap, Docker, the `deploy` user, SSH keys only, fail2ban, the timers, this repo in `/opt/sports-follow`. Safe to run again. |
| `push-secrets.sh` | From the Mac: copies the API tokens to the server without printing them (runs `install-secrets.sh` there). |
| `seal-secrets.sh`, `secrets-recipients.txt` | On the server: seals every secret into `secrets.age` for the age keys listed, or puts a sealed copy back. |
| `backup-secrets.sh`, `restore-secrets.sh` | From any machine: download the sealed copy for the password manager, or put it back on a new server. |
| `offsite-backup.sh`, `systemd/sports-follow-offsite.*` | On the server, hourly: seals new dumps and sends them, with the sealed secrets, to object storage; thins out old ones. |
| `fetch-backup.sh` | From any machine: brings a dump back from object storage onto the server, opened, for `pg_restore`. |
| `deploy.sh` | On the server: deploys a commit of main, rolls back, or shows what's live. GitHub Actions runs it. |
| `../.github/workflows/deploy.yml` | After CI passes on main: waits for your approval (in a private repository, for you to start it), then runs `deploy.sh` over SSH, then a health check from outside that the site runs that commit. |
| `deploy-summary.sh`, `../.github/workflows/deploy-summary.yml` | What a waiting (or ready) deploy brings, on its run's page; sent when it starts waiting and every morning while it does. |
| `server-check.sh`, `systemd/` | The server checks, whose findings go to Grafana Cloud as metrics; the weekly Docker clean-up. |
| `alloy/` | Grafana Alloy's config: the server's metrics and the checks' findings, to Grafana Cloud every minute. |
| `grafana/` | The alert rules (`rules.json`), and `apply-alerts.sh`, which puts them, an email contact point and the policy into Grafana Cloud. |
| `seed-registry.sh` | From the Mac: copies the player registry (323,000 athletes, 15 MB) instead of an hour of Wikidata queries. |
| `migrate-to-server.sh` | From the Mac: steps 3–6 of the first deploy in one go, with checks before and after ([Moving off the Mac](#moving-off-the-mac)). |
| `backup.sh` | The daily database dump. |

## Size and cost

The whole stack uses about 450 MB of memory at rest; the API is the largest (240 MB for two workers).
A **2 vCPU / 4 GB** server leaves room for the weekly registry import, builds and the chess engine.
The sources' cache (`cache` in `compose.yaml`) may grow to 256 MB, then drops the least recently used
response; `CACHE_REDIS_MAXMEMORY` in `deploy/.env` changes that (`512mb`). On 18 October the server had
2.3 GB available, and the main Redis used 4 MB.

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
Keep the `.pub` file for step 2. Save the private half in the password manager **as one line of base64**:
a pasted key loses its line breaks in a password field, and then can't be read back.
```bash
base64 < ~/sports-follow-recovery | tr -d '\n' | pbcopy   # then paste it as the entry's password
```
After step 2, copy it back out of the password manager and check that copy opens the server. This prints
the key's fingerprint and logs in once; the test file and the clipboard are cleared:
```bash
umask 077; pbpaste | base64 -d > /tmp/recovery-test && ssh-keygen -lf /tmp/recovery-test && ssh -o IdentitiesOnly=yes -i /tmp/recovery-test deploy@<server-ip> 'echo the saved copy works'; rm -f /tmp/recovery-test; pbcopy < /dev/null
```
Only then delete the private half from the Mac (`rm ~/sports-follow-recovery`).

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

The server reads the repo with a key of its own, so the first run stops and prints it: add it under
[Settings → Deploy keys](https://github.com/Codewiz2898/sports-follow/settings/keys/new) with write access
off. Deploys fetch with it too. The repo is public, but the key stays: it works either way, and nothing
changes if the repo goes private again. (It isn't `DEPLOY_KEY`, which lets GitHub Actions into the
server.) Then run the setup again as `deploy` (root can't log in by then):
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
opens, search finds players, and `/credits` lists Sportmonks and Lichess only, with PandaScore and PUBG
once their tokens are sent ([Esports](#secrets), below).

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
5. **Checks against the live site:** `/api/health` answers healthy, `/api/credits` names no ESPN (so no
   development-only source runs), and the server's registry has as many athletes as the Mac's.

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
| The recovery key (`RECOVERY_KEY`) | everything; its only copy is in the password manager, as base64 (step 0) |
| GitHub Actions' (`DEPLOY_KEY`) | run `deploy.sh` and nothing else: no shell, no forwarding |
| Lightsail's default key, if `ubuntu` had it (copied like the Mac's) | everything, until you drop it. Its private half can be downloaded from the AWS account. To drop it, remove its line from `~deploy/.ssh/authorized_keys`, as production has. Browser SSH still gets in without it |
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
| The API tokens (Sportmonks, Lichess, Resend, Google, …) and the admin password's hash | `~/.config/sports-follow` on the Mac, sent by `push-secrets.sh` |
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

**The admin panel** (you, once): `https://<domain>/admin` turns sports and sources off and on, sets what
each source may spend, and looks after accounts, on the running app. It opens with a password of its own
and a 6-digit code from an authenticator app (Google Authenticator, 1Password, Authy…). Only the
password's hash goes to the server, with the app's key; without a password the panel isn't there, and
without a key it asks for the password alone and says so on every page. Make both on the Mac, from a
checkout at the latest main (keep the password in the password manager; `code` prints a key to add to
the app, and keeps it once the app's code matches), then send them. No restart: the API reads them on
each sign-in, and a new password or key signs every admin out.
```bash
.venv/bin/python -m sports_follow.admin password     # writes ~/.config/sports-follow/admin-password
.venv/bin/python -m sports_follow.admin code         # adds the authenticator app's key to it
deploy/push-secrets.sh deploy@<server-ip>
```
A sign-in that works is emailed ("Someone signed in to the admin panel", with how many in the last 5
minutes; another within those 5 minutes sends no email of its own), and so are 10 wrong ones in an hour; the rules are in `grafana/app-rules.json`: after changing them, `deploy/grafana/apply-alerts.sh`.
If a sign-in wasn't you, make a new password and key at once, and send them: that signs every admin out.
The first time, the containers need the file mounted: `docker compose -f deploy/compose.yaml up -d
--force-recreate api worker` after the deploy that brings the panel. Wrong passwords are limited for
everyone together (30 an hour), so someone guessing can lock you out for the rest of the hour; to get in
sooner, clear the count:
`docker compose -f deploy/compose.yaml exec redis sh -c 'redis-cli --scan --pattern "admin-fails:*" | xargs -r redis-cli del'`.

**Linear's plan on the admin panel** (you, once): the panel's Sources tab shows which plan the Linear
workspace is on and how many issues it holds against the Free plan's 250, past which Linear refuses new
issues. It reads them with a key that can only read: in Linear, Settings › Account › Security & access,
make a personal API key with the Read permission alone and access to all teams, copy it, then save it on
the Mac and send it. No restart: the API reads the key when the panel next asks, and each API process
asks Linear at most once an hour. Without a key the card says how to add one, and the rest of the panel doesn't need it.
```bash
pbpaste > ~/.config/sports-follow/linear-key && chmod 600 ~/.config/sports-follow/linear-key
deploy/push-secrets.sh deploy@<server-ip>
```

**Esports** (you, once): `settings.env` runs PandaScore (team titles) and PUBG (PUBG PC), and each runs
only with its token on the server. Save them on the Mac as `~/.config/sports-follow/pandascore-token` and
`pubg-token` (PandaScore's free plan is enough; PUBG's at developer.pubg.com), then send them and restart:
```bash
deploy/push-secrets.sh deploy@<server-ip>
ssh deploy@<server-ip> 'cd /opt/sports-follow && docker compose -f deploy/compose.yaml up -d --force-recreate api worker'
```
Then the pros go into search. PUBG's come by themselves: the worker reads them from its tournament index
every six hours, the first time within ten minutes of starting. PandaScore's come with the weekly import
(Mondays), or now, in an hour or two (paced, so live series keep their share of the hourly 1,000):
`c exec worker python -m sports_follow.registry esports` (see [Operating](#operating) for `c`).
`/credits` then lists PandaScore, PUBG and OpenDota, and searching a pro's handle ("TenZ") finds them.

**The feed's videos** (you, once): the curator reads YouTube with an API key of a Google Cloud project that has
the YouTube Data API v3 turned on (the Firebase project's will do: APIs & Services, enable "YouTube Data API v3",
then Credentials, Create credentials, API key, restricted to that API). Save it on the Mac as
`~/.config/sports-follow/youtube-key` (`chmod 600`), then send it and restart the curator:
```bash
deploy/push-secrets.sh deploy@<server-ip>
ssh deploy@<server-ip> 'cd /opt/sports-follow && docker compose -f deploy/compose.yaml up -d --force-recreate api worker curator'
```
(The API and the worker too: each process reads the key once, for the admin panel and the metrics.)
`settings.env` already lists `youtube`; without the key the admin panel's Sources tab says "No token on the
server" under Feed, and nothing else changes. Its free quota is 10,000 units a day; the panel shows what's spent.

**Clips** (you, once): clips published from the Mac app live in the Cloudflare R2 bucket `slam-clips`
(`settings.env` names it, its endpoint and its public address). Its key is an R2 API token (Cloudflare dashboard,
R2, Manage API tokens, Create: Object Read & Write, `slam-clips` only), saved on the Mac as
`~/.config/sports-follow/clips-key`: the Access Key ID, then the Secret Access Key, a line each (`chmod 600`).
Send it and restart the API and the worker, as for the esports tokens above. Then make the Mac's publishing key
on the server: `c exec api python -m sports_follow.clips key "Bharath's Mac"` (shown once; `keys` lists them,
`revoke <id>` stops one). Without the bucket's key, publishing answers "not set up" and the clips feeds are empty.

**Android notifications** (you, once): the Android app's pushes go through Firebase Cloud Messaging with
a service-account key of the app's Firebase project (README.md, "Android app", says how to make it). Save
it on the Mac as `~/.config/sports-follow/fcm-service-account.json` (`chmod 600`), then send it and
restart the API and the worker, as for the esports tokens above. `https://<domain>/api/push` then answers
`"fcm": true`. Without it the app's notifications are off and nothing else changes. To rotate it, make a
new key in the Firebase console, send it, restart, then delete the old key there.

**Without an OpenRouter key**, set `SPORTS_FOLLOW_AGENT=0` in `settings.env` and deploy: pages come from
the sources, Wikidata and Wikipedia alone (scores, games, stats, live and alerts, profiles), and a player
with no English article has no background. With the key but out of credit, pages work too. Players no
live source has get no page unless `SPORTS_FOLLOW_RESEARCH=1` (the agent then writes them whole, offered as
AI research); pages the agent built before stay as they were, no longer updated.

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
1. **The bucket**, with another company than the server's, so one account problem can't take both.
   Production uses **Backblaze B2**: its 10 GB free tier needs no card. A dump is about 17 MB, so 30 days
   plus 12 monthly firsts come to about 0.7 GB. (Cloudflare R2 also gives 10 GB free, but needs a card on
   file to turn on.) In B2, under **Buckets → Create a Bucket**:
   - **Files:** Private.
   - **Object Lock:** off, so old dumps can be deleted.
   - **Lifecycle Settings:** "Keep only the last version of the file". B2 keeps every old version by
     default, so deleted dumps and the hourly copies of `secrets.age` would pile up hidden.
   - **Endpoint:** note the bucket's, `s3.<region>.backblazeb2.com`.
2. **A key for that bucket only**, to read, write and delete objects. In B2, under **Application Keys →
   Add a New Application Key**: the bucket only, Read and Write. **Not the Master Application Key:** B2's
   S3 interface refuses it ("Malformed Access Key Id"). Its ID is the short 12-character one; an
   application key's ID is about 25 characters, and the key itself about 31, starting `K`. (On R2: an
   "Object Read & Write" API token for the bucket.)
3. **The settings:** on the Mac, `~/.config/sports-follow/offsite.env` (`chmod 600`):
   ```bash
   RCLONE_CONFIG_OFFSITE_TYPE=s3
   # Other (Backblaze B2 and the rest), Cloudflare (R2), DigitalOcean (Spaces), or AWS
   RCLONE_CONFIG_OFFSITE_PROVIDER=Other
   # B2: https://s3.<region>.backblazeb2.com, R2: https://<account id>.r2.cloudflarestorage.com,
   # Spaces: https://<region>.digitaloceanspaces.com
   RCLONE_CONFIG_OFFSITE_ENDPOINT=https://s3.<region>.backblazeb2.com
   # B2: the region in the endpoint (us-east-005, …); R2: auto
   RCLONE_CONFIG_OFFSITE_REGION=<region>
   RCLONE_CONFIG_OFFSITE_ACCESS_KEY_ID=<key id>
   RCLONE_CONFIG_OFFSITE_SECRET_ACCESS_KEY=<secret>
   # The key can't make buckets, and doesn't need to
   RCLONE_CONFIG_OFFSITE_NO_CHECK_BUCKET=true
   OFFSITE_PATH=<bucket>
   ```
   Before sending it, the AWS CLI can check the key from the Mac, read-only:
   `aws s3api list-objects-v2 --bucket <bucket> --endpoint-url <endpoint> --region <region>`, with the key
   in `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`. A key limited to one bucket can't list the others;
   that's fine.
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

**From GitHub, after each merge:** every commit on main that CI passes starts a Deploy run, named after
the commit, which waits for your approval at the `production` environment. GitHub notifies you, and its
phone app can approve it; so can the run's page (**Review deployments** → **Approve and deploy**). Approving deploys that
exact commit; the run then checks from outside that `/api/health` names it. Leaving it changes nothing on
the site.

- **Only the newest waits.** When the next commit passes CI, its run cancels the one waiting, so approving
  deploys everything merged so far. A run for a commit a newer one has replaced (an old CI run re-run)
  stops before it waits. A deploy that's running is never stopped: the next run waits behind it, then for
  approval.
- **What it brings** is on the run's page, above the approval button (`deploy-summary.sh`). It starts
  with what to look at before approving: a database migration (a rollback leaves it in place), changed
  secrets scripts (push a new key first), server changes the deploy applies (`compose.yaml`,
  `settings.env`, the image, Caddy's config, the backup script), a step it doesn't take
  (`apply-alerts.sh`, `server-setup.sh`, restarting Alloy for its environment), and Android changes,
  which reach phones with the next app build instead. Then the changes since the live commit, each
  linked and with what it touches (website, API and worker, data sources, database, …), the BHA issues
  they close, and whether CI passed. The first deploy to wait since the last one went out is also sent
  to you at once, and every morning at 09:00 (India) whatever is waiting then is (`deploy-summary.yml`;
  Actions → Deploy summary → Run workflow sends it now). Several merges in a day make one waiting
  deploy, one message when it starts and the morning's.
- **Without a required reviewer** on `production` (in a public repository), nothing deploys by itself: the
  run fails and says so.
- **Claude sessions never approve.** `gh` there runs as you, so GitHub would count their approval as yours; a hook
  (`.claude/hooks/deploy_gate.py`) blocks approving or rejecting a deploy, starting or re-running a Deploy run,
  changing the environment, and running `deploy.sh deploy` or `rollback`. A session sends you the run's link.

**In a private repository** GitHub doesn't hold a run for a required reviewer (only on Enterprise), so
nothing waits, and starting the run is your approval. Each merge's run puts what it brings on its page,
with **Before you deploy** in place of Before you approve, and stops there. The first change since what's
live is sent to you at once, and every morning whatever is ready then: main's newest commit that CI passed,
if it isn't live. To deploy it, Actions → Deploy → Run workflow, with the commit the summary names (left
blank, it deploys the tip of main, which may have more). That run deploys at once and says what it brought.
Before turning a repository private, cancel any deploy still waiting for approval: GitHub may stop holding it.

**From GitHub, by hand:** Actions → Deploy → Run workflow, with `deploy` and a commit on main (blank: the
tip) or `rollback`. In a public repository it waits for your approval too. A deploy goes ahead only if
CI passed for that commit on main. A run by hand cancels a deploy waiting after CI (a rollback would
otherwise queue behind it); in a public repository, re-run that one later to deploy it. One catch: a
rollback queued behind a deploy that's running is replaced if a merge's deploy queues too (GitHub keeps
one run waiting per group), so after such a merge check that the rollback is still there. Deploys never
run for pull requests: a fork's PR must not reach the deploy key.

**By hand:** `ssh deploy@<server-ip> /opt/sports-follow/deploy/deploy.sh deploy` (or `deploy <commit>`,
`rollback`, `status`).

What a deploy does: fetches main from GitHub (a commit that isn't on main is refused), builds the image
on the server and tags it with the commit, runs the migrations on their own, then swaps the containers
and waits until the API is healthy and the worker hasn't crashed. Then Caddy reloads
`caddy/Caddyfile` (nothing happens if it's unchanged, and HTTPS doesn't drop if it has), and the backup
container restarts if `backup.sh` changed. Fans see about two seconds of errors during the swap. The
old worker's jobs (a live poll, a card build) stop and free their locks within five seconds, and the
new worker starts them again as soon as it's up.

- **A migration fails:** nothing is replaced; the old version keeps serving.
- **The new version doesn't get healthy, or Caddy refuses its Caddyfile:** the previous one comes back
  by itself, with its Caddyfile and backup script, and the deploy fails. Caddy keeps serving its old
  config until then.
- **It's healthy but wrong:** `rollback` starts the previous deploy's image again, with its Caddyfile
  and backup script. The newer migrations stay in the database; if the older code can't run with them,
  undo them first, while the newer image is still live:
  `docker compose -f deploy/compose.yaml run --rm migrate alembic downgrade <revision>`.

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
5. **Approval:** in the `production` environment, tick **Required reviewers** and add yourself. Leave
   **Prevent self-review** unticked: GitHub counts a merge you make as yours, so you couldn't approve it.
   (A private repository has no such setting off Enterprise: you deploy by hand, as above.)
6. **The summary's way to you**, as repository secrets (Settings → Secrets and variables → Actions), not the
   environment's, which would make it wait for approval too. Either or both:
   - **Email, through Resend** (the sign-in codes' service, from `deploys@` the same domain): make a key
     with sending access only (Resend → API Keys), then
     ```bash
     gh secret set RESEND_KEY            # paste the key
     gh secret set DEPLOY_SUMMARY_TO --body <your address>
     ```
   - **A push through ntfy**, with a button that opens the approval: `gh secret set DEPLOY_SUMMARY_URL
     --body https://ntfy.sh/<a topic only you know>`, and subscribe to the topic in ntfy's phone app.

   Check it with Actions → Deploy summary → Run workflow while a deploy is waiting (or ready).

## Alerts

Alerts come by **email from Grafana Cloud**, on its free tier.
- **Alloy:** on the server, Grafana Alloy (`alloy/config.alloy`) sends the server's own metrics every
  minute. It comes from Grafana's apt repository and starts once `grafana.env` is there.
- **The checks:** Alloy also sends what `server-check.sh` sees every 5 minutes, from its `metrics.prom`:
  backups, off-site backups, containers and the certificate.
- **The rules:** they live in `grafana/rules.json` and run in Grafana Cloud, so they still work when the
  server is down. That's one of the things they catch.
- **The uptime check:** Grafana's Synthetic Monitoring asks `https://<domain>/api/health` every minute
  from two of its probes, nearest to India first, as a fan's phone would. It catches what the server
  can't see from inside: DNS, Caddy, the certificate or the firewall. That's at most 89,280 checks a
  month, within the free tier's 100,000.

Each one emails when it starts, again every 6 hours while it lasts, and "Resolved" when it clears:

| Rule | Emails when |
|---|---|
| The server stopped sending metrics | nothing has arrived for 10 minutes: the server is down or unreachable, or Alloy stopped |
| Fans can't reach the app | for 3 minutes, no probe outside the server got a healthy answer from `/api/health` |
| The uptime check stopped reporting | no results from it for 20 minutes (it also says so until it's set up) |
| The server checks stopped running | `server-check.sh` hasn't run for 15 minutes |
| Disk | any disk is over 80% full for 10 minutes |
| Memory | for 10 minutes, tasks waited for memory over 10% of the time, or under 5% of memory was free |
| Out-of-memory killer | it stopped any process |
| CPU held back | for 30 minutes, Lightsail withheld over 20% of CPU time: its burst allowance is used up |
| Backups | the newest dump is 26 hours old, or there is none |
| Off-site backups | once set up: the last hourly copy that worked is 26 hours old, or none worked in the first 2 hours |
| Docker, containers | Docker, or one of the app's containers, isn't up for 10 minutes; or a container restarted 3 times in an hour |
| Certificate | the one Caddy serves isn't trusted for 10 minutes, or expires within 10 days |

And on the app's own metrics (`grafana/app-rules.json`, from `sports_follow/metrics.py`; "Traces and
logs" below), which catch the app working badly while the server is fine. These have nothing to say
when there's nothing to measure, such as no push, and stay quiet. The live games, the queue and unsent
moments are read every minute, 0 or not: if the worker goes quiet, those keep their state and "The
worker stopped reporting" says why. Counts start at 0 when a process starts (`metrics.seed`), so the
first failures after a deploy count:

| Rule | Emails when |
|---|---|
| The worker stopped reporting | no worker metrics for 10 minutes: it's stuck or down, or `otlp.env` stopped working |
| The API is failing requests | over 3 server errors (5xx) in 10 minutes |
| The API is slow | for 10 minutes, the slowest 5% of requests take over 3 s (once there are 20 requests in 10 minutes) |
| A source keeps failing | for 5 minutes, over half of a source's requests fail with a server error or no answer, at least 5 of them |
| A source keeps telling us to slow down | it had us paused (429) over half of the last 15 minutes (given 10 minutes of readings) |
| A source's allowance is nearly used | over 90% of a window's allowance is spent, or the source says under 10% of its hour is left |
| A live game isn't being polled | for 2 minutes, a game hasn't been polled for three of its rounds (at least 3 minutes; Liquipedia's 15) |
| Jobs are waiting for the worker | for 5 minutes, a job has waited over 2 minutes for a slot |
| Pushes aren't going out | for 5 minutes, a moment found over 3 minutes ago hasn't been sent |
| Pushes are failing | over 20% of the last 30 minutes' pushes failed (unsubscribed devices aside), at least 3 |
| Player pages keep failing to build, or refreshes do | 3 builds failed in an hour, or over half the refreshes (at least 5) |
| Someone signed in to the admin panel | a sign-in to `/admin` worked in the last 5 minutes (it says how many): if it wasn't you, see "The admin panel" |
| Someone is guessing at the admin sign-in | 10 wrong admin passwords or codes in an hour (at 30 the sign-in waits out the hour) |
| The feed has kept nothing for a day | for 30 minutes, no video has been kept from any channel or moment search in the last 24 hours, given a day of the curator's readings (its counts start at 0 while a video source runs) |
| The feed's judge is failing | over 5 candidates of one kind the judge couldn't answer for in an hour (out of credit, timeout, invalid output, declined, unavailable; not the model turned off) |
| A sport's followed players have no videos | for a day (of the curator's readings), none of a sport's followed players had a servable video published in the last 7 days |

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
4. **Uptime check:** in the stack's Grafana, open **Testing & synthetics → Synthetics** and click **Get
   started**, once. `apply-alerts.sh` then makes the check with the same token. It reads the app's address
   from the production environment's `APP_URL` variable (through `gh`), or from `APP_URL=https://<domain>`
   in `grafana-admin.env`.
5. **Turn it on:** run `chmod 600 ~/.config/sports-follow/grafana*.env`, then:
   ```bash
   deploy/push-secrets.sh deploy@<server-ip>   # grafana.env to the server; Alloy starts
   deploy/grafana/apply-alerts.sh --test       # the rules, the uptime check, the email contact point and policy, and a test email
   ```

Run `apply-alerts.sh` again after changing `rules.json`. A rule whose query changed starts over: if it
was firing, it sends "Resolved", then fires again after its wait. Its notification policy replaces the
stack's whole policy tree. `grafana-admin.env` only loads rules, so it stays on the Mac. To look at alerts
or silence them, use the stack's Grafana.

Each rule's query returns the current value, such as the backup's age or how full a disk is, and the
rule's threshold decides whether that's a problem. If the metrics stop, a rule keeps its last state, so a
silent server sends one "stopped sending metrics" email and no false "Resolved" ones.

**Setting one off on purpose** (on the server, as `deploy`):
- **A late backup:** this runs the checks once as if there were no backups.
  ```bash
  sudo systemd-run --quiet --wait -p User=deploy -p StateDirectory=sports-follow-check -E BACKUP_DIR=/nonexistent /opt/sports-follow/deploy/server-check.sh
  ```
  "The database backup is late" arrives within about 3 minutes. The timer's next normal run puts it
  right, and "Resolved" follows within about 10.
- **The server going quiet:** `sudo systemctl stop alloy`. The email comes after about 11 minutes. Then
  `sudo systemctl start alloy`.
- **Fans can't reach the app:** this takes the app away from fans while it lasts, so pick a quiet time.
  ```bash
  cd /opt/sports-follow && docker compose -f deploy/compose.yaml stop caddy
  ```
  "Fans can't reach the app" arrives in about 4 to 5 minutes. Then `docker compose -f deploy/compose.yaml
  start caddy` within 10 minutes, or the containers alert fires too. "Resolved" follows within about 5.

`server-check.sh` can still push to an ntfy topic as well, but the emails cover the same ground. For that,
save the topic's URL in `~/.config/sports-follow/alert-url` and run `push-secrets.sh`. The checks' log:
`sudo journalctl -u sports-follow-check`. Alloy's: `sudo journalctl -u alloy`.

Old images and build cache older than a week are removed early on Sundays (`sports-follow-prune.timer`).

## Traces and logs

With `otlp.env` on the server, the API and the worker send **traces and log lines to Grafana Cloud**
over OTLP: the same free stack the alerts use, its Tempo and Loki. Without it they stay on the server
(`c logs`), and the app runs the same.

- **Traces:** each API request is a trace, named for its route. The jobs it queues continue it. Inside
  are each job, each round of a live poll, and the source requests, database queries, Redis commands and
  model calls they made, with their timings. Health checks and live streams have none.
- **Log lines:** every line the app writes to stdout, with its fields as attributes: `rid`, the fan's
  hash, `job`, `player_id`, `route`, `status`, `duration_ms`. A line logged inside a trace carries its
  trace id, so Grafana links the line to the trace and the trace to its lines.
- **Errors:** a line with an exception has its stack (`exception_stacktrace`), what failed (`error`, its
  class) and where (`place`, the innermost function of the app's own code it passed through, e.g.
  `sports_follow.adapters.sportmonks:get`). An exception that escapes a route is that request's line, and
  a failed job's closing line has its stack. Each stack is logged once. The web app sends its own errors
  (uncaught ones, failed renders, calls answered with a 5xx) to `/api/errors`. Those lines are logged by
  the API on `sports_follow.web`, with the page, the app's build (`version`) and a failed call's request
  id (`call_rid`). Their stacks are mapped back to `web/src` through the build's source maps, which the
  image keeps in `web/sourcemaps` and doesn't serve.
- **Metrics:** every minute, the API's requests by route, the worker's jobs, pushes, moments, card
  builds and refreshes, and source requests by host; and what the worker reads each minute: live games
  and how long since each was polled, the job queue, cards by status, each source's allowance used and
  left. They land in the stack's Prometheus as `sports_follow_…`, `http_server_request_duration_seconds_…`
  and `http_client_request_duration_seconds_…`, with `job="sports-follow-api"` or `"sports-follow-worker"`.
- **Never sent:** query strings (a fan's search words), fan ids (a hash instead), query or Redis values.
- **Settings:** OpenTelemetry's own (`OTEL_*`, README.md's settings table), so another backend, or a
  collector on another server, is a change to `otlp.env` alone.

**Setting it up** (you, once):
1. **The key:** on the grafana.com portal, open your stack → **OpenTelemetry** → **Configure**. Create
   a token there (its scopes include `traces:write` and `logs:write`). The page then shows the settings
   to use. Copy just the endpoint and headers lines into `~/.config/sports-follow/otlp.env` on the Mac:
   ```
   OTEL_EXPORTER_OTLP_ENDPOINT=https://otlp-gateway-prod-….grafana.net/otlp
   OTEL_EXPORTER_OTLP_HEADERS=Authorization=Basic%20…
   ```
   Leave out the page's `OTEL_RESOURCE_ATTRIBUTES` and `OTEL_SERVICE_NAME` lines: the API and the worker
   name themselves (`sports-follow-api`, `sports-follow-worker`), and `settings.env` says production.
2. **Turn it on:** run `chmod 600 ~/.config/sports-follow/otlp.env`, then:
   ```bash
   deploy/push-secrets.sh deploy@<server-ip>   # otlp.env to the server, sealed with the rest
   ```
   Then on the server, `c up -d --force-recreate api worker`. The next deploy does it too.
3. **Check:** in the stack's Grafana, go to **Explore**. Pick the **Tempo** data source, search, and
   choose service `sports-follow-api`; a trace comes within a minute of any request. Or pick **Loki**
   and query `{service_name="sports-follow-api"}`.

**Finding things:**
- **What's failing:** the **Errors** dashboard (below). Each group's lines are in Loki as
  `{service_name=~"sports-follow.*"} | error="<error>" | place="<place>"`.
- **A request a fan reports:** its id is in the answer's `X-Request-ID` header. In Loki, query
  `{service_name=~"sports-follow.*"} | rid="<id>"` for its lines and its jobs' lines. Open the trace from
  any of them.
- **A slow or failing route:** in Tempo, search `{span.http.route="/api/follows" && duration>1s}` or
  `{status=error}`.
- **A source:** spans named `GET <host>`, each with its rate-limit lane (`sports_follow.lane`). For
  example `{span.server.address="api.sportmonks.com"}`, or its refusals:
  `{span.server.address="api.sportmonks.com" && span.http.response.status_code=429}`.

Every trace is kept. That's fine at today's traffic, well inside the free tier's monthly traces and logs.
Once there's much more, set `OTEL_TRACES_SAMPLER=parentbased_traceidratio` and `OTEL_TRACES_SAMPLER_ARG`
in `settings.env` to keep a share. Keeping every error and slow trace as well needs a collector that sees
whole traces (Alloy's tail sampling) in between.

## Dashboards

Six dashboards in the stack's Grafana, in the **Sports Follow** folder the alert rules use. They're built
from the traces, log lines and metrics (above), so they need nothing more on the server:

| Dashboard | Shows | From |
|---|---|---|
| **API** | requests, server errors and refusals, time taken (p95) by route; the failed requests and the API's warnings | each request's log line |
| **Sources** | requests a second, errors, 429s and time taken (p95) by source; requests by rate-limit lane; slow requests; refusals and source warnings; each source's allowance used this window, what it says is left, pauses, this hour's requests by lane | the source requests' spans, the worker's metrics |
| **Jobs and live polls** | live games, games behind (not polled for 3 minutes) and since each source's games were last polled; jobs waiting and the oldest's wait; job runs, failures, time in the queue and time taken by job; live poll rounds and their time; failed cards and how old live cards' scores are; card builds and refreshes; the worker's warnings | the worker's metrics, each job's closing log line, the rounds' spans |
| **Notifications** | moments found by kind; pushes delivered and failed (by push service and status), opened, and how long a moment waited for its push | `notify.py`'s log lines and metrics, the "opened" requests |
| **Feed** | the feed's funnel by sport (uploads read, matched, kept, judged, passed, served) and kept by stream; what wasn't kept, by reason; each sport's coverage (followed players with a servable video this week); the judge's verdicts, errors by kind and time; moment searches, what they kept and served, and units per served video (sweeps' searches count in the funnel as the `gap`, `wide`, `famous` and `pool` streams); YouTube's units and requests; the curator's warnings | the curator's metrics (`feedstats.py`), the worker's source readings, the curator's log lines |
| **Errors** | errors from the API, the worker and the web app, grouped by what failed and where, with how many lines and when each was first and last seen; the error lines with their stacks | lines with an exception, and the web app's error lines |

A failed request's line opens its trace (its `traceID`), and a trace's id offers **Log lines of this
trace** (a correlation from Tempo to Loki). The dashboards find their Loki and Tempo data sources
themselves, so the same JSON works on another Grafana.

**Putting them in** (or back, after a change): from the Mac, with the same `grafana-admin.env` as the
alert rules:
```bash
deploy/grafana/apply-dashboards.sh
```
It replaces each dashboard with its file in `grafana/dashboards/`. To keep a change made in Grafana's UI,
export the dashboard's JSON (**Share → Export**, without "external sharing") over its file.

Traces' panels may miss the last few minutes on a long range; a shorter range shows them.

## Operating

On the server, as `deploy`, in `/opt/sports-follow`, with `c() { docker compose -f deploy/compose.yaml "$@"; }`.
To find out why something went wrong (a fan's missing push, a stuck card, a stale score), Claude sessions
follow the `debug` skill (`.claude/skills/debug/SKILL.md`), which also says what's safe to run here.

| | |
|---|---|
| What's live | `deploy/deploy.sh status`; from anywhere, `curl https://<domain>/api/health` (its `commit`) |
| Logs | `c logs -f api worker` (Caddy's certificate work: `c logs caddy`) |
| One request or job's lines | `c logs api worker \| grep <id>`: JSON lines, each with the `rid` of its request or job; a request's id is in its answer's `X-Request-ID` header, and the jobs it queued log under it too |
| Restart after a secret changes | `c up -d --force-recreate` |
| A shell in the database | `c exec postgres psql -U sports sports_follow` |
| Registry import now | `c exec worker python -m sports_follow.registry` (one sport: `… registry chess`; every esports game: `… registry esports`, or one: `… registry valorant`) |
| Sources' spend by lane | `c exec worker python -m sports_follow.usage` |
| Sources' cache | `c exec cache redis-cli INFO memory` (keys: the `debug` skill's `redis.md`). Emptying it loses nothing but spends quota to fill again |
| Add or remove a player | `c exec -it worker python -m sports_follow.players list` (`add`, `remove`, `restore`, `unmatch` a wrong face, `rematch`: see the README) |
| A campaign page (stann.in/gukesh) | `c exec worker python -m sports_follow.campaigns list` (`set`, `show`, `player add`, `team find`, `team add`, `game add`, `remove`: see the README) |
| Backups | `ls /var/backups/sports-follow` |
| Restore a backup | `c stop api worker && c exec -T postgres pg_restore -U sports -d sports_follow --clean --if-exists < /var/backups/sports-follow/<file>.dump && c run --rm migrate && c start api worker` (off-site ones: [Off-site backups](#off-site-backups)) |
| Off-site backups | `deploy/offsite-backup.sh list`; run now: `sudo systemctl start sports-follow-offsite`; log: `sudo journalctl -u sports-follow-offsite` |

The daily dumps stay on the server for 14 days. Off-site, they're sealed and kept for longer (see
[Off-site backups](#off-site-backups)), next to the sealed secrets. Lightsail's automatic snapshots, if
they're on, are one more copy.

A server set up before fan cookies were signed has no cookie key yet: run `deploy/push-secrets.sh
deploy@<server-ip>` once before deploying, or the API stops at startup and says so. The key is kept from
then on; a new one would sign every fan out of their follows.

### Rebuilding the server from nothing

Use this when the server is gone, the Mac too or not. It needs three things only:
- **the repo**, on GitHub;
- **your age key**, in the password manager;
- **the Backblaze bucket**, through its web page.

The drill (2026-10-14) went from a blank server to serving in **17 minutes**, plus the console steps. Fans
stay signed in and keep getting notifications, because the cookie and push keys come back with the
secrets. Their data is as of the newest off-site dump: the daily one, off-site within the hour.

1. **A new server** (First deploy, step 1): Lightsail in Sydney, Ubuntu 24.04, the $24 plan, your key.
   - **Firewall:** open **HTTPS (TCP and UDP 443)**, to any address.
   - **Static IP:** on the Networking tab, **move it** from the old instance to the new one. The address,
     and with it the sslip.io name or your DNS, then stays the same.
   - **Check you can get in:** `ssh ubuntu@<ip> true`. If it's refused, the server came up with
     Lightsail's default key. Add yours from the console's browser SSH (First deploy, step 1).
2. **Setup**, about 3 minutes: `server-setup.sh` as `ubuntu` (First deploy, step 2). Add the read key
   it prints on GitHub, then run it again as `deploy`. Remove the old server's read key from GitHub.
3. **Secrets**, about 2 minutes:
   - **The sealed copy:** download `secrets.age` from the bucket's page (**Browse Files**). It's at most
     an hour behind the last change to the secrets, so it can be newer than the password manager's copy.
   - **Put them back:** `deploy/restore-secrets.sh deploy@<ip> secrets.age`, with the age key.
4. **The app and its data**, about 7 minutes. On the server, as `deploy`, in `/opt/sports-follow`, start
   only the database first, so the worker never runs on an empty one:
   ```bash
   c() { docker compose -f deploy/compose.yaml "$@"; }
   c build && c up -d postgres redis
   ```
   Fetch the newest dump from the Mac with `deploy/fetch-backup.sh deploy@<ip>`, which asks for the age
   key. Then, back on the server:
   ```bash
   c exec -T postgres pg_restore -U sports -d sports_follow --clean --if-exists --no-owner < /var/backups/sports-follow/fetched/<file>.dump
   deploy/deploy.sh deploy   # migrations, then everything: API, worker, backups, Caddy
   sudo systemctl start alloy
   ```
   The hourly off-site timer is already on, so with the restored `offsite.env` the new server's dumps go
   to the same bucket.
5. **GitHub deploys** point at the old server's host key and key, so redo
   [Setting up the GitHub deploys](#setting-up-the-github-deploys-you-once): a new key, and the new
   server's host key in `DEPLOY_KNOWN_HOSTS`.
6. **Check it:**
   - `https://<domain>/api/health`, a search, and `/credits` (no ESPN);
   - an existing phone is still signed in, with its players;
   - the Grafana email "The server stopped sending metrics" resolves once Alloy reports from the new
     server.

**Rehearsing it** next to a live production: make the drill server something production can't feel.
Don't start its worker or backup job, which would poll the sources on the same tokens and push to real
fans. Disable its off-site timer and Alloy, which would write to the same bucket and Grafana (`sudo
systemctl disable --now sports-follow-offsite.timer alloy`, before restoring the secrets). To check
"still signed in", use a marker fan made on production beforehand: a cookie and a follow, in a dump taken
then. Its cookie must be accepted on the drill server without a new one being issued. Afterwards, delete
the instance, its GitHub read key and the marker's follow.

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
  So did "The server checks stopped running" once their last run was 15 minutes old.
- With Alloy stopped, "The server stopped sending metrics" emailed after 10½ minutes and resolved once it
  was back. Meanwhile the other alerts kept their state rather than sending false "Resolved" mails.
- The first version of the rules returned data only while something was wrong. Kept-state on no data then
  meant they never resolved; the value-and-threshold rules do.

On the production server, `server-setup.sh` installed Alloy from Grafana's repository, and
`push-secrets.sh` started it. Its metrics then arrived on the real Grafana Cloud stack (Grafana 13.3):
about 370 series, all seven containers up. All 14 rules evaluated without errors and stayed quiet. The
test email arrived through Grafana 13's receivers API, since 13 removed the older test endpoint.

The rebuild was drilled on 2026-10-14 (BHA-175) with a throwaway Lightsail instance in Sydney, from the
repo, the age key and the Backblaze bucket only.

| Time (UTC) | |
|---|---|
| 20:26 | blank server, and the console's browser SSH to add the key it came up without |
| 20:27–20:30 | `server-setup.sh` as `ubuntu`: updates, Docker, Alloy, `deploy`, the lockdown |
| 20:32 | its read key added on GitHub, the setup again as `deploy` |
| 20:33–20:35 | `secrets.age` downloaded from the bucket's page, `restore-secrets.sh`: 11 files, identical to production's |
| 20:35–20:41 | `c build` (5½ minutes), the database and Redis started |
| 20:42–20:43 | `fetch-backup.sh` (byte-identical to production's dump), `pg_restore`, migrations, the API up |

That's **17 minutes** to serving, plus the port 443 rule added in the console.

Then, compared with production:
- The health check answered from outside.
- A marker fan's production cookie was accepted, with no new one issued (a forged cookie was replaced),
  and its follow was there.
- The push public key was the same.
- So were the counts: 366,805 athletes, 2 follows, 2 players.
- Search worked, and the credits named no ESPN.

Two things the drill showed:
- **The off-site secrets can lag by up to an hour.** A secret added at 20:33 (`otlp.env`, for traces)
  wasn't in the bucket's copy downloaded just after, because the hourly upload hadn't run yet.
- **How much data a disaster can lose.** It's whatever was written after the newest dump: up to a day,
  plus that hour. To lose less, make `backup.sh` dump more often.
