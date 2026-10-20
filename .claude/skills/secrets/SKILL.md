---
name: secrets
description: Add, rotate, check, send or remove a secret for Sports Follow - an API token or key, a service-account file, the admin password's hash, a GitHub Actions secret. Covers where each kind lives (the Mac's ~/.config/sports-follow, the server's /etc/sports-follow, the containers' /run/secrets, GitHub), how Bharath gets a key from a provider and saves it, checking it without printing it, push-secrets.sh, what to restart, the sealed backup, and which files change when the code needs a secret it doesn't know yet. Use whenever a task needs a token, key or password, a Linear issue waits on one, or the admin panel says "No token on the server".
---

# Secrets

A secret's value is never seen by anyone but Bharath and the process that uses it. Everything here keeps
it that way, and keeps the server's copy, the Mac's copy and the sealed backup in step.

## Rules

- **Never show a value.** Don't `cat`, `echo`, `grep` or print a secret file, or put one in chat, a
  commit, a Linear comment, a log line, a test fixture or a URL. Check a file with `check.sh` (below), and
  pass a key to a test call in a header (`X-Goog-Api-Key`, `Authorization`), never as `?key=`.
- **Bharath gets keys, Claude doesn't.** Signing up, making a key in a provider's console, paying and
  accepting terms are his. Never type a key into a web form or read one off a page. Write him the steps
  (below), put `Needs you` on the issue, and take over once the file is on the Mac.
- **Never in the repo.** The repo is public. `deploy/settings.env` holds settings, not secrets.
- **Never make the server's own secrets again.** A new fan cookie key signs every fan out, and a new push
  key stops notifications on every device until each subscribes again. `install-secrets.sh` makes them
  once and keeps them.
- **Production is his to approve.** Sending a secret Bharath saved for that purpose (`push-secrets.sh`) is
  part of the task. Restarting production containers, deleting a server file or changing the `production`
  environment's secrets needs his go-ahead first. Approving a deploy is never yours (the deploy gate hook).
- **Tests never see secrets.** CI has no `~/.config/sports-follow`, so run tests as CI does:
  `env HOME=$(mktemp -d) REDIS_URL=redis://127.0.0.1:1/0 .venv/bin/python -m pytest`.

## Where each kind lives

| Kind | On the Mac | On the server | In the containers |
|---|---|---|---|
| A source's token or service key: `sportmonks-token`, `lichess-token`, `pandascore-token`, `liquipedia-token`, `pubg-token`, `youtube-key`, `resend-key`, `google-client-secret`, `linear-key`, `fcm-service-account.json` | `~/.config/sports-follow/<name>`, mode 600 | `/etc/sports-follow/files/<name>`, mode 400, owner 10001 | `/run/secrets/<name>`; its path in `SPORTS_FOLLOW_<X>_FILE` (`deploy/compose.yaml`, `x-app`) |
| The admin panel's password hash and authenticator key | `admin-password`, made by `python -m sports_follow.admin password` and `code` | `files/admin-password` | `/run/secrets/admin-password` |
| Production's OpenRouter key | `openrouter-key` | `/etc/sports-follow/gateway.env` | the gateway's environment |
| `.env` files for the host: `offsite.env` (backups), `grafana.env` (Alloy), `otlp.env` (traces and logs) | `~/.config/sports-follow/<name>` | `/etc/sports-follow/<name>`, mode 640, root:deploy | `otlp.env` as `env_file`; the other two are for the host |
| Made by the server, never sent: database password, gateway token, push key, fan cookie key | none | `postgres.env`, `app.env`, `gateway.env`, `files/vapid-private.pem`, `files/cookie-secret` | as above |
| All of the server's secrets, sealed | none | `/var/backups/sports-follow/secrets.age` | none |
| GitHub Actions secrets | none | none | in GitHub only (see the GitHub Actions secrets section below) |

