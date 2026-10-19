# Release build: a signed `.aab` for Play

The upload is an Android App Bundle (`.aab`) signed with **our upload key**. Play App Signing then signs what
phones install with Google's own app signing key: the upload key only proves the upload is ours, and Google
can reset it if it's lost. Both keys' SHA-256 fingerprints go into the server's App Links setting (below).

Everything here is local Gradle, the same way `npm run android` builds: no EAS account and no Expo servers.
(`eas build` would work too, with `eas credentials` holding the same keystore, but it's one more account and
a queue; not needed for one app.)

## Once: the upload key

The keystore lives with the other local secrets, in `~/.config/sports-follow/`, never in the repo
(`mobile/.gitignore` ignores `*.jks`, and the keystore isn't under `mobile/` anyway).

```bash
export JAVA_HOME=~/.local/jdk-17/Contents/Home
"$JAVA_HOME/bin/keytool" -genkeypair -v -keystore ~/.config/sports-follow/upload-key.jks -alias upload -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=stann, O=stann, C=IN"
```

It asks for a keystore password; use the same for the key (press Enter when it asks for the key's). Then:

```bash
chmod 600 ~/.config/sports-follow/upload-key.jks
printf '%s' 'the password' > ~/.config/sports-follow/upload-key.password && chmod 600 ~/.config/sports-follow/upload-key.password
```

**Back it up off the laptop** with the other sealed secrets (deploy/README.md, "Offsite backup": the B2
bucket holds them), or in the password manager as a file. Losing it isn't fatal (Play can reset an upload key,
with a few days' wait), losing the password with it is the same.

Its fingerprint, for the server's App Links setting (`SPORTS_FOLLOW_ANDROID_CERT_SHA256`, BHA-25):

```bash
"$JAVA_HOME/bin/keytool" -list -v -keystore ~/.config/sports-follow/upload-key.jks -alias upload | grep SHA256
```

## Each release

1. **Raise `versionCode`** in `mobile/app.config.ts` (Play refuses an upload with one it has seen) and, for a
   version fans should see, `version`. One place for each; nothing else to edit.
2. **Build.** From `mobile/`, with the app's Firebase file (notifications) and the upload key in the environment.
   The config plugin `plugins/with-upload-key.js` puts the key on the release build type at prebuild; without
   `STANN_UPLOAD_KEYSTORE` a release build keeps the debug key, as Expo's template leaves it.

   ```bash
   cd mobile && npm install
   export JAVA_HOME=~/.local/jdk-17/Contents/Home ANDROID_HOME=~/.local/android-sdk
   export GOOGLE_SERVICES_JSON=~/.config/sports-follow/google-services.json
   export STANN_UPLOAD_KEYSTORE=~/.config/sports-follow/upload-key.jks
   export STANN_UPLOAD_KEYSTORE_PASSWORD="$(cat ~/.config/sports-follow/upload-key.password)"
   npx expo prebuild --platform android --clean
   (cd android && ./gradlew :app:bundleRelease)
   ```

   The bundle is `mobile/android/app/build/outputs/bundle/release/app-release.aab`. `--clean` regenerates
   `android/` from `app.config.ts`, so the version, the plugin's signing and the App Links routes are the
   config's; `npm run android` afterwards is unaffected (debug builds keep the debug key).

   Variables: `STANN_UPLOAD_KEY_ALIAS` if the key isn't `upload`; `STANN_UPLOAD_KEY_PASSWORD` if the key's
   password differs from the keystore's. The passwords never go on a command line, so they're not in the
   shell history.
3. **Check it** before uploading: it's signed by the upload key, and it's the version you meant.

   ```bash
   "$JAVA_HOME/bin/jarsigner" -verify -verbose:summary android/app/build/outputs/bundle/release/app-release.aab | tail -3
   "$JAVA_HOME/bin/keytool" -printcert -jarfile android/app/build/outputs/bundle/release/app-release.aab | grep SHA256
   ```

   The second fingerprint must equal the keystore's (above). The debug key's would mean the environment
   variables weren't set when prebuild ran.
4. **Upload** in Play Console, **Testing → Closed testing → (track) → Create new release**, drop the
   `.aab`, write a short release note, roll out. The first upload is where Play asks to turn on Play App
   Signing (keep the default: Google makes the app signing key).
5. **After the first upload**, read the app signing key's fingerprint from **Setup → App integrity → App
   signing**, and set the server's `SPORTS_FOLLOW_ANDROID_CERT_SHA256` to both fingerprints, upload key's and
   app signing key's, comma separated (README.md, "Android app"; the TWA's `android/` config is retired with
   BHA-39). Until then stann.in links open in the browser on Play-installed phones.

Tested on 19 Oct 2026 with a throwaway keystore: prebuild writes the signing config, `:app:bundleRelease`
produces `app-release.aab` signed by it, and the merged manifest asks for no advertising id.

## What's not here yet

- **Google sign-in in the app** (BHA-167/168): the Android app signs in by email code only, so there's no
  OAuth client to make for the app signing key's fingerprint yet. When it comes, Firebase (or the Google
  Cloud console) needs both fingerprints, as App Links do.
- **CI builds**: the key stays on Bharath's laptop; a GitHub Actions build would need it as a secret. Not
  worth it for one upload a week.
