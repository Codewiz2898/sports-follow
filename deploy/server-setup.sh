#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 24.04 server: security updates, a firewall, swap, Docker, a `deploy`
# user to log in as (root can't, over SSH), the server checks, and this repo in /opt/sports-follow.
# The first time, from the Mac, as root with the key the provider put on the server:
#   ssh root@<server-ip> 'RECOVERY_KEY="ssh-ed25519 AAAA… recovery" bash -s' < deploy/server-setup.sh
# Every later run (root login is off by then):
#   ssh deploy@<server-ip> 'sudo bash -s' < deploy/server-setup.sh
# Optional, in front of bash -s:
#   DOMAIN=app.example.com     the app's address; defaults to <server-ip>.sslip.io (real HTTPS, no domain needed)
#   RECOVERY_KEY="<public key>"  a second way in that doesn't live on the Mac (README.md, "Getting in")
#   DEPLOY_KEY="<public key>"    GitHub Actions' key; it can run deploy/deploy.sh and nothing else
# Safe to run again: keys are added, never removed.
set -euo pipefail
REPO="${REPO:-git@github.com:Codewiz2898/sports-follow.git}"   # private: read with REPO_KEY, below
APP_DIR=/opt/sports-follow
USER_NAME=deploy
export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get -y upgrade
apt-get install -y ca-certificates curl git openssl ufw unattended-upgrades fail2ban python3-systemd
echo 'APT::Periodic::Update-Package-Lists "1"; APT::Periodic::Unattended-Upgrade "1";' >/etc/apt/apt.conf.d/20auto-upgrades

# Firewall: SSH and the web only (Docker publishes just Caddy's 80 and 443).
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
ufw --force enable

# 2 GB of swap, so a registry import or a build never meets the out-of-memory killer.
if ! swapon --show | grep -q /swapfile; then
  if fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile; then
    grep -q /swapfile /etc/fstab || echo '/swapfile none swap sw 0 0' >>/etc/fstab
  else
    rm -f /swapfile
    echo "server-setup: this server can't use a swap file; carrying on without one" >&2
  fi
fi

# Docker from Docker's own repository, with logs that rotate.
if ! command -v docker >/dev/null; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" >/etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
mkdir -p /etc/docker
[ -f /etc/docker/daemon.json ] || { echo '{ "log-driver": "local" }' >/etc/docker/daemon.json; systemctl restart docker; }

# The deploy user: runs the app and the deploys. Docker's group already makes it root's equal, so its sudo
# has no password; a password would only be one more secret to lose.
id "$USER_NAME" >/dev/null 2>&1 || useradd --create-home --shell /bin/bash "$USER_NAME"
usermod -aG docker "$USER_NAME"
echo "$USER_NAME ALL=(ALL) NOPASSWD:ALL" >/etc/sudoers.d/90-$USER_NAME
chmod 440 /etc/sudoers.d/90-$USER_NAME
visudo -cqf /etc/sudoers.d/90-$USER_NAME

# Its SSH keys: the ones that open root (or the account running this with sudo) today, which is the Mac's,
# plus RECOVERY_KEY, plus DEPLOY_KEY limited to deploy.sh. Lines with options (some providers put a
# "log in as ubuntu" command on root's) are not copied.
KEYS=/home/$USER_NAME/.ssh/authorized_keys
install -d -m 700 -o "$USER_NAME" -g "$USER_NAME" "/home/$USER_NAME/.ssh"
touch "$KEYS"
add_key() {  # add_key <options or ""> <public key line>: replaces any line holding the same key
  local opts="$1" line="$2" blob
  blob=$(awk '{print $2}' <<<"$line")
  case "$line" in ssh-*|ecdsa-*|sk-*) ;; *) echo "server-setup: not a public key: ${line:0:30}…" >&2; return 1 ;; esac
  awk -v b="$blob" '{ for (i = 1; i <= NF; i++) if ($i == b) next } 1' "$KEYS" >"$KEYS.new"
  echo "${opts:+$opts }$line" >>"$KEYS.new"
  mv "$KEYS.new" "$KEYS"
}
for f in /root/.ssh/authorized_keys ${SUDO_USER:+"/home/$SUDO_USER/.ssh/authorized_keys"}; do
  [ -f "$f" ] && [ "$f" != "$KEYS" ] || continue
  while read -r line || [ -n "$line" ]; do
    case "$line" in ssh-*|ecdsa-*|sk-*) grep -qF "$(awk '{print $2}' <<<"$line")" "$KEYS" || add_key "" "$line" ;; esac
  done <"$f"
done
[ -z "${RECOVERY_KEY:-}" ] || add_key "" "$RECOVERY_KEY"
[ -z "${DEPLOY_KEY:-}" ] || add_key "restrict,command=\"$APP_DIR/deploy/deploy.sh\"" "$DEPLOY_KEY"
chown "$USER_NAME:$USER_NAME" "$KEYS"
chmod 600 "$KEYS"
logins=$(grep -cE '^(ssh-|ecdsa-|sk-)' "$KEYS" || true)

