// The engine of a fan's analysis board (Play from here): Stockfish 18's lite single-threaded WebAssembly build
// (public/engine/, its README says where it's from and its licence), in a Web Worker of the page, speaking UCI
// over postMessage. Started only when a fan opens an analysis board, one per board, and ended when they leave
// it. Nothing it's told or says leaves the device.

import { engineLook, type EngineLook } from './facts/chessLine'

// The worker script; it fetches its .wasm from beside it, by its own name.
const ENGINE = '/engine/stockfish-18-lite-single.js'
// How long it looks at a position: deep enough for a fan's question, short enough for a phone's battery. Its
// look at each depth reaches the bar as it comes.
const LOOK = 'go depth 18 movetime 6000'
// A build that can't start says nothing (a page that doesn't allow WebAssembly rejects a promise inside the
// worker, which never reaches its error event): this long without a word from it while it starts, it has
// failed. Its download's progress is a word, so a slow network isn't a failure.
const QUIET = 15_000

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
  const fail = () => {
    if (quit) return
    quit = true
    window.clearTimeout(quiet)
    worker.terminate()
    on.failed()
  }
  let quiet = window.setTimeout(fail, QUIET)
  const heard = () => {
    window.clearTimeout(quiet)
    if (!ready) quiet = window.setTimeout(fail, QUIET)
  }
  // The build reports its download on a port of ours, if it's given one.
  const progress = new MessageChannel()
  progress.port1.onmessage = (e: MessageEvent<{ percent?: number }>) => {
    if (typeof e.data?.percent !== 'number') return
    heard()
    on.loading(e.data.percent)
  }
  worker.postMessage({ progressPort: progress.port2 }, [progress.port2])
  // One search at a time: a new position waits for the last search's bestmove (after a stop), so every info
  // line between is of the position being searched.
  let searching: { fen: string; seen: (look: EngineLook) => void } | null = null
  let next: typeof searching = null
  const go = () => {
    if (!ready || searching || !next) return
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
    } else if (line.startsWith('bestmove')) {
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
      if (searching) worker.postMessage('stop')
      else go()
    },
    quit() {
      quit = true
      window.clearTimeout(quiet)
      worker.terminate()
      progress.port1.close()
    },
  }
}
