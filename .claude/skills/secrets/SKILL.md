---
name: secrets
description: Add, rotate, check, send or remove a secret for Sports Follow - an API token or key, a service-account file, the admin password's hash, a GitHub Actions secret. Covers where each kind lives (the Mac's ~/.config/sports-follow, the server's /etc/sports-follow, the containers' /run/secrets, GitHub), how Bharath gets a key from a provider and saves it, checking it without printing it, push-secrets.sh, what to restart for each kind, the sealed backup, and which files change when the code needs a secret it doesn't know yet. Use whenever a task needs a token, key or password, a Linear issue waits on one, or the admin panel says "No token on the server".
---

# Secrets

A secret's value is never seen by anyone but Bharath and the process that uses it. Everything here keeps
it that way, and keeps the server's copy, the Mac's copy and the sealed backup in step.

## Rules

- **Never show a value.** Don't `cat`, `echo`, `grep` or print a secret file, or put one in chat, a
  commit, a Linear comment, a log line, a test fixture or a URL. Check a file with `check.sh` (below), and
  pass a key to a test call in a header (`X-Goog-Api-Key`, `Authorization`), never as `?key=`. Don't run
  scripts that read secrets under `bash -x`.
- **Bharath gets keys, Claude doesn't.** Signing up, making a key in a provider's console, paying and
  accepting terms are his. Never type a key into a web form or read one off a page. Write him the steps
  (below), put `Needs you` on the issue, and take over once the file is on the Mac.
- **Never in the repo.** The repo is public. `deploy/settings.env` holds settings, not secrets, and a
  sealed backup (`*.age`) doesn't belong in a working tree either.
- **Never make the server's own secrets again.** A new fan cookie key signs every fan out, and a new push
  key stops notifications on every device until each subscribes again. `install-secrets.sh` makes them
  once and keeps them.
- **Production is his to approve.** Sending a secret Bharath saved for that purpose (`push-secrets.sh`) is
  part of the task. Restarting production containers, deleting a server file or changing the `production`
  environment's secrets needs his go-ahead first. Approving a deploy is never yours (the deploy gate hook).
- **Tests never see secrets.** CI has no `~/.config/sports-follow`, so run tests as CI does:
  `env HOME=$(mktemp -d) REDIS_URL=redis://127.0.0.1:1/0 .venv/bin/python -m pytest`.

## Where each kind lives

