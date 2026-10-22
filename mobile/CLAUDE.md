# The Android app (mobile/)

Expo (SDK 57) and React Native, in TypeScript. The README's "Android app" says how to build and run it.

- **Expo changes every SDK.** Before touching an Expo or React Native API, read the docs for this SDK
  (`https://docs.expo.dev/versions/v57.0.0/`, or `https://docs.expo.dev/llms.txt` for the index) rather
  than memory. Add packages with `npx expo install <package>`, which picks versions that match the SDK.
- **`mobile/android` is generated** (`npx expo prebuild`, which `npm run android` runs only while it's missing)
  and not committed. Native settings go in `app.config.ts` or a config plugin, never into `android/` by hand;
  after changing them (or adding a route, which App Links read), `npx expo prebuild --platform android` first;
  with `--clean` after the package id or the Firebase file changes, which a plain prebuild only adds to.
- **Routes are files** in `src/app/` (Expo Router); everything else lives beside them in `src/`.
- **Google Play lives in `store/`**: the listing, data safety answers, graphics (`make_graphics.py`) and the
  signed-build recipe. `plugins/with-upload-key.js` signs a release build with the upload key when the
  environment names it; `versionCode` is `app.config.ts`'s, raised for every upload.
- **It looks like the site.** Each page and block here is the twin of one in `web/src` (its comment names
  it); a change to the site's look is made in both. Values come from `web/src/styles.css` (`theme.ts`).
  Android draws a skewed Text upright, so the site's leaning display type (`.dsp`) is a font here
  (`font.slanted`, made by `scripts/slant_font.py`): use `dsp()` or `Dsp`, never a skew transform on text.
- **Share, don't copy.** What the pages decide comes from the web app: `web/src/endpoints.ts`, `types.ts`,
  `today.ts`, `lines.ts`, `search.ts`, `alerts.ts`, `format.ts`, `follow.ts`, `share.ts`, `campaign.ts`,
  `match.ts`, `feed.ts`, `legal.ts`, `refetch.ts`, `visitTags.ts`, `fanChange.ts`, `resync.ts`, `sports/naming.ts` and `sports/facts/` (Metro watches `web/src`). Keep those files free of the
  browser, and add to them rather than writing a second copy here; only the drawing lives here.
- **Sports and sources come from the API**, as in the web app (the root CLAUDE.md):
  `tests/test_web_names.py` fails if `mobile/src` names one.
- Check with `npm run typecheck` and on the emulator. CI runs the type-check too, but without the route
  types Expo generates when it starts (`.expo/types`), so only a local check catches a misspelt route.
