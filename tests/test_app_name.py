"""The app's name as fans see it is written once, in sports_follow/app.json (config.APP_NAME), so the next
rename is a one-line change (README, "Renaming the app"). The API reads it there; the web build fills it
in for %APP_NAME% (web/vite.config.ts) and APP_NAME (web/src/app.ts); android/generate.mjs gives it to
the Android app. These tests fail if anything fans see writes the name out itself. Comments don't count,
and nor does the domain ("stann.in"), which is a different setting."""

from __future__ import annotations

import io
import json
import re
import tokenize
from pathlib import Path
from types import SimpleNamespace

from sports_follow import mail
from sports_follow.config import APP_NAME
from tests.test_api import api  # noqa: F401  (the fixture)

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "sports_follow"
WEB = ROOT / "web"
# The name as a word of its own, not part of an address ("stann.in") or a longer word.
NAME = re.compile(rf"(?<![\w.@-]){re.escape(APP_NAME)}(?![\w@-]|\.\w)", re.I)
# A comment in TypeScript, or a one-line string (kept, so a "//" inside a string isn't a comment).
TS_COMMENTS = re.compile(r"""//[^\n]*|/\*.*?\*/|(["'`])(?:\\.|(?!\1)[^\\\n])*\1""", re.S)


def without_comments(ts: str) -> str:
    return TS_COMMENTS.sub(lambda m: m[0] if m[1] else "", ts)


def test_the_name_is_written_once():
    assert APP_NAME and APP_NAME == json.loads((PACKAGE / "app.json").read_text(encoding="utf-8"))["name"]
    # The web build puts it in as written, into HTML, a JSON string and a quoted JS string (vite.config.ts).
    assert not re.search(r"""['"`&<>\\]""", APP_NAME)


def test_the_web_apps_files_are_filled_with_the_name_at_build_time():
    for rel in ("index.html", "public/manifest.webmanifest", "public/sw.js"):
        text = (WEB / rel).read_text()
        assert "%APP_NAME%" in text, f"web/{rel} should say %APP_NAME% where the name goes"
        assert not NAME.search(without_comments(text) if rel.endswith(".js") else text), f"web/{rel} writes out {APP_NAME!r}: use %APP_NAME%"


def test_the_web_apps_code_takes_the_name_from_app_ts():
    found = []
    for path in sorted(p for p in (WEB / "src").rglob("*") if p.suffix in (".ts", ".tsx")):
        code = without_comments(path.read_text())
        found += [f"web/{path.relative_to(WEB).as_posix()}: {m[0]!r}" for m in NAME.finditer(code)]
    assert not found, "Use APP_NAME (web/src/app.ts) instead of writing the name out:\n" + "\n".join(found)


def test_the_android_app_is_named_by_generate_mjs():
    manifest = json.loads((ROOT / "android" / "twa-manifest.json").read_text())
    assert "name" not in manifest and "launcherName" not in manifest  # android/generate.mjs sets both
    assert not NAME.search(json.dumps(manifest))


def test_the_apis_strings_take_the_name_from_config():
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        for tok in tokenize.generate_tokens(io.StringIO(path.read_text()).readline):
            if tok.type in (tokenize.STRING, tokenize.FSTRING_MIDDLE) and NAME.search(tok.string):
                found.append(f"{path.relative_to(ROOT)}:{tok.start[0]}: {tok.string[:60]!r}")
    assert not found, "Use config.APP_NAME instead of writing the name out:\n" + "\n".join(found)


def test_the_api_and_the_sign_in_email_say_the_name(api, monkeypatch):  # noqa: F811
    assert api.call("GET", "/api/config").json()["name"] == APP_NAME
    sent = []
    monkeypatch.setattr(mail, "_key", lambda: "key")
    monkeypatch.setattr(mail, "MAIL_FROM", "signin@example.com")
    monkeypatch.setattr(mail._client, "post", lambda url, headers, json: sent.append(json) or SimpleNamespace(status_code=200, text=""))
    mail.send_code("fan@example.com", "123456")
    assert sent[0]["from"] == f"{APP_NAME} <signin@example.com>"
    assert sent[0]["subject"] == f"123456 is your {APP_NAME} code"
    assert APP_NAME in sent[0]["text"] and APP_NAME in sent[0]["html"]
    monkeypatch.setattr(mail, "MAIL_FROM", "Sign-in <signin@example.com>")  # a sender given whole is sent as it is
    mail.send_code("fan@example.com", "123456")
    assert sent[1]["from"] == "Sign-in <signin@example.com>"

