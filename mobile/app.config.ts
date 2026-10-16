// The Android app's config (Expo). Its name is stann's, from sports_follow/app.json, the one place it's
// written; its icons are the web app's (web/scripts/make_icons.py draws them). The API it talks to is
// src/api.ts's.
//
// Notifications come through the app's Firebase project (src/push.ts): its google-services.json is never
// committed. It's GOOGLE_SERVICES_JSON's file when that's set, else mobile/google-services.json (gitignored);
// a build without either runs, without notifications.

/// <reference types="node" />
import fs from 'node:fs'
import path from 'node:path'
import type { ExpoConfig } from 'expo/config'

const { name } = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'sports_follow', 'app.json'), 'utf8')) as { name: string }
if (!name) throw new Error('sports_follow/app.json has no name')

const ICONS = '../web/public/icons'
const PAPER = '#F3F3EF' // web/src/styles.css --bg
const BLUE = '#1F5BEF' // stann's own blue, the badge's (web/scripts/make_icons.py)
const FONTS = 'node_modules/@expo-google-fonts'
const GOOGLE_SERVICES = process.env.GOOGLE_SERVICES_JSON || './google-services.json'
const FIREBASE = fs.existsSync(path.resolve(__dirname, GOOGLE_SERVICES))

export default (): ExpoConfig => ({
  name,
  slug: 'stann',
  scheme: 'stann',
  version: '0.1.0',
  orientation: 'portrait',
  userInterfaceStyle: 'light',
  backgroundColor: PAPER,
  icon: `${ICONS}/icon-512.png`,
  android: {
    // Play never lets a package id change after the first upload (decided 16 Oct 2026, BHA-210).
    package: 'io.stann.app',
    adaptiveIcon: { foregroundImage: `${ICONS}/adaptive-432.png`, monochromeImage: `${ICONS}/adaptive-monochrome-432.png`, backgroundColor: PAPER },
    predictiveBackGestureEnabled: false,
    ...(FIREBASE ? { googleServicesFile: GOOGLE_SERVICES } : {}),
  },
  ios: { bundleIdentifier: 'io.stann.app' },
  plugins: [
    'expo-router',
    // The badge on paper while the first screen waits for the sports (catalog.tsx).
    ['expo-splash-screen', { image: `${ICONS}/mark-192.png`, imageWidth: 96, backgroundColor: PAPER }],
    // The status bar's mark for a notification is the badge in white (badge-96), tinted stann's blue.
    ['expo-notifications', { icon: `${ICONS}/badge-96.png`, color: BLUE }],
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
