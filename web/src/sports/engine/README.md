# The chess engine of a match page's Play from here

A fan's analysis board runs this engine on their own device, in a Web Worker: in their browser
(web/src/sports/chessAnalysis.tsx, chessEngine.ts), and in the Android app in a hidden WebView of its own
(mobile/src/sports/chessAnalysis.tsx, chessEngine.tsx, mobile/modules/chess-engine). Both speak to it as
web/src/sports/facts/chessEngineCore.ts does. Their lines never leave the device. It's chess's, as the rest of
web/src/sports/chess*: removing chess removes this folder too.

- **What:** Stockfish.js 18, the lite single-threaded WebAssembly build, from the npm package `stockfish@18.0.8`
  (https://www.npmjs.com/package/stockfish), its `bin/stockfish-18-lite-single.js` and `.wasm` as published. The
  tarball (https://registry.npmjs.org/stockfish/-/stockfish-18.0.8.tgz) matched the registry's integrity,
  `sha512-z+f2UMPXLylDBGjv9e9zU8QulY7hUl8MYHesLRrdddewlOXjJrUSmtNmbtID1/F72EPhq0CCkCNxgWS5MQVWtQ==`.
- **Who:** Stockfish.js by Nathan Rugg, (c) 2026 Chess.com, LLC; based on Stockfish by T. Romstad, M. Costalba,
  J. Kiiski, G. Linscott and other contributors (https://github.com/official-stockfish/Stockfish).
- **Source:** the commit that published 18.0.8 ("18.0.8", 2026-06-15; its package.json says 18.0.8):
  https://github.com/nmrugg/stockfish.js/tree/93c994592dcf3b4b21052ab925e9b534df9c0918. The repository has no
  version tags, and its tip has moved on.
- **Licence:** GPL-3.0, its text in `Copying.txt` here (the package's own copy, the same as the source commit's,
  which the board's "GPL-3.0" links to).

SHA-256 of the files as vendored:

```
5243fd9b276cab7dfe3ad1d43ab9ead73568fac76468c614242977a210c4a391  stockfish-18-lite-single.js
a8fbc05ec6920b56d7485826dcb02c5ffd2826bcbf751cf973046f237a9096f1  stockfish-18-lite-single.wasm
0b383d5a63da644f628d99c33976ea6487ed89aaa59f0b3257992deac1171e6b  Copying.txt
```

chessEngine.ts imports both files' addresses (`?url`), so the build copies them into `/assets/` as they are,
named by their content. Only a fan who opens an analysis board fetches them, and the service worker keeps
them then, as it does every built asset. The worker script is told its `.wasm`'s address in its own address's
hash (`<script>#<wasm>`). WebAssembly needs `'wasm-unsafe-eval'` in the page's Content-Security-Policy
(deploy/caddy/Caddyfile).

The Android app carries these files rather than downloading them: its module's build copies the `.js`, the
`.wasm` and `Copying.txt` from here into its assets at each build (mobile/modules/chess-engine/android/build.gradle),
and its WebView serves them to the engine's page at https://appassets.androidplatform.net/assets/engine/
(WebViewAssetLoader, which types the `.wasm` application/wasm), with no file access and no network. A new
engine reaches phones with a new build of the app, not with its JavaScript alone.

Replacing the engine: new files here and their checksums above; in chessEngine.ts the two `?url` imports (they
name the files); in the Android module, the names in build.gradle's copy and in its page
(android/src/main/assets/engine/page.js); and `BUILT_FROM` in facts/chessEngineCore.ts with `ENGINE_BUILT_FROM`
in sports_follow/sports/chess.py (the Credits page's), the commit the source and licence links point at.
