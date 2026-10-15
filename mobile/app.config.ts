// The Android app's config (Expo). Its name is stann's, from sports_follow/app.json, the one place it's
// written; its icons are the web app's (web/scripts/make_icons.py draws them). The API it talks to is
// src/api.ts's.

/// <reference types="node" />
import fs from 'node:fs'
import path from 'node:path'
import type { ExpoConfig } from 'expo/config'

const { name } = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'sports_follow', 'app.json'), 'utf8')) as { name: string }
if (!name) throw new Error('sports_follow/app.json has no name')

const ICONS = '../web/public/icons'
const PAPER = '#F3F3EF' // web/src/styles.css --bg
const FONTS = 'node_modules/@expo-google-fonts'

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
  },
  ios: { bundleIdentifier: 'io.stann.app' },
  plugins: [
    'expo-router',
    // The web app's typefaces (styles.css): Anton for display, Manrope for text, Fredoka for the name.
    ['expo-font', {
      fonts: [
        `${FONTS}/anton/400Regular/Anton_400Regular.ttf`,
        `${FONTS}/manrope/500Medium/Manrope_500Medium.ttf`,
        `${FONTS}/manrope/700Bold/Manrope_700Bold.ttf`,
        `${FONTS}/manrope/800ExtraBold/Manrope_800ExtraBold.ttf`,
        `${FONTS}/fredoka/600SemiBold/Fredoka_600SemiBold.ttf`,
      ],
    }],
  ],
  experiments: { typedRoutes: true },
})