| Kind | On the Mac (`~/.config/sports-follow/`) | On the server (`/etc/sports-follow/`) | Read by |
|---|---|---|---|
| A score source's token: `sportmonks-token`, `lichess-token` (both required), `pandascore-token`, `liquipedia-token`, `pubg-token` | `<name>`, mode 600 | `files/<name>`, mode 400, owner 10001; `/run/secrets/<name>` in the containers | `SPORTS_FOLLOW_<X>_TOKEN_FILE` (or `SPORTS_FOLLOW_<X>_TOKEN`), once per process: api, worker, curator |
| `youtube-key` (the feed) | same | same | `SPORTS_FOLLOW_YOUTUBE_KEY_FILE` (or `_KEY`), once per process: api, worker, curator |
| `resend-key`, `google-client-secret` (signing in) | same | same | `SPORTS_FOLLOW_RESEND_KEY_FILE`, `SPORTS_FOLLOW_GOOGLE_CLIENT_SECRET_FILE` (or without `_FILE`), once per process: api |
| `fcm-service-account.json` (Android pushes) | same | same | `SPORTS_FOLLOW_FCM_KEY_FILE`, once per process: api, worker |
| `admin-password` (hash and authenticator key, made by `python -m sports_follow.admin password` and `code`), `linear-key` | same | same | `SPORTS_FOLLOW_ADMIN_PASSWORD_FILE`, `SPORTS_FOLLOW_LINEAR_KEY_FILE`, on each use: no restart |
| `openrouter-key` (production's own) | same | inside `gateway.env` | the gateway, when its container is made |
| `otlp.env` (traces and logs) | same | `otlp.env`, mode 640, root:deploy | api, worker, curator, when their containers are made |
| `offsite.env` (backups), `grafana.env` (Alloy) | same | same, 640 | the host's scripts; `install-secrets.sh` restarts Alloy whenever `grafana.env` is sent |
| `alert-url`, `heartbeat-url` (server checks) | same | both in `alerts.env`, rewritten from whichever are sent | `server-check.sh` |
| Made by the server, never sent: database password, gateway token, push key, fan cookie key | none | `postgres.env`, `app.env`, `gateway.env`, `files/vapid-private.pem`, `files/cookie-secret` | as their files say |
| On the Mac only, never sent: `grafana-admin.env` (`apply-alerts.sh`, the debug skill), `google-services.json` (the Android build), `ntfy-topic`, `cookie-secret` (local runs) | `<name>` | none | the Mac's scripts |
| All of the server's secrets, sealed | none | `/var/backups/sports-follow/secrets.age` | the password manager's age key only |
| GitHub Actions secrets | none | none | the workflows (see the GitHub Actions secrets section) |

`deploy/push-secrets.sh` holds the current list of what it sends; trust it over this table. Locally the
app reads the same `~/.config/sports-follow` files. An isolated stack (the `isolated-stack` skill) points
every `_FILE` at nothing, so it never spends production's keys.

## Adding or rotating a value

The usual job: a source needs its key, a key expired, or one has to be replaced.

1. **Bharath gets it.** Write the steps in chat and on the Linear issue, click by click. Name the console
   and the project, link straight to the page (`?project=<id>`), and say what to restrict it to: one API,
   read only, no billing if none is needed. If he can't find a project, he's signed in to another
   account. For Google, the Firebase console for `stann-300ed` opens in the right one. He makes the key
   himself. Never ask him to paste it into chat.
2. **He saves it on the Mac:**
   ```bash
   pbpaste > ~/.config/sports-follow/<name> && chmod 600 ~/.config/sports-follow/<name>
   ```
3. **Check it without printing it:** `.claude/skills/secrets/check.sh <name>` says whether it's there,
   its mode, stray whitespace, and whether it has the provider's shape. For a JSON file it gives the field
   names; for an `.env` file, the variable names. Then, where the quota is free or large, make one cheap
   call with it in a header. For example, YouTube (10,000 units a day):
   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' -H "X-Goog-Api-Key: $(tr -d '[:space:]' < ~/.config/sports-follow/youtube-key)" \
     'https://www.googleapis.com/youtube/v3/videos?part=id&id=jNQXAC9IVRw'
   ```
   Skip the call for a tight quota (PUBG's 10 a minute, Liquipedia's 60 an hour). The Mac's key is
   production's, and the call isn't counted in `adapters/budget.py`, so it uses up what live games need.
4. **Send it** from a checkout at the latest `origin/main`. The main checkout's branch is often stale,
   and an old script may not know the file or may seal for the wrong keys:
   ```bash
   git fetch -q origin && git diff --quiet origin/main -- deploy/push-secrets.sh deploy/install-secrets.sh deploy/seal-secrets.sh deploy/secrets-recipients.txt && echo current
   .claude/skills/secrets/check.sh openrouter-key
   deploy/push-secrets.sh deploy@16.178.106.36
   ```
   No `current`? Run it from a worktree at `origin/main` (`git worktree add --detach .claude/worktrees/<name> origin/main`).
   `push-secrets.sh` sends only the names in its own list, and stops if `sportmonks-token` or
   `lichess-token` is missing. It replaces the tokens, keeps what the server made, and seals the backup
   again. Without `openrouter-key` on the machine, it would give production the Mac's development
   OpenRouter key; that's what the `check.sh openrouter-key` line is for. Because the Mac has
   `grafana.env`, every run also restarts Alloy. It prints `install-secrets: /etc/sports-follow updated (…)`
   and `seal-secrets: N secret files sealed`.
5. **Confirm it landed.** Tokens live in `files/`, not in `/etc/sports-follow` itself:
   ```bash
   ssh -o BatchMode=yes deploy@16.178.106.36 'sudo test -s /etc/sports-follow/files/<name> && echo present'
   ```
6. **Restart what reads it**, per the table's last column. Ask Bharath first.
   `ssh deploy@16.178.106.36 'cd /opt/sports-follow && docker compose -f deploy/compose.yaml up -d --force-recreate <services>'`
   - A score source's token, `youtube-key` or `otlp.env`: `api worker curator`.
   - `fcm-service-account.json`: `api worker`.
   - `resend-key` or `google-client-secret`: `api`.
   - `openrouter-key`: `gateway`. Do this before the old key is revoked, or the AI stops until the next deploy.
   - `admin-password`, `linear-key`, `offsite.env`, `alert-url` or `heartbeat-url`: nothing. `grafana.env`: done already.

   If a deploy is waiting, his approval recreates the app containers anyway, so send the key before he
   approves. That doesn't cover the gateway.
7. **The backup.** Tell Bharath to download the new sealed copy into the password manager, outside the repo:
   `deploy/backup-secrets.sh deploy@16.178.106.36 ~/Downloads/sports-follow-secrets-$(date +%Y%m%d-%H%M).age`.
8. **Check the app uses it:**
   - A score source: the admin panel's Sources tab no longer says "No token on the server", `https://stann.in/api/credits` names it, and `c exec worker python -m sports_follow.usage` (on the server; deploy/README.md, "Operating") shows it spending.
   - `youtube-key`: the Sources tab's Feed shows YouTube running, and its `youtube-data` budget spends.
   - `fcm-service-account.json`: `https://stann.in/api/push` answers `"fcm": true`.
   - `resend-key`: a sign-in code email arrives.
   - `google-client-secret`: the Continue with Google button shows (`/api/config`, `google_sign_in: true`).
   - `linear-key`: the admin panel's Linear card fills in.
   - `admin-password`: signing in at `/admin` asks for the code.
9. **Linear.** Comment what was done: where it went and how it was checked, never the value. Take
   `Needs you` off once his part is done.

**Rotating** is the same, then Bharath revokes the old key at the provider (after step 6). **If one
leaked**, send the new one first, then revoke the old, then look for where it leaked (git history, logs,
chat) and say so.

## A secret the code doesn't know yet

A new source or service with a key of its own. This is the known exception to CLAUDE.md's "one module,
one entry" for a source: the deploy's secrets lists name each file. One PR changes:

- **The module that reads it** (the adapter's client, for a source):
  `TOKEN_FILE = Path(os.environ.get("SPORTS_FOLLOW_<X>_TOKEN_FILE", Path.home() / ".config" / "sports-follow" / "<name>"))`
  and a cached `token()` that prefers `SPORTS_FOLLOW_<X>_TOKEN`, as in `adapters/pubg.py`. The adapter's
  `available()` is false without it, so the admin panel shows "No token on the server" and nothing else
  breaks. The adapter describes its own token (CLAUDE.md, "Add or remove a data source").
- **`deploy/compose.yaml`:** the very variable the module reads (`SPORTS_FOLLOW_<X>_TOKEN_FILE: /run/secrets/<name>`)
  under `x-app`'s environment, with a comment saying what it's for and whether it's optional. A different
  name there means the container never finds the file.
- **`deploy/push-secrets.sh`:** the name in the optional-files loop, and in the header comment.
- **`deploy/install-secrets.sh`:** the same name in its loop.
- **`.claude/skills/isolated-stack/SKILL.md`:** `SPORTS_FOLLOW_<X>_TOKEN_FILE=<scratch>/none` in its
  environment, so an isolated stack never spends production's key.
- **`deploy/README.md`, "Secrets":** a paragraph for Bharath on how to get it, save it, send it and restart.
- **`README.md`'s env table:** the variable.
- **This skill's table**, with what reads it and what to restart.
- **Linear:** an issue for getting the key, with `Needs you`.

**Removing** one: take it out of the same places. `push-secrets.sh` never deletes, so the server keeps the
old file until it's removed there. That's `sudo rm /etc/sports-follow/files/<name>`, with Bharath's
go-ahead, and then `push-secrets.sh` again to reseal. Delete the Mac's copy too.

## GitHub Actions secrets

- **Repository secrets** feed workflows that don't wait for approval: `DEPLOY_SUMMARY_URL` (an ntfy
  topic), `RESEND_KEY` and `DEPLOY_SUMMARY_TO`. Set them with `gh secret set <NAME>`, never `--env production`,
  or the summary would wait for approval too.
- **`production` environment secrets** (`DEPLOY_SSH_KEY`, `DEPLOY_KNOWN_HOSTS`, `DEPLOY_HOST`) open the
  server to the Deploy run. They're Bharath's: change them only when he asks (deploy/README.md, "Setting up
  the GitHub deploys").
- `gh secret list` shows names and dates, never values. A value Claude makes goes straight to a file and
  from there to GitHub, never through the transcript. For example, a new ntfy topic:
  ```bash
  (umask 077; python3 -c 'import secrets; print("stann-deploys-" + secrets.token_hex(8))' > ~/.config/sports-follow/ntfy-topic)
  printf 'https://ntfy.sh/%s' "$(tr -d '[:space:]' < ~/.config/sports-follow/ntfy-topic)" | gh secret set DEPLOY_SUMMARY_URL
  ```
  Bharath reads the topic himself (`cat ~/.config/sports-follow/ntfy-topic` in his own terminal) to
  subscribe in the ntfy app.
