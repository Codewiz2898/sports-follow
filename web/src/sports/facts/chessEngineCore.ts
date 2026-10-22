// The conversation with the engine of a fan's analysis board (Play from here), Stockfish 18's lite single-threaded
// build (web/src/sports/engine/, its README says where it's from and its licence), whichever way its lines travel:
// the browser's Web Worker (chessEngine.ts) or the Android app's WebView (mobile/src/sports/chessEngine.tsx). Pure,
// nothing of a browser's: a transport tells the engine a line and hands back each line the engine says.

import { engineLook, type EngineLook } from './chessLine'

// Its Corresponding Source, the commit that published this build (18.0.8: the repository has no version tags),
// and the GPL as that commit has it, for the board's credit line and the Credits page.
const BUILT_FROM = '93c994592dcf3b4b21052ab925e9b534df9c0918'
export const ENGINE_SOURCE = `https://github.com/nmrugg/stockfish.js/tree/${BUILT_FROM}`
export const ENGINE_LICENCE = `https://github.com/nmrugg/stockfish.js/blob/${BUILT_FROM}/Copying.txt`
// How deep it looks at a position: to depth 18 and no other limit, so every device ends on the same result
// (one thread, the hash cleared before each position: ucinewgame, and the same number of lines: talkToEngine's
// `lines`, which changes the search too), a slow phone later rather than shallower: the web's board and the
// Android app's both look for 3. Its look at each depth reaches the bar as it comes, so the fan sees one at once. This is the lite build's smaller
// network, not the server's full engine (sports_follow/engine.py): the two can differ on the same position.
const LOOK = 'go depth 18'
// A build that can't start says nothing (a page that doesn't allow WebAssembly rejects a promise inside the
// worker, which never reaches its error event): this long without a word from it while it starts, it has
// failed. A sign of life that isn't a line (the web's download progress) counts, so a slow network isn't a failure.
const QUIET = 15_000
// Once it answers, it answers a stop or an isready at once (it acts on a stop mid-search): this long without the
// bestmove or readyok it owes, it has wedged, and the board says so rather than look forever. A search left to
// run isn't timed: a slow phone may take seconds to reach its depth.
const OWED = 5_000

export interface Engine {
  /** Look at a position (a FEN), each look at it, deeper as it goes, to `seen` (with several lines asked for,
   * each line's, by its number); a new position stops the last, whose looks stop coming. */
  look: (fen: string, seen: (look: EngineLook) => void) => void
  /** Stop looking (a position that has ended): the search stops, and its looks with it. */
  stop: () => void
  /** End it, mid-search or not: its transport goes. */
  quit: () => void
}

/** What a transport does with the conversation: start it (the engine's `uci`) once the engine can hear, hand on
 * each line the engine says (`heard`), say it's alive meanwhile (`stirred`), and that it has gone (`failed`). */
export interface Talk {
  engine: Engine
  start: () => void
  heard: (line: string) => void
  stirred: () => void
  failed: () => void
}

const unseen = () => {}

/** A position to look at, and who's told each look at it. */
interface Search { fen: string; seen: (look: EngineLook) => void }

/** The conversation over a transport: `tell` gives the engine a line, `end` closes the transport (on quitting,
 * or failing). `ready`: the engine answers; `failed`: it couldn't start, it stopped answering, or the transport
 * went. `lines`: how many of its best lines it looks for (MultiPV), its best alone by default; more take it
 * longer to reach its depth. */
export function talkToEngine(transport: { tell: (line: string) => void; end: () => void }, on: { ready: () => void; failed: () => void }, lines = 1): Talk {
  let ready = false
  let over = false
  let owed: ReturnType<typeof setTimeout> | undefined
  const tell = (line: string) => { if (!over) transport.tell(line) }
  const end = () => {
    over = true
    clearTimeout(quiet)
    clearTimeout(owed)
    transport.end()
  }
  const fail = () => {
    if (over) return
    end()
    on.failed()
  }
  let quiet = setTimeout(fail, QUIET)
  const stirred = () => {
    clearTimeout(quiet)
    if (!ready && !over) quiet = setTimeout(fail, QUIET)
  }
  const owe = () => {
    clearTimeout(owed)
    owed = setTimeout(fail, OWED)
  }
  // One search at a time: a new position waits for the last search's bestmove (after a stop), then for the
  // engine to have cleared what it learnt (ucinewgame, then readyok), so every info line is of the position being
  // searched, and no earlier line changes its result.
  let searching: Search | null = null
  let clearing = false
  let next: Search | null = null
  const go = () => {
    if (!ready || searching || clearing || !next) return
    clearing = true
    tell('ucinewgame')
    tell('isready')
    owe()
  }
  const search = () => {
    clearing = false
    clearTimeout(owed)
    if (!next) return
    searching = next
    next = null
    tell(`position fen ${searching.fen}`)
    tell(LOOK)
  }
  // The search under way stops; what it says until its bestmove is of a position the fan has left.
  const halt = () => {
    if (!searching) return
    searching.seen = unseen
    tell('stop')
    owe()
  }
  const heard = (line: string) => {
    if (over) return
    stirred()
    if (line === 'uciok') {
      if (lines > 1) tell(`setoption name MultiPV value ${lines}`)
      tell('isready')
    }
    else if (line === 'readyok' && !ready) {
      ready = true
      clearTimeout(quiet)
      on.ready()
      go()
    } else if (line === 'readyok' && clearing) search() // the newest position asked for, if several came meanwhile
    else if (line.startsWith('bestmove')) {
      clearTimeout(owed)
      searching = null
      go()
    } else if (searching) {
      const look = engineLook(line)
      if (look) searching.seen(look)
    }
  }
  const engine: Engine = {
    look(fen, seen) {
      if (over) return
      next = { fen, seen }
      if (searching) halt()
      else go() // while it clears, the readyok searches this one
    },
    stop() {
      next = null
      halt()
    },
    quit: end,
  }
  return { engine, start: () => tell('uci'), heard, stirred, failed: fail }
}
