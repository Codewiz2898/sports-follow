# The chess engine of a match page's Play from here

A fan's analysis board (web/src/sports/chessAnalysis.tsx) runs this engine in their browser, in a Web Worker
(web/src/sports/chessEngine.ts): their lines never leave the device. It's chess's, as the rest of
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
(deploy/caddy/Caddyfile). Replacing the engine: new files here, their checksums above, and the source and
licence links in chessAnalysis.tsx.
