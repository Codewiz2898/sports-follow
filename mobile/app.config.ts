// The Android app's config (Expo). Its name is sports_follow/app.json's, the one place it's written; its
// icons are the web app's (web/scripts/make_icons.py draws them). The API it talks to is src/api.ts's.
//
// Notifications come through the app's Firebase project (src/push.ts): its google-services.json is never
// committed. It's GOOGLE_SERVICES_JSON's file when that's set, else mobile/google-services.json (gitignored);
// a build without either, or with one that has no Android app for this package in it, runs, without
// notifications.

//
// The site's links open in the app (Android App Links): its pages, read from its routes (src/app), so a new page
// needs no second list. A folder is every address under it, and [slug].tsx is the one-word addresses
// (campaigns, campaigns.SLUG), from Android 12; older phones open those in the browser. Android matches a path as
// it's typed, capitals and all, so that pattern takes them: the server finds a campaign whatever its case. Not the
// site's own one-word pages the app has no screen for (WEB_ONLY: the admin panel), which stay the browser's. Android
// trusts the claim once the site's /.well-known/assetlinks.json names this package and the certificate that signed
// it (README.md, "Android app").

/// <reference types="node" />
import fs from 'node:fs'
import path from 'node:path'
import type { ExpoConfig, ExpoConfigAndroidIntentFiltersData } from 'expo/config'

const { name } = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'sports_follow', 'app.json'), 'utf8')) as { name: string }
if (!name) throw new Error('sports_follow/app.json has no name')

const ICONS = '../web/public/icons'
const PAPER = '#F3F3EF' // web/src/styles.css --bg
const BLUE = '#1F5BEF' // the app's own blue, the badge's (web/scripts/make_icons.py)
const FONTS = 'node_modules/@expo-google-fonts'
const GOOGLE_SERVICES = process.env.GOOGLE_SERVICES_JSON || './google-services.json'
// Play never lets a package id change after the first upload (io.stann.app until the rename to Slam, BHA-348).
const PACKAGE = 'io.slam.app'
// TODO(BHA-348): the new domain once it's bought, with src/api.ts's and the notifications' (Settings.kt) default.
const SITE = 'stann.in'

/** Whether there's a Firebase file and it has an Android app for this package: Google's Gradle plugin stops the
 *  build on one without (a file downloaded before the package changed). */
function firebase(): boolean {
  const file = path.resolve(__dirname, GOOGLE_SERVICES)
  if (!fs.existsSync(file)) return false
  type Client = { client_info?: { android_client_info?: { package_name?: string } } }
  let client: Client[]
  try {
    client = (JSON.parse(fs.readFileSync(file, 'utf8')) as { client?: Client[] }).client ?? []
  } catch (e) {
    throw new Error(`${GOOGLE_SERVICES} isn't a google-services.json Firebase gave: ${String(e)}`)
  }
  if (client.some((c) => c.client_info?.android_client_info?.package_name === PACKAGE)) return true
  console.warn(`${GOOGLE_SERVICES} has no Android app ${PACKAGE}: building without notifications. Add the app to the Firebase project and download its google-services.json again (README.md, "Android app").`)
  return false
}

const FIREBASE = firebase()

// The site's one-word pages that aren't the app's (web/src/main.tsx): its admin panel. The app showed its Not found
// page for stann.in/admin (BUG-157).
const WEB_ONLY = ['admin']
const SLUG = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-' // what a campaign's address is made of

