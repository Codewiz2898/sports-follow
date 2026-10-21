// The Play upload key (store/release-build.md). Expo's template signs release builds with the debug key, and
// android/ is generated (CLAUDE.md), so the change is made here at each prebuild: when the build has the key,
// SPORTS_FOLLOW_UPLOAD_KEYSTORE naming the keystore file (never in the repo:
// ~/.config/sports-follow/upload-key.jks), SPORTS_FOLLOW_UPLOAD_KEYSTORE_PASSWORD its password and
// SPORTS_FOLLOW_UPLOAD_KEY_ALIAS its key ("upload" unless set, with the keystore's password unless
// SPORTS_FOLLOW_UPLOAD_KEY_PASSWORD gives another), a release build is signed with it.
// Without it a release build stays on the debug key, as before, so `npm run android` and CI need nothing.
const { withAppBuildGradle } = require('expo/config-plugins')

const RELEASE_SIGNING = `        release {
            // The Play upload key, when the build has it (plugins/with-upload-key.js, store/release-build.md).
            def uploadKeystore = System.getenv("SPORTS_FOLLOW_UPLOAD_KEYSTORE")
            if (uploadKeystore) {
                storeFile file(uploadKeystore)
                storePassword System.getenv("SPORTS_FOLLOW_UPLOAD_KEYSTORE_PASSWORD")
                keyAlias System.getenv("SPORTS_FOLLOW_UPLOAD_KEY_ALIAS") ?: "upload"
                keyPassword System.getenv("SPORTS_FOLLOW_UPLOAD_KEY_PASSWORD") ?: System.getenv("SPORTS_FOLLOW_UPLOAD_KEYSTORE_PASSWORD")
            }
        }
`
// The template's debug signing config, whole: the release one goes after it, inside signingConfigs.
const DEBUG_SIGNING = /signingConfigs \{\n(\s+)debug \{[\s\S]*?\n\1\}\n/
// The release build type's line that puts the debug key on it (its comments kept).
const RELEASE_ON_DEBUG_KEY = /(release \{\n(?:\s*\/\/.*\n)*\s+)signingConfig signingConfigs\.debug\n/

module.exports = (config) =>
  withAppBuildGradle(config, (mod) => {
    let gradle = mod.modResults.contents
    if (gradle.includes('STANN_UPLOAD_KEYSTORE')) throw new Error('with-upload-key: android/ has the upload key under its old variables; prebuild with --clean')
    if (!gradle.includes('SPORTS_FOLLOW_UPLOAD_KEYSTORE')) {
      if (!DEBUG_SIGNING.test(gradle)) throw new Error('with-upload-key: no signingConfigs.debug in android/app/build.gradle; Expo\'s template changed')
      if (!RELEASE_ON_DEBUG_KEY.test(gradle)) throw new Error('with-upload-key: the release build type is not on signingConfigs.debug; Expo\'s template changed')
      gradle = gradle.replace(DEBUG_SIGNING, (debug) => debug + RELEASE_SIGNING)
      gradle = gradle.replace(RELEASE_ON_DEBUG_KEY, '$1signingConfig System.getenv("SPORTS_FOLLOW_UPLOAD_KEYSTORE") ? signingConfigs.release : signingConfigs.debug\n')
      mod.modResults.contents = gradle
    }
    return mod
  })
