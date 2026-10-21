// The engine of a fan's analysis board (Play from here): Stockfish 18's lite single-threaded WebAssembly build
// (engine/, its README says where it's from and its licence), in a Web Worker of the page, speaking UCI over
// postMessage. Started only when a fan opens an analysis board, one per board, and ended when they leave it.
// Nothing it's told or says leaves the device.

import { engineLook, type EngineLook } from './facts/chessLine'
// The build names both by their content (/assets/…-<hash>), so a new engine is new names and a fan's cached copy
// (the service worker keeps /assets/ once fetched) is never a stale one.
import script from './engine/stockfish-18-lite-single.js?url'
import wasm from './engine/stockfish-18-lite-single.wasm?url'

// The worker script, told where its .wasm is: the build reads the address before a comma in its own address's
// hash (without one, it would look beside itself under its own name, which the build's hash changes). Nothing
// after the comma: ",worker" there would make it one of a threaded build's helpers, which starts no engine.
const ENGINE = `${script}#${encodeURIComponent(wasm)}`
// Its Corresponding Source, the commit that published this build (18.0.8: the repository has no version tags),
// and the GPL as that commit has it, for the board's credit line.
const BUILT_FROM = '93c994592dcf3b4b21052ab925e9b534df9c0918'
export const ENGINE_SOURCE = `https://github.com/nmrugg/stockfish.js/tree/${BUILT_FROM}`
export const ENGINE_LICENCE = `https://github.com/nmrugg/stockfish.js/blob/${BUILT_FROM}/Copying.txt`
// How deep it looks at a position: to depth 18 and no other limit, so every device ends on the same result (one
// thread, the hash cleared before each position: ucinewgame), a slow phone later rather than shallower. Its look
// at each depth reaches the bar as it comes, so the fan sees one at once. This is the lite build's smaller
// network, not the server's full engine (sports_follow/engine.py): the two can differ on the same position.
const LOOK = 'go depth 18'
// A build that can't start says nothing (a page that doesn't allow WebAssembly rejects a promise inside the
// worker, which never reaches its error event): this long without a word from it while it starts, it has
// failed. Its download's progress is a word, so a slow network isn't a failure.
const QUIET = 15_000
// Once it answers, it answers a stop or an isready at once (it acts on a stop mid-search): this long without the
// bestmove or readyok it owes, it has wedged, and the board says so rather than look forever. A search left to
// run isn't timed: a slow phone may take seconds to reach its depth.
const OWED = 5_000

export interface Engine {
  /** Look at a position (a FEN), each look at it, deeper as it goes, to `seen`; a new position stops the last. */
  look: (fen: string, seen: (look: EngineLook) => void) => void
  /** End it: the worker goes, mid-search or not. */
  quit: () => void
}

/** An engine in a worker of the page. `loading`: how much of it has come, 0 to 1, while it downloads; `ready`:
 * it answers; `failed`: it couldn't start (a browser without WebAssembly, or a page that doesn't allow it). */
export function startEngine(on: { loading: (share: number) => void; ready: () => void; failed: () => void }): Engine {
  const worker = new Worker(ENGINE)
  let ready = false
  let quit = false
  let owed = 0
  const fail = () => {
    if (quit) return
    quit = true
    window.clearTimeout(quiet)
    window.clearTimeout(owed)
    worker.terminate()
    on.failed()
  }
  let quiet = window.setTimeout(fail, QUIET)
  const heard = () => {
    window.clearTimeout(quiet)
    if (!ready) quiet = window.setTimeout(fail, QUIET)
  }
  const owe = () => {
    window.clearTimeout(owed)
    owed = window.setTimeout(fail, OWED)
  }
  // The build reports its download on a port of ours, if it's given one.
  const progress = new MessageChannel()
  progress.port1.onmessage = (e: MessageEvent<{ percent?: number }>) => {
    if (typeof e.data?.percent !== 'number') return
    heard()
    on.loading(e.data.percent)
  }
  worker.postMessage({ progressPort: progress.port2 }, [progress.port2])
  // One search at a time: a new position waits for the last search's bestmove (after a stop), then for the
  // engine to have cleared what it learnt (ucinewgame, then readyok), so every info line is of the position being
  // searched, and no earlier line changes its result.
  let searching: { fen: string; seen: (look: EngineLook) => void } | null = null
  let clearing = false
  let next: typeof searching = null
  const go = () => {
    if (!ready || searching || clearing || !next) return
    clearing = true
    worker.postMessage('ucinewgame')
    worker.postMessage('isready')
    owe()
  }
  const search = () => {
    clearing = false
    window.clearTimeout(owed)
    if (!next) return
    searching = next
    next = null
    worker.postMessage(`position fen ${searching.fen}`)
    worker.postMessage(LOOK)
  }
  worker.onmessage = (e: MessageEvent<unknown>) => {
    const line = String(e.data)
    heard()
    if (line === 'uciok') worker.postMessage('isready')
    else if (line === 'readyok' && !ready) {
      ready = true
      window.clearTimeout(quiet)
      on.ready()
      go()
    } else if (line === 'readyok' && clearing) search()  // the newest position asked for, if several came meanwhile
    else if (line.startsWith('bestmove')) {
      window.clearTimeout(owed)
      searching = null
      go()
    } else if (searching) {
      const look = engineLook(line)
      if (look) searching.seen(look)
    }
  }
  worker.onerror = (e) => {
    e.preventDefault()
    fail()
  }
  worker.postMessage('uci')
  return {
    look(fen, seen) {
      next = { fen, seen }
      if (searching) {
        worker.postMessage('stop')
        owe()
      } else go()  // while it clears, the readyok searches this one
    },
    quit() {
      quit = true
      window.clearTimeout(quiet)
      window.clearTimeout(owed)
      worker.terminate()
      progress.port1.close()
    },
  }
}
