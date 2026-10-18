"""Stops a session from deploying to production on its own: approving a deploy is Bharath's.

Every deploy waits at GitHub's `production` environment for his approval (deploy/README.md, "Deploying").
`gh` runs as his account, so an approval a session sends through the API counts as his, and the review
it stands for never happens (18 Oct 2026). This blocks what would get past that gate:

- approving or rejecting a waiting deploy: a write to `actions/runs/<id>/pending_deployments` (or its
  `deployment_protection_rule`), from `gh api` or curl, or the GraphQL approveDeployments/rejectDeployments;
- changing an environment (its required reviewers): a write to `repos/<owner>/<repo>/environments/...`;
- `deploy.sh deploy` or `deploy.sh rollback`, here or on the server through ssh.

Reading them goes through (`gh api .../pending_deployments`, `deploy.sh status`), and so does starting a
Deploy run (`gh workflow run deploy.yml`), which waits for his approval like any other.

A PreToolUse hook on every Bash call in this project: exit 2 blocks the call and stderr goes to Claude.
A command it can't read goes through, as with the other hooks.
"""

from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bash_words import parse  # noqa: E402

TELL = ("Approving or rejecting a production deploy, changing its environment, and running deploy.sh deploy or "
        "rollback are Bharath's. Give him the Deploy run's link "
        "(https://github.com/Codewiz2898/sports-follow/actions/workflows/deploy.yml) and what it brings: every "
        "merge since the live commit, and any migration, secrets or server change. He approves it there.")

GATES = ("pending_deployments", "deployment_protection_rule")
MUTATIONS = re.compile(r"\b(approve|reject)Deployments\b", re.IGNORECASE)
ENVIRONMENT = re.compile(r"repos/[^/\s]+/[^/\s]+/environments(/|\s|$)")
# Options of `gh api` that send fields, which make its request a POST unless --method says otherwise.
FIELDS = ("-f", "-F", "--field", "--raw-field", "--input")


def gh_api_method(words: list[str]) -> str:
    """The HTTP method a `gh api` call sends."""
    method, fields = None, False
    for i, w in enumerate(words):
        following = words[i + 1] if i + 1 < len(words) else ""
        if w in ("-X", "--method"):
            method = following
        elif w.startswith("--method="):
            method = w.split("=", 1)[1]
        elif w.startswith("-X") and len(w) > 2:
            method = w[2:]
        elif w in FIELDS or any(w.startswith(f"{f}=") for f in FIELDS if f.startswith("--")):
            fields = True
    return (method or ("POST" if fields else "GET")).upper()


def curl_writes(words: list[str]) -> bool:
    """Whether a curl call sends anything but a GET."""
    for i, w in enumerate(words):
        following = words[i + 1] if i + 1 < len(words) else ""
        if w in ("-X", "--request"):
            return following.upper() not in ("GET", "HEAD")
        if w.startswith("-X") and len(w) > 2:
            return w[2:].upper() not in ("GET", "HEAD")
        if w.startswith(("-d", "--data", "--json", "-F", "--form", "-T", "--upload-file")):
            return True
    return False


def field_texts(words: list[str], heredocs: list[str], cwd: str) -> list[str]:
    """What a `gh api` call's fields read from files (query=@q.graphql, --input body.json) or stdin (@-)."""
    texts: list[str] = []
    for i, w in enumerate(words):
        path = w.split("=@", 1)[1] if "=@" in w else words[i + 1] if w == "--input" and i + 1 < len(words) else None
        if path in ("-", None):
            texts += heredocs if path == "-" else []
            continue
        try:
            with open(os.path.join(cwd, os.path.expanduser(path)), encoding="utf-8") as f:
                texts.append(f.read())
        except OSError:
            continue
    return texts


def runs_deploy(words: list[str]) -> bool:
    return any(os.path.basename(w) == "deploy.sh" and words[i + 1:i + 2] in (["deploy"], ["rollback"])
               for i, w in enumerate(words))


def blocked(command: str, cwd: str) -> bool:
    parsed = parse(command)
    remote: list[str] = []  # what ssh or a shell runs, read as commands of their own
    for simple in parsed.commands:
        words = simple.words
        text = " ".join(words)
        if runs_deploy(words):
            return True
        if simple.name in ("ssh", "bash", "sh", "zsh"):
            remote.append(" ".join(words[1:]))  # `ssh host "deploy/deploy.sh deploy"`, `bash -c "..."`
            remote += parsed.heredocs  # `ssh host bash -s <<'EOF'`: the body runs there
        if simple.name == "gh" and words[1:2] == ["api"]:
            if any(MUTATIONS.search(t) for t in [text, *field_texts(words, parsed.heredocs, cwd)]):
                return True
            if gh_api_method(words) != "GET" and (any(g in text for g in GATES) or ENVIRONMENT.search(text)):
                return True
        if simple.name == "curl" and curl_writes(words) and (any(g in text for g in GATES) or ENVIRONMENT.search(text)):
            return True
    for script in remote:
        try:
            if any(runs_deploy(s.words) for s in parse(script).commands):
                return True
        except ValueError:
            continue
    return False


def main() -> int:
    try:
        call = json.load(sys.stdin)
        command = call.get("tool_input", {}).get("command") or ""
        lowered = command.lower()
        if not ("deploy" in lowered or "environments" in lowered or ("graphql" in lowered and "@" in command)):
            return 0
        stop = blocked(command, call.get("cwd") or os.getcwd())
    except Exception:
        return 0
    if stop:
        print(TELL, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
