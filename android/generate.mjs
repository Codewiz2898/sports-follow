// Generate the Android app: a Trusted Web Activity (TWA) that opens the Sports Follow web app full
// screen, from twa-manifest.json, with Google's Bubblewrap library (the same one its CLI uses).
//
//   npm install && node generate.mjs                      # icons from https://<host>
//   SITE=http://localhost:8421 node generate.mjs          # icons from a local build
//
// Then build with Gradle (JDK 17, Android SDK):
//   ./gradlew assembleDebug -PlaunchUrl=http://localhost:8421/   # a test build for `adb reverse`
//   ./gradlew bundleRelease                                        # the Play Store bundle
//
// The generated project is not committed: this file and twa-manifest.json are the source.

import fs from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { ConsoleLog, TwaGenerator, TwaManifest } from '@bubblewrap/core'

const here = path.dirname(fileURLToPath(import.meta.url))
const config = JSON.parse(await fs.readFile(path.join(here, 'twa-manifest.json'), 'utf8'))
const site = (process.env.SITE || `https://${config.host}`).replace(/\/$/, '')

// Icon and manifest paths in twa-manifest.json are relative to the site, so one config serves a
// local build and the hosted one.
for (const key of ['iconUrl', 'maskableIconUrl', 'monochromeIconUrl', 'webManifestUrl']) {
  if (config[key]?.startsWith('/')) config[key] = site + config[key]
}
for (const shortcut of config.shortcuts ?? []) {
  if (shortcut.chosenIconUrl?.startsWith('/')) shortcut.chosenIconUrl = site + shortcut.chosenIconUrl
}

const manifest = new TwaManifest(config)
const problem = manifest.validate()
if (problem) throw new Error(`twa-manifest.json: ${problem}`)
await new TwaGenerator().createTwaProject(here, manifest, new ConsoleLog('generate'))

// The template always opens https://<host><startUrl>. A -PlaunchUrl=... Gradle property overrides
// it, so a test build can open the local server that `adb reverse` forwards to the phone.
const gradle = path.join(here, 'app', 'build.gradle')
const source = await fs.readFile(gradle, 'utf8')
const original = 'def launchUrl = "https://" + twaManifest.hostName + twaManifest.launchUrl'
if (!source.includes(original)) throw new Error('app/build.gradle changed shape; update the launchUrl override in generate.mjs')
await fs.writeFile(gradle, source.replace(original, `def launchUrl = project.findProperty('launchUrl') ?: ("https://" + twaManifest.hostName + twaManifest.launchUrl)`))

// Gradle finds the SDK from local.properties (not committed; it's this machine's path).
const sdk = process.env.ANDROID_HOME || process.env.ANDROID_SDK_ROOT || '/opt/homebrew/share/android-commandlinetools'
await fs.writeFile(path.join(here, 'local.properties'), `sdk.dir=${sdk}\n`)
console.log(`Generated the Android project for ${config.packageId} (${site})`)
