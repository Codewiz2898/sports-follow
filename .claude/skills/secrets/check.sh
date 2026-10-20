#!/usr/bin/env bash
# Whether a secret on the Mac is there and looks right, without printing it:
#   .claude/skills/secrets/check.sh <name>      e.g. youtube-key, resend-key, fcm-service-account.json
# Says: present or not, its mode (600 wanted), stray whitespace, its length, and whether it has the shape
# its provider gives (where that's known). A JSON file gets its field names, never their values.
set -euo pipefail
name="${1:?usage: check.sh <name in ~/.config/sports-follow>}"
f="${SPORTS_FOLLOW_CONFIG:-$HOME/.config/sports-follow}/$name"

[ -e "$f" ] || { echo "$name: missing ($f)"; exit 1; }
[ -s "$f" ] || { echo "$name: empty"; exit 1; }
mode=$(stat -f %Lp "$f" 2>/dev/null || stat -c %a "$f")
[ "$mode" = 600 ] || [ "$mode" = 400 ] || echo "$name: mode $mode, should be 600: chmod 600 '$f'"

case "$name" in
  *.json)
    python3 -I - "$f" <<'EOF'
import json, sys
name = sys.argv[1].rsplit("/", 1)[-1]
try:
    data = json.load(open(sys.argv[1]))
except ValueError:
    sys.exit(f"{name}: not JSON")
print(f"{name}: JSON with fields {', '.join(sorted(data)) if isinstance(data, dict) else type(data).__name__}")
EOF
    exit ;;
  *.env)
    echo "$name: variables $(sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p' "$f" | paste -sd ' ' -)"
    exit ;;
esac

value=$(tr -d '[:space:]' <"$f")
[ "$(wc -c <"$f" | tr -d ' ')" = "${#value}" ] || echo "$name: has whitespace or a newline around it (the app strips it; fine)"
case "$name" in
  youtube-key) shape='^AIza[0-9A-Za-z_-]{35}$' ;;      # a Google API key
  resend-key) shape='^re_[0-9A-Za-z_]+$' ;;
  linear-key) shape='^lin_api_[0-9A-Za-z]+$' ;;
  openrouter-key) shape='^sk-or-v1-[0-9a-f]+$' ;;
  pubg-token) shape='^eyJ[0-9A-Za-z_.-]+$' ;;          # a JWT
  *) shape='' ;;
esac
if [ -z "$shape" ]; then
  echo "$name: present, ${#value} characters (no known shape to check)"
elif printf '%s' "$value" | grep -Eq "$shape"; then
  echo "$name: present, ${#value} characters, looks like one"
else
  echo "$name: present, ${#value} characters, but not the shape expected ($shape): copied the right thing?"
  exit 1
fi
