#!/usr/bin/env bash
# Whether a secret on the Mac is there and looks right, without printing it:
#   .claude/skills/secrets/check.sh <name>      e.g. youtube-key, resend-key, fcm-service-account.json
# Says: present or not, its mode (600 wanted), stray whitespace, its length, and whether it has the shape
# its provider gives (where that's known). A JSON file gets its field names and an .env file its variable
# names, never their values. The value is never held in a variable, so even `bash -x` doesn't show it.
set -euo pipefail
name="${1:?usage: check.sh <name in ~/.config/sports-follow>}"
f="${SPORTS_FOLLOW_CONFIG:-$HOME/.config/sports-follow}/$name"

[ -e "$f" ] || { echo "$name: missing ($f)"; exit 1; }
[ -s "$f" ] || { echo "$name: empty"; exit 1; }
mode=$(stat -c %a "$f" 2>/dev/null || stat -f %Lp "$f")   # GNU first: BSD stat rejects -c cleanly
[ "$mode" = 600 ] || [ "$mode" = 400 ] || echo "$name: mode $mode, should be 600: chmod 600 '$f'"

case "$name" in
  *.json)
    python3 -I - "$f" <<'EOF'
import json, sys
path = sys.argv[1]
name = path.rsplit("/", 1)[-1]
try:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
except (OSError, ValueError):
    sys.exit(f"{name}: can't be read as JSON")
print(f"{name}: JSON with fields {', '.join(sorted(data)) if isinstance(data, dict) else type(data).__name__}")
EOF
    exit ;;
  *.env)
    echo "$name: variables $(sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p' "$f" | paste -sd ' ' -)"
    exit ;;
esac

# Whitespace: around the value the app strips it (fine); inside it, or a second line, the key is broken.
[ "$(grep -c '[^[:space:]]' "$f" || true)" = 1 ] ||
  { echo "$name: more than one line: copied more than the key?"; exit 1; }
[ "$(sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' "$f" | grep -c '[[:space:]]' || true)" = 0 ] ||
  { echo "$name: has whitespace inside it: copied more than the key?"; exit 1; }
[ "$(wc -c <"$f" | tr -d ' ')" = "$(tr -d '[:space:]' <"$f" | wc -c | tr -d ' ')" ] ||
  echo "$name: has a newline or spaces around it (the app strips them: fine)"
length=$(tr -d '[:space:]' <"$f" | wc -c | tr -d ' ')

case "$name" in
  youtube-key) shape='^AIza[0-9A-Za-z_-]{35}$' ;;      # a Google API key
  resend-key) shape='^re_[0-9A-Za-z_]+$' ;;
  linear-key) shape='^lin_api_[0-9A-Za-z]+$' ;;
  openrouter-key) shape='^sk-or-v1-[0-9a-f]+$' ;;
  pubg-token) shape='^eyJ[0-9A-Za-z_.-]+$' ;;          # a JWT
  *) shape='' ;;
esac
if [ -z "$shape" ]; then
  echo "$name: present, $length characters (no known shape to check)"
elif tr -d '[:space:]' <"$f" | grep -Eq "$shape"; then
  echo "$name: present, $length characters, looks like one"
else
  echo "$name: present, $length characters, but not the shape expected ($shape): copied the right thing?"
  exit 1
fi
