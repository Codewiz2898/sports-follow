#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 24.04 server, as root: security updates, a firewall, swap, Docker,
# and this repo in /opt/sports-follow. From the Mac:
#   ssh root@<server-ip> 'bash -s' < deploy/server-setup.sh
# DOMAIN defaults to <server-ip>.sslip.io (real HTTPS, no domain needed); pass DOMAIN=app.example.com
# in front of bash -s to use your own. Safe to run again.
set -euo pipefail
REPO=https://github.com/Codewiz2898/sports-follow.git
APP_DIR=/opt/sports-follow
export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get -y upgrade
apt-get install -y ca-certificates curl git openssl ufw unattended-upgrades
echo 'APT::Periodic::Update-Package-Lists "1"; APT::Periodic::Unattended-Upgrade "1";' >/etc/apt/apt.conf.d/20auto-upgrades

# Firewall: SSH and the web only (Docker publishes just Caddy's 80 and 443).
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
ufw --force enable

# 2 GB of swap, so a registry import or a build never meets the out-of-memory killer.
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  grep -q /swapfile /etc/fstab || echo '/swapfile none swap sw 0 0' >>/etc/fstab
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

# The app, its secrets directory (filled by push-secrets.sh) and its backups.
[ -d "$APP_DIR/.git" ] || git clone "$REPO" "$APP_DIR"
install -d -m 700 /etc/sports-follow
install -d -m 700 /var/backups/sports-follow
if [ ! -f "$APP_DIR/deploy/.env" ]; then
  ip=$(curl -4 -fsS https://api.ipify.org)
  domain="${DOMAIN:-${ip//./-}.sslip.io}"
  printf 'DOMAIN=%s\nSECRETS_DIR=/etc/sports-follow\nBACKUP_DIR=/var/backups/sports-follow\n' "$domain" >"$APP_DIR/deploy/.env"
fi
echo "server-setup: done. The app will be at https://$(grep '^DOMAIN=' "$APP_DIR/deploy/.env" | cut -d= -f2)"
echo "server-setup: next, from the Mac: deploy/push-secrets.sh root@<server-ip>"
