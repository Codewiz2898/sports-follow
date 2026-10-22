// The site's addresses the Android app claims (Android App Links), for app.config.ts, which says how; apart from it
// so the tests can read them with Node alone (tests/test_mobile_app_links.py). CommonJS, as plugins/ is: Expo reads
// app.config.ts's own imports with require.
const fs = require('node:fs')

/** @typedef {{ path?: string, pathPrefix?: string, pathAdvancedPattern?: string }} Claimed */

// The site's one-word pages that aren't the app's (web/src/main.tsx): its admin panel. The app showed its Not found
// page for stann.in/admin (BUG-157).
const WEB_ONLY = ['admin']
const SLUG = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-' // what a campaign's address is made of

/** An Android pattern's set of these characters ("[a-zA-Z0-9-]"), in ranges.
 * @param {string[]} chars
 * @returns {string} */
function charSet(chars) {
  const codes = SLUG.split('').filter((c) => chars.includes(c) && c !== '-').map((c) => c.charCodeAt(0))
  let out = ''
  for (let i = 0; i < codes.length; i++) {
    let j = i
    while (j + 1 < codes.length && codes[j + 1] === codes[j] + 1) j++
    out += j > i ? `${String.fromCharCode(codes[i])}-${String.fromCharCode(codes[j])}` : String.fromCharCode(codes[i])
    i = j
  }
  return `[${out}${chars.includes('-') ? '-' : ''}]`
}

/** Patterns for every one-word address but `words`, in any case: Android's patterns can't say "but not", so they
 * are spelled out letter by letter. For each start of a word ("", "a", "ad"…): the start itself where it isn't a
 * word, and the addresses that go on from it with a character no word does there ("adx…", or "adminx…").
 * @param {string[]} words
 * @returns {Claimed[]} */
function slugsBut(words) {
  const all = SLUG.split('')
  /** @param {string} start */
  const cased = (start) => start.split('').map((c) => (/[a-z]/.test(c) ? `[${c}${c.toUpperCase()}]` : c)).join('')
  /** @type {Claimed[]} */
  const out = []
  /** @param {string} start */
  const walk = (start) => {
    const next = [...new Set(words.filter((w) => w.length > start.length && w.startsWith(start)).map((w) => w[start.length]))]
    if (start && !words.includes(start)) out.push({ pathAdvancedPattern: `/${cased(start)}` })
    const rest = all.filter((c) => !next.includes(c.toLowerCase()))
    out.push({ pathAdvancedPattern: `/${cased(start)}${charSet(rest)}${charSet(all)}*` })
    for (const c of next) walk(start + c)
  }
  walk('')
  return out
}

/** The addresses on the site the app has a page for, from its routes (`routes`, src/app): one per route file, every
 * one under a route folder, and for [slug].tsx the one-word addresses but WEB_ONLY, as typed (/Gukesh is /gukesh to
 * the server).
 * @param {string} routes
 * @returns {Claimed[]} */
function pages(routes) {
  return fs.readdirSync(routes, { withFileTypes: true }).flatMap((f) => {
    const route = f.name.replace(/\.tsx$/, '')
    if (f.isDirectory()) return [{ pathPrefix: `/${route}/` }]
    if (route === 'index') return [{ path: '/' }, { path: '' }] // "https://stann.in/", and without its slash
    if (route === '[slug]') return slugsBut(WEB_ONLY)
    return /^[a-z]+$/.test(route) ? [{ path: `/${route}` }] : [] // not _layout or +not-found
  })
}

module.exports = { WEB_ONLY, pages }
