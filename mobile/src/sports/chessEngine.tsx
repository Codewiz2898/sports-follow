// The engine of a fan's analysis board (Play from here), the web's chessEngine.ts: the same Stockfish 18 build
// (web/src/sports/engine/, its README says where it's from and its licence) and the same conversation
// (facts/chessEngineCore.ts); this is its way there on Android. The engine runs in a Web Worker of a page of the
// app's own in a hidden WebView (modules/chess-engine), which the app carries with the engine's files rather than
// downloads: started only when a fan opens an analysis board, one per board, and ended when they leave it, the
// WebView going with the board. The page gets nothing but the app's own files, and asks no network: nothing the
// engine is told or says leaves the phone.

import { useEffect, useRef } from 'react'
import { StyleSheet } from 'react-native'
import { talkToEngine, type Engine, type Talk } from '../../../web/src/sports/facts/chessEngineCore'
import EnginePage, { type EnginePageHandle } from '../../modules/chess-engine'

export { ENGINE_LICENCE, ENGINE_SOURCE, type Engine } from '../../../web/src/sports/facts/chessEngineCore'

// The page's own words (modules/chess-engine's page.js), which no engine line starts with: it's listening, and
// it couldn't start the engine; and its WebView's, Android has ended the page's process.
const UP = '!up'
const FAILED = '!failed'
const GONE = '!gone'

/** What an engine's EngineView connects: the conversation, how to tell the page a line, and whether its page went. */
interface Wires { talk: Talk; tell: ((line: string) => void) | null; gone: boolean }
const WIRES = new WeakMap<Engine, Wires>()

/** An engine for a board, its page in an EngineView the board draws. `ready`: it answers; `failed`: it couldn't
 * start, it stopped answering, or its page went (`gone`: Android ended its process, the phone short of memory,
 * often while the app was behind another, where another engine can start): the board says so and lets its
 * EngineView go, and its moves still play. */
export function startEngine(on: { ready: () => void; failed: (gone: boolean) => void }): Engine {
  const wires: Wires = {
    talk: talkToEngine({ tell: (line) => wires.tell?.(line), end: () => { wires.tell = null } }, { ready: on.ready, failed: () => on.failed(wires.gone) }),
    tell: null,
    gone: false,
  }
  WIRES.set(wires.talk.engine, wires)
  // A build without the page can't start it.
  if (!EnginePage) setTimeout(wires.talk.failed)
  return wires.talk.engine
}

/** An engine's page, unseen: drawn anywhere in the board while it's open, and gone (its worker with it) when the
 * board goes. */
export function EngineView({ engine }: { engine: Engine }) {
  const page = useRef<EnginePageHandle>(null)
  const wires = WIRES.get(engine)
  useEffect(() => {
    if (!wires) return
    wires.tell = (line) => { page.current?.tell(line).catch(() => wires.talk.failed()) }
    return () => { wires.tell = null }
  }, [wires])
  if (!wires || !EnginePage) return null
  const heard = (word: string) => {
    if (word === UP) wires.talk.start()
    else if (word === GONE) {
      wires.gone = true
      wires.talk.failed()
    } else if (word.startsWith(FAILED)) wires.talk.failed()
    else wires.talk.heard(word)
  }
  return <EnginePage ref={page} style={s.unseen} onWord={(e) => heard(e.nativeEvent.word)} />
}

// One pixel, not none: a WebView that isn't drawn may be held back as a page out of sight.
const s = StyleSheet.create({ unseen: { position: 'absolute', width: 1, height: 1, opacity: 0 } })