/** An Android pattern's set of these characters ("[a-zA-Z0-9-]"), in ranges. */
function charSet(chars: string[]): string {
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
 * word, and the addresses that go on from it with a character no word does there ("adx…", or "adminx…"). */
function slugsBut(words: string[]): ExpoConfigAndroidIntentFiltersData[] {
  const all = SLUG.split('')
  const cased = (start: string) => start.split('').map((c) => (/[a-z]/.test(c) ? `[${c}${c.toUpperCase()}]` : c)).join('')
  const out: ExpoConfigAndroidIntentFiltersData[] = []
  const walk = (start: string) => {
    const next = [...new Set(words.filter((w) => w.length > start.length && w.startsWith(start)).map((w) => w[start.length]))]
    if (start && !words.includes(start)) out.push({ pathAdvancedPattern: `/${cased(start)}` })
    const rest = all.filter((c) => !next.includes(c.toLowerCase()))
    out.push({ pathAdvancedPattern: `/${cased(start)}${charSet(rest)}${charSet(all)}*` })
    for (const c of next) walk(start + c)
  }
  walk('')
  return out
}

/** The addresses on the site the app has a page for: one per route file, every one under a route folder. */
function pages(): ExpoConfigAndroidIntentFiltersData[] {
  return fs.readdirSync(path.join(__dirname, 'src', 'app'), { withFileTypes: true }).flatMap((f): ExpoConfigAndroidIntentFiltersData[] => {
    const route = f.name.replace(/\.tsx$/, '')
    if (f.isDirectory()) return [{ pathPrefix: `/${route}/` }]
    if (route === 'index') return [{ path: '/' }, { path: '' }] // "https://stann.in/", and without its slash
    if (route === '[slug]') return slugsBut(WEB_ONLY) // as typed: /Gukesh is /gukesh to the server
    return /^[a-z]+$/.test(route) ? [{ path: `/${route}` }] : [] // not _layout or +not-found
  })
}

export default (): ExpoConfig => ({
  name,
  slug: 'slam',
  scheme: 'slam', // the app's own links (a notification's tap, modules/stann-notifications Cards.kt)
  // What Play shows testers; versionCode below is what it orders uploads by.
  version: '0.1.0',
  orientation: 'portrait',
  userInterfaceStyle: 'light',
  backgroundColor: PAPER,
  icon: `${ICONS}/icon-512.png`,
  android: {
    package: PACKAGE,
    // Every upload to Play needs a higher one than the last (store/release-build.md): raise it here, nowhere else.
    versionCode: 1,
    adaptiveIcon: { foregroundImage: `${ICONS}/adaptive-432.png`, monochromeImage: `${ICONS}/adaptive-monochrome-432.png`, backgroundColor: PAPER },
    predictiveBackGestureEnabled: false,
    // expo-network (the Feed's "On Wi-Fi" autoplay) reads the connection's type, which needs only ACCESS_NETWORK_STATE;
    // its Wi-Fi permission is for an IP address the app never asks, so it stays out (store/data-safety.md unchanged).
    blockedPermissions: ['android.permission.ACCESS_WIFI_STATE'],
    intentFilters: [{
      action: 'VIEW',
      autoVerify: true,
      category: ['BROWSABLE', 'DEFAULT'],
      data: [{ scheme: 'https', host: SITE }, ...pages()],
    }],
    ...(FIREBASE ? { googleServicesFile: GOOGLE_SERVICES } : {}),
  },
  ios: { bundleIdentifier: PACKAGE },
  plugins: [
    'expo-router',
    // A release build signed with the Play upload key when the build has it (store/release-build.md).
    './plugins/with-upload-key',
    // The badge on paper while the first screen waits for the sports (catalog.tsx).
    ['expo-splash-screen', { image: `${ICONS}/mark-192.png`, imageWidth: 96, backgroundColor: PAPER }],
    // The status bar's mark for a notification is the badge in white (badge-96), tinted the app's blue. A push on
    // a channel this build hasn't made lands on the quiet one (src/push.ts's QUIET_CHANNEL), not on FCM's own
    // fallback, which rings: a server and an app a channel apart stay silent.
    ['expo-notifications', { icon: `${ICONS}/badge-96.png`, color: BLUE, defaultChannel: 'other' }],
    // The web app's typefaces (styles.css): Anton for display, Manrope for text, Fredoka for the name. Anton
    // comes twice: upright for numbers (.num), and leaning as .dsp does, made by scripts/slant_font.py.
    ['expo-font', {
      fonts: [
        `${FONTS}/anton/400Regular/Anton_400Regular.ttf`,
        './assets/fonts/AntonSlanted.ttf',
        `${FONTS}/manrope/500Medium/Manrope_500Medium.ttf`,
        `${FONTS}/manrope/600SemiBold/Manrope_600SemiBold.ttf`,
        `${FONTS}/manrope/700Bold/Manrope_700Bold.ttf`,
        `${FONTS}/manrope/800ExtraBold/Manrope_800ExtraBold.ttf`,
        `${FONTS}/fredoka/600SemiBold/Fredoka_600SemiBold.ttf`,
      ],
    }],
  ],
  experiments: { typedRoutes: true },
  extra: { firebase: FIREBASE }, // whether this build can have an FCM token (src/push.ts)
})
