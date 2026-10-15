# The Android app (mobile/)

Expo (SDK 57) and React Native, in TypeScript. The README's "Android app" says how to build and run it.

- **Expo changes every SDK.** Before touching an Expo or React Native API, read the docs for this SDK
  (`https://docs.expo.dev/versions/v57.0.0/`, or `https://docs.expo.dev/llms.txt` for the index) rather
  than memory. Add packages with `npx expo install <package>`, which picks versions that match the SDK.
- **`mobile/android` is generated** (`npx expo prebuild`, run by `npm run android`) and not committed.
  Native settings go in `app.config.ts` or a config plugin, never into `android/` by hand.
- **Routes are files** in `src/app/` (Expo Router); everything else lives beside them in `src/`.
- **Share, don't copy.** The API's types and the pure helpers come from the web app (`web/src/types.ts`,
  `format.ts`, `follow.ts`, `sports/naming.ts`; Metro watches `web/src`). Keep those files free of the
  browser, and add to them rather than writing a second copy here.
- **Sports and sources come from the API**, as in the web app (the root CLAUDE.md):
  `tests/test_web_names.py` fails if `mobile/src` names one.
- Check with `npm run typecheck` (CI runs it) and on the emulator.