# SSH: keys only, no root. This file sorts before cloud-init's 50-cloud-init.conf, and sshd keeps the
# first value it reads. Skipped while no key can log in as deploy, so this can't lock you out.
SSHD_CONF=/etc/ssh/sshd_config.d/00-sports-follow.conf
if [ "$logins" -gt 0 ]; then
  cat >"$SSHD_CONF" <<'EOF'
# Written by deploy/server-setup.sh: keys only, and no root (log in as deploy, then sudo).
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
AuthenticationMethods publickey
MaxAuthTries 4
X11Forwarding no
EOF
  install -d -m 755 /run/sshd
  if ! sshd -t; then
    rm -f "$SSHD_CONF"
    echo "server-setup: sshd rejected the new settings; SSH is unchanged" >&2
    exit 1
  fi
  systemctl try-reload-or-restart ssh
else
  echo "server-setup: no key can log in as $USER_NAME yet, so root login stays on. Pass RECOVERY_KEY and run again." >&2
fi

# fail2ban: bans an address for an hour after 5 failed SSH logins in 10 minutes. "aggressive" also counts
# logins that offer only wrong keys (the only kind there are, with passwords off); a right key never fails.
cat >/etc/fail2ban/jail.d/sports-follow.conf <<'EOF'
[sshd]
enabled = true
backend = systemd
mode = aggressive
maxretry = 5
findtime = 10m
bantime = 1h
EOF
systemctl enable fail2ban >/dev/null 2>&1
systemctl restart fail2ban

# The repo is private, so deploy reads it with a read-only key of its own, made here once and added on
# GitHub by hand (not DEPLOY_KEY, which lets Actions in here). GitHub's host keys come from its API over
# HTTPS, so the first connection isn't taken on trust.
REPO_KEY=/home/$USER_NAME/.ssh/github-read
if [ ! -f "$REPO_KEY" ]; then
  ssh-keygen -q -t ed25519 -N '' -C "sports-follow read $(hostname)" -f "$REPO_KEY"
  chown "$USER_NAME:$USER_NAME" "$REPO_KEY" "$REPO_KEY.pub"
fi
grep -q '^github.com ' /etc/ssh/ssh_known_hosts 2>/dev/null ||
  curl -fsS https://api.github.com/meta | python3 -c 'import json, sys; [print("github.com", k) for k in json.load(sys.stdin)["ssh_keys"]]' >>/etc/ssh/ssh_known_hosts
export GIT_SSH_COMMAND="ssh -i $REPO_KEY -o IdentitiesOnly=yes"
if ! git ls-remote "$REPO" HEAD >/dev/null 2>&1; then
  echo "server-setup: add this key on GitHub (Settings, Deploy keys, write access off), then run this again:" >&2
  echo "  https://github.com/Codewiz2898/sports-follow/settings/keys/new" >&2
  cat "$REPO_KEY.pub" >&2
  exit 1
fi

# The app (owned by deploy, which pulls and builds it), its secrets directory (filled by push-secrets.sh;
# deploy reads the .env files for compose) and its backups.
[ -d "$APP_DIR/.git" ] || git clone --quiet "$REPO" "$APP_DIR"
chown -R "$USER_NAME:$USER_NAME" "$APP_DIR"
sudo -u "$USER_NAME" git -C "$APP_DIR" remote set-url origin "$REPO"
sudo -u "$USER_NAME" git -C "$APP_DIR" config core.sshCommand "$GIT_SSH_COMMAND"   # deploy.sh's fetches
install -d -m 750 -o root -g "$USER_NAME" /etc/sports-follow
install -d -m 750 -o root -g "$USER_NAME" /var/backups/sports-follow
if [ ! -f "$APP_DIR/deploy/.env" ]; then
  ip=$(curl -4 -fsS https://api.ipify.org)
  domain="${DOMAIN:-${ip//./-}.sslip.io}"
  printf 'DOMAIN=%s\nSECRETS_DIR=/etc/sports-follow\nBACKUP_DIR=/var/backups/sports-follow\n' "$domain" >"$APP_DIR/deploy/.env"
  chown "$USER_NAME:$USER_NAME" "$APP_DIR/deploy/.env"
fi

# The server checks (every 5 minutes; alerts go to ALERT_URL) and the weekly Docker clean-up.
install -m 644 "$APP_DIR"/deploy/systemd/*.service "$APP_DIR"/deploy/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now sports-follow-check.timer sports-follow-prune.timer

host=$(grep '^DOMAIN=' "$APP_DIR/deploy/.env" | cut -d= -f2)
echo "server-setup: done. The app will be at https://$host"
echo "server-setup: $logins key(s) can log in as $USER_NAME; root can't log in over SSH."
[ "$logins" -ge 2 ] || echo "server-setup: only one key can get in. Add a recovery key: RECOVERY_KEY=\"<public key>\" (deploy/README.md)."
echo "server-setup: next, from the Mac: deploy/push-secrets.sh $USER_NAME@<server-ip>"
