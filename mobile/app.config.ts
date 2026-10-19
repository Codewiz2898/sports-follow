// The Android app's config (Expo). Its name is stann's, from sports_follow/app.json, the one place it's
// written; its icons are the web app's (web/scripts/make_icons.py draws them). The API it talks to is
// src/api.ts's.
//
// Notifications come through the app's Firebase project (src/push.ts): its google-services.json is never
// committed. It's GOOGLE_SERVICES_JSON's file when that's set, else mobile/google-services.json (gitignored);
// a build without either runs, without notifications.

//
// stann.in links open in the app (Android App Links): its pages, read from its routes (src/app), so a new page
// needs no second list. A folder is every address under it, and [slug].tsx is the one-word addresses
// (campaigns, campaigns.SLUG), from Android 12; older phones open those in the browser. Android trusts the
// claim once the site's /.well-known/assetlinks.json names this package and the certificate that signed it
// (README.md, "Android app").

/// <reference types="node" />
import fs from 'node:fs'
import path from 'node:path'
import type { ExpoConfig, ExpoConfigAndroidIntentFiltersData } from 'expo/config'

const { name } = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'sports_follow', 'app.json'), 'utf8')) as { name: string }
if (!name) throw new Error('sports_follow/app.json has no name')

const ICONS = '../web/public/icons'
const PAPER = '#F3F3EF' // web/src/styles.css --bg
const BLUE = '#1F5BEF' // stann's own blue, the badge's (web/scripts/make_icons.py)
const FONTS = 'node_modules/@expo-google-fonts'
const GOOGLE_SERVICES = process.env.GOOGLE_SERVICES_JSON || './google-services.json'
const FIREBASE = fs.existsSync(path.resolve(__dirname, GOOGLE_SERVICES))
const SITE = 'stann.in'

/** The addresses on the site the app has a page for: one per route file, every one under a route folder. */
function pages(): ExpoConfigAndroidIntentFiltersData[] {
  return fs.readdirSync(path.join(__dirname, 'src', 'app'), { withFileTypes: true }).flatMap((f): ExpoConfigAndroidIntentFiltersData[] => {
    const route = f.name.replace(/\.tsx$/, '')
    if (f.isDirectory()) return [{ pathPrefix: `/${route}/` }]
    if (route === 'index') return [{ path: '/' }, { path: '' }] // "https://stann.in/", and without its slash
    if (route === '[slug]') return [{ pathAdvancedPattern: '/[a-z0-9-]+' }]
    return /^[a-z]+$/.test(route) ? [{ path: `/${route}` }] : [] // not _layout or +not-found
  })
}

export default (): ExpoConfig => ({
  name,
  slug: 'stann',
  scheme: 'stann',
  // What Play shows testers; versionCode below is what it orders uploads by.
  version: '0.1.0',
  orientation: 'portrait',
  userInterfaceStyle: 'light',
  backgroundColor: PAPER,
  icon: `${ICONS}/icon-512.png`,
  android: {
    // Play never lets a package id change after the first upload (decided 16 Oct 2026, BHA-210).
    package: 'io.stann.app',
    // Every upload to Play needs a higher one than the last (store/release-build.md): raise it here, nowhere else.
    versionCode: 1,
    adaptiveIcon: { foregroundImage: `${ICONS}/adaptive-432.png`, monochromeImage: `${ICONS}/adaptive-monochrome-432.png`, backgroundColor: PAPER },
    predictiveBackGestureEnabled: false,
    intentFilters: [{
      action: 'VIEW',
      autoVerify: true,
      category: ['BROWSABLE', 'DEFAULT'],
      data: [{ scheme: 'https', host: SITE }, ...pages()],
    }],
    ...(FIREBASE ? { googleServicesFile: GOOGLE_SERVICES } : {}),
  },
  ios: { bundleIdentifier: 'io.stann.app' },
  plugins: [
    'expo-router',
    // A release build signed with the Play upload key when the build has it (store/release-build.md).
    './plugins/with-upload-key',
    // The badge on paper while the first screen waits for the sports (catalog.tsx).
    ['expo-splash-screen', { image: `${ICONS}/mark-192.png`, imageWidth: 96, backgroundColor: PAPER }],
    // The status bar's mark for a notification is the badge in white (badge-96), tinted stann's blue. A push on
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
