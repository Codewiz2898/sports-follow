# The chess engine of a match page's Play from here

A fan's analysis board (web/src/sports/chessAnalysis.tsx) runs this engine in their browser, in a Web Worker
(web/src/sports/chessEngine.ts): their lines never leave the device.

- **What:** Stockfish.js 18, the lite single-threaded WebAssembly build, from the npm package `stockfish@18.0.8`
  (https://www.npmjs.com/package/stockfish), its `bin/stockfish-18-lite-single.js` and `.wasm` as published. The
  tarball (https://registry.npmjs.org/stockfish/-/stockfish-18.0.8.tgz) matched the registry's integrity,
  `sha512-z+f2UMPXLylDBGjv9e9zU8QulY7hUl8MYHesLRrdddewlOXjJrUSmtNmbtID1/F72EPhq0CCkCNxgWS5MQVWtQ==`.
- **Who:** Stockfish.js by Nathan Rugg, (c) 2026 Chess.com, LLC; based on Stockfish by T. Romstad, M. Costalba,
  J. Kiiski, G. Linscott and other contributors (https://github.com/official-stockfish/Stockfish).
- **Source:** https://github.com/nmrugg/stockfish.js
- **Licence:** GPL-3.0, its text in `Copying.txt` here (the package's own copy).

SHA-256 of the files as vendored:

```
5243fd9b276cab7dfe3ad1d43ab9ead73568fac76468c614242977a210c4a391  stockfish-18-lite-single.js
a8fbc05ec6920b56d7485826dcb02c5ffd2826bcbf751cf973046f237a9096f1  stockfish-18-lite-single.wasm
```

The worker script finds its `.wasm` beside it by its own name. The service worker (web/public/sw.js) keeps
both once a fan has used them, never before, and keeps them by name: a new engine comes under new names (and
a new `ENGINE` in chessEngine.ts), never in place of these. WebAssembly needs `'wasm-unsafe-eval'` in the
page's Content-Security-Policy (deploy/caddy/Caddyfile).
