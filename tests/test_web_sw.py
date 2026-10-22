"""The web app's service worker (web/public/sw.js), run in Node with a fake network and fake caches:
- a picture opened as a page (a player's photo, a share picture) never becomes the app's shell, so offline the
  installed app still opens as itself (BUG-164);
- each build's files are kept in a cache of that build's, which goes when the next build takes over: they no longer
  pile up, about 0.7 MB a deploy, on every device (BUG-168); photos stay;
- the sports (/api/sports) come from the network when there is one, so a sport switched on or off in /admin reaches
  the app on its next load, not its third (BUG-166); the last answer offline.
Skipped without Node 22.6 or later."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from .test_web_news_lines import NODE

SW = Path(__file__).resolve().parent.parent / "web" / "public" / "sw.js"
pytestmark = pytest.mark.skipif(NODE is None, reason="needs Node 22.6+")

# A browser around the worker, enough for its install, activate and fetch: caches by name (kept across workers, as
# a browser keeps them), a network whose answers the scenario sets, and a worker made from sw.js for a build.
HARNESS = """
import { readFileSync } from 'node:fs'
const ORIGIN = 'https://stann.in'
const source = readFileSync(%(sw)s, 'utf8')
const stores = new Map()
const keyOf = (k) => new URL(typeof k === 'string' ? k : k.url, ORIGIN).href
const cacheOf = (name) => {
  if (!stores.has(name)) stores.set(name, new Map())
  const m = stores.get(name)
  return {
    put: async (k, r) => { m.set(keyOf(k), r) },
    match: async (k) => m.get(keyOf(k))?.clone(),
    keys: async () => [...m.keys()].map((url) => ({ url })),
    delete: async (k) => m.delete(keyOf(k)),
    addAll: async (paths) => { for (const p of paths) m.set(keyOf(p), await net(p)) },
  }
}
globalThis.caches = { open: async (n) => cacheOf(n), keys: async () => [...stores.keys()], delete: async (n) => stores.delete(n), match: async () => undefined }
const site = new Map()  // path -> [content type, body]
let offline = false
const net = async (req) => {
  if (offline) throw new TypeError('Failed to fetch')
  const path = new URL(typeof req === 'string' ? req : req.url, ORIGIN).pathname
  const [type, body] = site.get(path) ?? ['text/html', '<!doctype html><title>Slam</title>']
  return new Response(body, { status: 200, headers: { 'content-type': type } })
}
globalThis.fetch = net
const worker = (build) => {
  const handlers = {}
  globalThis.self = { location: new URL(ORIGIN), addEventListener: (t, f) => { handlers[t] = f }, clients: { matchAll: async () => [], claim: async () => {} }, registration: {}, skipWaiting: () => {} }
  new Function(source.replace(/const BUILD = '[^']*'/, `const BUILD = '${build}'`))()
  const run = async (type) => { let done; handlers[type]({ waitUntil: (p) => { done = p } }); await done }
  const get = async (path, mode = 'cors') => {
    let answered = null
    handlers.fetch({ request: { method: 'GET', url: ORIGIN + path, mode, headers: new Headers({ accept: mode === 'navigate' ? 'text/html' : '*/*' }) }, respondWith: (p) => { answered = p } })
    const res = await (answered ?? net(path))  // one the worker leaves alone goes to the network
    return { type: res.headers.get('content-type'), body: await res.text(), handled: answered !== null }
  }
  return { install: () => run('install'), activate: () => run('activate'), get }
}
const kept = (name) => [...(stores.get(name)?.keys() ?? [])].map((url) => new URL(url).pathname).sort()
"""

SHELL = """
site.set('/api/photos/ab12.jpg', ['image/jpeg', 'JPEG'])
site.set('/api/share.png', ['image/png', 'PNG'])
site.set('/icons/icon-512.png', ['image/png', 'PNG'])
const w = worker('one')
await w.install(); await w.activate()
const opened = []
for (const path of ['/player/14', '/api/share.png', '/api/photos/ab12.jpg', '/icons/icon-512.png']) opened.push([path, (await w.get(path, 'navigate')).type])
const shell = (await caches.open('shell-v3').then((c) => c.match('/'))).headers.get('content-type')
offline = true
const later = [(await w.get('/me/feed', 'navigate')).type, (await w.get('/', 'navigate')).type]
console.log(JSON.stringify({ opened, shell, later }))
"""

BUILDS = """
site.set('/assets/index-one.js', ['text/javascript', 'one'])
site.set('/assets/chess-one.js', ['text/javascript', 'chess one'])
site.set('/assets/index-two.js', ['text/javascript', 'two'])
site.set('/api/photos/ab12.jpg', ['image/jpeg', 'JPEG'])
// A device that had the app before builds kept their own files: its files cache still has a build's.
await caches.open('files-v3').then((c) => c.put('/assets/index-zero.js', new Response('zero')))
const one = worker('one')
await one.install(); await one.activate()
for (const path of ['/assets/index-one.js', '/assets/chess-one.js', '/api/photos/ab12.jpg']) await one.get(path)
const first = { caches: [...stores.keys()].sort(), files: [...kept('files-v3'), ...Object.values(Object.fromEntries([...stores].filter(([n]) => n.startsWith('assets-')).map(([n]) => [n, kept(n)]))).flat()].sort() }
// The next deploy: its worker installs beside the one in charge, and takes over on Reload.
const two = worker('two')
await two.install()
const waiting = [...stores.keys()].filter((n) => n.startsWith('assets-')).sort()
await two.activate()
await two.get('/assets/index-two.js')
const photo = await two.get('/api/photos/ab12.jpg')
const after = Object.fromEntries([...stores.keys()].sort().map((n) => [n, kept(n)]))
console.log(JSON.stringify({ first, waiting, after, photo: photo.body }))
"""

SPORTS = """
const w = worker('one')
await w.install(); await w.activate()
site.set('/api/sports', ['application/json', JSON.stringify({ on: ['football', 'cricket'] })])
const before = JSON.parse((await w.get('/api/sports')).body).on
site.set('/api/sports', ['application/json', JSON.stringify({ on: ['cricket'] })])  // football switched off in /admin
const next = JSON.parse((await w.get('/api/sports')).body).on
offline = true
const away = JSON.parse((await w.get('/api/sports')).body).on
console.log(JSON.stringify({ before, next, away }))
"""


def run(scenario: str) -> dict:
    script = HARNESS % {"sw": json.dumps(str(SW))} + scenario
    done = subprocess.run([NODE or "node", "--no-warnings", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_a_picture_opened_as_a_page_never_becomes_the_apps_shell():
    found = run(SHELL)
    # Each opens as itself; a picture of the API's isn't the worker's to answer.
    assert found["opened"] == [["/player/14", "text/html"], ["/api/share.png", "image/png"], ["/api/photos/ab12.jpg", "image/jpeg"], ["/icons/icon-512.png", "image/png"]]
    assert found["shell"] == "text/html"
    assert found["later"] == ["text/html", "text/html"]  # offline, the app opens as the app


def test_each_builds_files_go_when_the_next_build_takes_over_and_photos_stay():
    found = run(BUILDS)
    # A build's files, in its own cache; what an earlier one left in the files cache is gone.
    assert found["first"]["files"] == ["/api/photos/ab12.jpg", "/assets/chess-one.js", "/assets/index-one.js"]
    assert "/assets/index-zero.js" not in found["first"]["files"]
    # The new build's worker, waiting, leaves the one in charge its files; taking over, it drops them.
    assert found["waiting"] == ["assets-one"]
    after = found["after"]
    assert not any("one" in path for paths in after.values() for path in paths)
    assert [n for n in after if n.startswith("assets-")] == ["assets-two"] and after["assets-two"] == ["/assets/index-two.js"]
    assert after["files-v3"] == ["/api/photos/ab12.jpg"] and found["photo"] == "JPEG"


def test_the_sports_come_from_the_network_and_the_last_answer_offline():
    found = run(SPORTS)
    assert found["before"] == ["football", "cricket"]
    assert found["next"] == ["cricket"]  # the load after the switch, not the third
    assert found["away"] == ["cricket"]