`deploy/push-secrets.sh` holds the current list of what it sends; trust it over this table. Locally the
app reads the same `~/.config/sports-follow` files, or `SPORTS_FOLLOW_<X>_TOKEN` in the environment. An
isolated stack (the `isolated-stack` skill) makes its own and runs paid sources off.

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
   its mode, any stray newline, and whether it has the provider's shape. Then make one cheap call with it
   in a header, for example YouTube:
   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' -H "X-Goog-Api-Key: $(tr -d '[:space:]' < ~/.config/sports-follow/youtube-key)" \
     'https://www.googleapis.com/youtube/v3/videos?part=id&id=jNQXAC9IVRw'
   ```
   For a JSON key, print its field names, never their values.
4. **Send it** from a checkout at the latest `origin/main` (the main checkout's branch is often stale,
   and an old `push-secrets.sh` may not know the file):
   ```bash
   git fetch -q origin && git diff --quiet origin/main -- deploy/push-secrets.sh deploy/install-secrets.sh && echo current
   deploy/push-secrets.sh deploy@16.178.106.36
   ```
   It sends everything in `~/.config/sports-follow`, replaces the tokens, keeps what the server made, and
   seals the backup again. It prints `install-secrets: /etc/sports-follow updated (…)` and
   `seal-secrets: N secret files sealed`.
5. **Confirm it landed.** Tokens live in `files/`, not in `/etc/sports-follow` itself:
   ```bash
   ssh -o BatchMode=yes deploy@16.178.106.36 'sudo test -s /etc/sports-follow/files/<name> && echo present'
   ```
6. **Restart what reads it.** Sources read their token once per process (`token()` is cached), so the
   API, the worker and the curator need recreating:
   `ssh deploy@16.178.106.36 'cd /opt/sports-follow && docker compose -f deploy/compose.yaml up -d --force-recreate api worker curator'`.
   Ask Bharath first. If a deploy is waiting, his approval recreates them anyway, so send the key before
   he approves. The admin password and the Linear key are read on each use and need no restart.
7. **The backup.** Tell Bharath to download the new sealed copy into the password manager:
   `deploy/backup-secrets.sh deploy@16.178.106.36`.
8. **Check the app uses it.** The admin panel's Sources tab no longer says "No token on the server",
   `https://stann.in/api/credits` names the source, and `python -m sports_follow.usage` shows it spending.
9. **Linear.** Comment what was done: where it went and how it was checked, never the value. Take
   `Needs you` off once his part is done.

**Rotating** is the same, then Bharath revokes the old key at the provider. **If one leaked**, send the new
one first, then revoke the old, then look for where it leaked (git history, logs, chat) and say so.

## A secret the code doesn't know yet

A new source or service with a key of its own. One PR changes, in order:

- **The module that reads it** (the adapter's client for a source):
  `TOKEN_FILE = Path(os.environ.get("SPORTS_FOLLOW_<X>_TOKEN_FILE", Path.home() / ".config" / "sports-follow" / "<name>"))`
  and a cached `token()` that prefers `SPORTS_FOLLOW_<X>_TOKEN`, as in `adapters/pubg.py`. The adapter's
  `available()` is false without it, so the admin panel shows "No token on the server" and nothing else
  breaks. The adapter describes its own token (CLAUDE.md, "Add or remove a data source").
- **`deploy/push-secrets.sh`:** the name in the optional-files loop, and in the header comment.
- **`deploy/install-secrets.sh`:** the same name in its loop.
- **`deploy/compose.yaml`:** `SPORTS_FOLLOW_<X>_FILE: /run/secrets/<name>` under `x-app`'s environment,
  with a comment saying what it's for and whether it's optional.
- **`deploy/README.md`, "Secrets":** a paragraph for Bharath on how to get it, save it, send it and restart.
- **`README.md`'s env table:** the variable.
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
- `gh secret list` shows names and dates, never values. For a value Claude makes (a random ntfy topic:
  `python3 -c 'import secrets; print(secrets.token_hex(8))'`), keep a copy in `~/.config/sports-follow/`
  when Bharath needs it, as `ntfy-topic` is.
