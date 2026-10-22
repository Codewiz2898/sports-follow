"""The web app's service worker (web/public/sw.js), run in Node with a fake network and fake caches:
- a picture opened as a page (a player's photo, a share picture) never becomes the app's shell, so offline the
  installed app still opens as itself (BUG-164);
- each build's files are kept in a cache of that build's, which goes when the next build takes over: they no longer
  pile up, about 0.7 MB a deploy, on every device (BUG-168); photos stay;
- the sports (/api/sports) come from the network when there is one, so a sport switched on or off in /admin reaches
  the app on its next load, not its third (BUG-166); the last answer offline;
- a worker stores its build's page and files as it installs (the list vite.config.ts stamps in), so the app opens
  offline from a fan's first visit (BUG-310) and right after a deploy, as one build's page and that build's files
  (BUG-305), the chess engine kept from the build before when it's the same file.
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
    // As a browser's: all of them or none, and a failed answer fails it.
    addAll: async (paths) => {
      const got = []
      for (const p of paths) {
        const res = await net(p)
        if (!res.ok) throw new TypeError(`addAll: ${p} answered ${res.status}`)
        got.push([keyOf(p), res])
      }
      for (const [k, res] of got) m.set(k, res)
    },
  }
}
// caches.match looks in every cache, oldest first.
const anyCache = async (k) => { for (const m of stores.values()) if (m.has(keyOf(k))) return m.get(keyOf(k)).clone() }
globalThis.caches = { open: async (n) => cacheOf(n), keys: async () => [...stores.keys()], delete: async (n) => stores.delete(n), match: anyCache }
const site = new Map()  // path -> [content type, body], or null: gone (a 404)
let offline = false
const net = async (req) => {
  if (offline) throw new TypeError('Failed to fetch')
  const path = new URL(typeof req === 'string' ? req : req.url, ORIGIN).pathname
  if (site.get(path) === null) return new Response('Not Found', { status: 404, headers: { 'content-type': 'text/plain' } })
  const [type, body] = site.get(path) ?? ['text/html', '<!doctype html><title>Slam</title>']
  return new Response(body, { status: 200, headers: { 'content-type': type } })
}
globalThis.fetch = net
// A build's worker, its build and its files stamped in as vite.config.ts does: those stored as it installs, and those
// stored when first used.
const worker = (build, files = [], later = []) => {
  const handlers = {}
  globalThis.self = { location: new URL(ORIGIN), addEventListener: (t, f) => { handlers[t] = f }, clients: { matchAll: async () => [], claim: async () => {} }, registration: {}, skipWaiting: () => {} }
  new Function(source.replace(/const BUILD = '[^']*'/, `const BUILD = '${build}'`)
    .replace(/const BUILD_FILES = \\[[^\\]]*\\]/, `const BUILD_FILES = ${JSON.stringify(files)}`)
    .replace(/const BUILD_FILES_LATER = \\[[^\\]]*\\]/, `const BUILD_FILES_LATER = ${JSON.stringify(later)}`))()
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
// What a worker answers for each path: the body, or "failed" (offline with nothing kept).
const answers = async (w, paths) => { const out = []; for (const p of paths) out.push(await w.get(p).then((r) => r.body, () => 'failed')); return out }
"""

