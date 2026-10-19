# Google Play: what the Console asks for

Everything the first upload of the stann Android app (`io.stann.app`) needs, ready to paste or run, so the
closed-testing release can go up the day the developer account is verified. Linear: BHA-8 (the epic), with
BHA-25 (upload key), BHA-26 (the account), BHA-39 (the closed test) and BHA-168 (data safety) under it.

| File | What |
|---|---|
| `listing.md` | The store listing: name, short and full description, category, contact, and the App content declarations (content rating, target audience, account deletion, ads, …) |
| `graphics.md` | Play's sizes, what's made and how, and the six screenshots to take on the emulator |
| `make_graphics.py` | Draws `icon-512.png` and `feature-1024x500.png` from the app's own badge and fonts; fits screenshots to Play's rules |
| `data-safety.md` | The data safety form, row by row, from what the code collects |
| `release-build.md` | The upload key, where it lives, and the signed `.aab` build |
| `closed-testing.md` | The 12 testers for 14 days rule, the timeline it forces, where the testers come from, the message to send |

The privacy policy is the site's, `https://stann.in/privacy` (`web/src/pages/PrivacyPage.tsx`, with its twin
`mobile/src/app/privacy.tsx`); it names the Android app's push token and Firebase since the PR that added this
folder.

## In the Console, in order

Each step is Bharath's: the Console is his account.

1. **Create the app**: done 19 Oct 2026 as `stann`, app, free (which Play never lets change), default language
   English.
2. **Set up your app** (the Dashboard's checklist): privacy policy, app access, ads, content rating, target
   audience, news app, data safety (`data-safety.md`), government app, financial features, health, account
   deletion. `listing.md` has every answer.
3. **Main store listing**: the text from `listing.md`, `icon-512.png`, `feature-1024x500.png`, the
   screenshots (`graphics.md`).
4. **Upload key**, once (`release-build.md`), then the build, then **Testing → Closed testing**: a track
   ("stann testers"), the `.aab`, the testers' Google Group (`closed-testing.md`), roll out. Google reviews
   closed-testing releases too; a day or three.
5. **Setup → App integrity**: copy the app signing key's SHA-256 into the server's
   `SPORTS_FOLLOW_ANDROID_CERT_SHA256` with the upload key's (App Links).
6. Send the testers' message; watch the opted-in count reach 12; 14 days later, **Apply for production**.

## Keeping it true

The listing promises only what the app does. A sport turned off in production, an analytics SDK, Google
sign-in in the app, or a new permission changes `listing.md`, `data-safety.md` and the privacy page together.