SHELL = """
site.set('/api/photos/ab12.jpg', ['image/jpeg', 'JPEG'])
site.set('/api/share.png', ['image/png', 'PNG'])
site.set('/icons/icon-512.png', ['image/png', 'PNG'])
const w = worker('one')
await w.install(); await w.activate()
const opened = []
for (const path of ['/player/14', '/api/share.png', '/api/photos/ab12.jpg', '/icons/icon-512.png']) opened.push([path, (await w.get(path, 'navigate')).type])
const shell = (await caches.open('shell-one').then((c) => c.match('/'))).headers.get('content-type')
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

# A build: its page, which needs its files, and the chess engine, too big to store unasked (stored on first use).
BUILT = """
const build = (name, engine = 'engine-one') => {
  const files = [`/assets/index-${name}.js`, `/assets/index-${name}.css`, `/assets/chess-${name}.js`]
  site.set('/', ['text/html', `page ${name}`])
  for (const f of files) site.set(f, ['text/javascript', f])
  site.set(`/assets/${engine}.wasm`, ['application/wasm', engine])
  return { files, later: [`/assets/${engine}.wasm`] }
}
"""

FIRST_VISIT = BUILT + """
const one = build('one')
const w = worker('one', one.files, one.later)
// A fan's first visit: the page isn't the worker's yet, so none of its files are asked for through it.
await w.install(); await w.activate()
offline = true
console.log(JSON.stringify({ page: (await w.get('/', 'navigate')).body, files: await answers(w, one.files), engine: await answers(w, one.later) }))
"""

DEPLOY = BUILT + """
// Build one in charge; the fan has used the app, the chess engine once.
const one = build('one')
const w1 = worker('one', one.files, one.later)
await w1.install(); await w1.activate()
await w1.get('/', 'navigate'); await answers(w1, [...one.files, ...one.later])
// The deploy: the site serves build two, and one's files are gone from it. The engine hasn't changed: the same file.
for (const f of one.files) site.set(f, null)
const two = build('two')
// The fan opens the app online: two's page, under one's worker still, which asks for two's files.
await w1.get('/', 'navigate'); await answers(w1, two.files)
// Two's worker installs and waits. Gone offline before it could, the app opens as one, whole.
const w2 = worker('two', two.files, two.later)
offline = true
await w2.install().catch(() => {})
const before = { page: (await w1.get('/', 'navigate')).body, files: await answers(w1, one.files) }
// Online again: two installs. The fan closes the app without Reload, and next opens it offline: two takes over.
offline = false
await w2.install()
offline = true
await w2.activate()
const after = { page: (await w2.get('/', 'navigate')).body, files: await answers(w2, two.files), engine: await answers(w2, two.later), caches: [...stores.keys()].sort() }
console.log(JSON.stringify({ before, after }))
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
    # The new build's worker, waiting, stores its own beside those of the one in charge, which stay; taking over, it
    # drops them.
    assert found["waiting"] == ["assets-one", "assets-two"]
    after = found["after"]
    assert not any("one" in path for paths in after.values() for path in paths)
    assert [n for n in after if n.startswith("assets-")] == ["assets-two"] and after["assets-two"] == ["/assets/index-two.js"]
    assert after["files-v3"] == ["/api/photos/ab12.jpg"] and found["photo"] == "JPEG"


def test_the_sports_come_from_the_network_and_the_last_answer_offline():
    found = run(SPORTS)
    assert found["before"] == ["football", "cricket"]
    assert found["next"] == ["cricket"]  # the load after the switch, not the third
    assert found["away"] == ["cricket"]


def test_a_first_visit_stores_the_apps_page_and_files_so_it_opens_offline():
    """BUG-310: the first page isn't the worker's, so nothing it loaded was kept, and an app installed then opened
    blank offline. The worker stores its build's page and files as it installs; the chess engine waits for its first use."""
    found = run(FIRST_VISIT)
    assert found["page"] == "page one"
    assert found["files"] == ["/assets/index-one.js", "/assets/index-one.css", "/assets/chess-one.js"]
    assert found["engine"] == ["failed"]


def test_after_a_deploy_the_app_opens_offline_as_one_build_whole():
    """BUG-305: one's worker kept two's files in one's cache, which two's worker dropped as it took over, and the shell
    (shared, overwritten on every load) was two's page: offline after a deploy, the app opened blank. Each build's
    worker stores its own page and files as it installs, and a page opened online doesn't replace the build's own."""
    found = run(DEPLOY)
    # Two not installed yet: one's page, with one's files.
    assert found["before"] == {"page": "page one", "files": ["/assets/index-one.js", "/assets/index-one.css", "/assets/chess-one.js"]}
    after = found["after"]
    assert after["page"] == "page two"
    assert after["files"] == ["/assets/index-two.js", "/assets/index-two.css", "/assets/chess-two.js"]
    assert after["engine"] == ["engine-one"]  # the same file as one's: kept, not fetched again or lost
    assert [n for n in after["caches"] if n.startswith(("assets-", "shell-"))] == ["assets-two", "shell-two"]  # one's went as two took over
