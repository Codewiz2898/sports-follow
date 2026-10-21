// The engine of a fan's analysis board (Play from here), the web's chessEngine.ts: Stockfish 18's lite
// single-threaded WebAssembly build, the website's own files (web/src/sports/engine/, its README says where they're
// from and their licence), which the app carries inside it (plugins/with-chess-engine.js) rather than downloads.
// It runs in a Web Worker of a page of its own in a hidden WebView, speaking UCI over postMessage: started only
// when a fan opens an analysis board, one per board, and ended when they leave it, the WebView going with the
// board. The page reads nothing but the app's own files and may ask no network: nothing the engine is told or
// says leaves the phone.
//
// It hands on the engine's info lines as they come, for the board to read with the web's (facts/chessLine.ts:
// engineLook, then lookFromWhite for the bar).

import { useEffect, useRef } from 'react'
import { StyleSheet } from 'react-native'
import { WebView } from 'react-native-webview'

// Its Corresponding Source, the commit that published this build (18.0.8: the repository has no version tags),
// and the GPL as that commit has it: the board credits them under it, as the website's does ("Engine: Stockfish
// 18 (GPL-3.0) · source"). The same as the web's chessEngine.ts, to be shared once both are in.
const BUILT_FROM = '93c994592dcf3b4b21052ab925e9b534df9c0918'
export const ENGINE_SOURCE = `https://github.com/nmrugg/stockfish.js/tree/${BUILT_FROM}`
export const ENGINE_LICENCE = `https://github.com/nmrugg/stockfish.js/blob/${BUILT_FROM}/Copying.txt`

// How deep it looks at a position, as the website does: to depth 18 and no other limit, one thread, the hash
// cleared before each position (ucinewgame), so a phone ends on the same result as every other device, a slow one
// later rather than shallower. Its look at each depth comes as it's done, so the bar moves at once.
const LOOK = 'go depth 18'
// A page that can't start the engine may say nothing (a worker that fails inside a promise never reaches its error
// event): this long without a word from it while it starts, it has failed.
const QUIET = 15_000
// Once it answers, it answers a stop or an isready at once (it acts on a stop mid-search): this long without the
// bestmove or readyok it owes, it has wedged, and the board says so rather than look forever. A search left to
// run isn't timed: a slow phone may take seconds to reach its depth.
const OWED = 5_000

// Where the app's assets are, the page's own address, so it reads the engine's files beside it.
const ASSETS = 'file:///android_asset/'
const SCRIPT = 'stockfish-18-lite-single.js'
const WASM = 'stockfish-18-lite-single.wasm'
// The page's own words, which no engine line starts with: it's listening, and it couldn't start the engine.
const UP = '!up'
const FAILED = '!failed'

// The engine's page. It reads both files as blobs and starts the worker from those, told where its .wasm is in
// its own address's hash, as on the website (and nothing after a comma: ",worker" would make it a threaded
// build's helper, which starts no engine). Blobs, not the files' own addresses, as a WebView's fetch can't read
// file: and the worker fetches its .wasm; and the .wasm's typed application/wasm, which WebAssembly's streaming
// compile asks for. What the app says reaches the worker; what the worker says, the app. Its policy lets it read
// the app's files and run them, and nothing else: no network, no other page.
const PAGE = `<!doctype html>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline' 'wasm-unsafe-eval' blob:; worker-src blob:; connect-src file: blob:">
<script>
  const say = (line) => window.ReactNativeWebView.postMessage(line)
  let engine = null
  const waiting = []
  document.addEventListener('message', (e) => (engine ? engine.postMessage(e.data) : waiting.push(e.data)))
  const read = (name, type) => new Promise((done, failed) => {
    const r = new XMLHttpRequest()
    r.open('GET', name)
    r.responseType = 'blob'
    r.onload = () => (r.response && r.response.size ? done(new Blob([r.response], { type })) : failed(new Error(name + ' is empty')))
    r.onerror = () => failed(new Error(name + " isn't in the app"))
    r.send()
  })
  Promise.all([read(${JSON.stringify(SCRIPT)}, 'text/javascript'), read(${JSON.stringify(WASM)}, 'application/wasm')]).then(([script, wasm]) => {
    const urls = [URL.createObjectURL(script), URL.createObjectURL(wasm)]
    engine = new Worker(urls[0] + '#' + encodeURIComponent(urls[1]))
    engine.onmessage = (e) => {
      for (const url of urls.splice(0)) URL.revokeObjectURL(url) // its first word comes once it has read both
      say(String(e.data))
    }
    engine.onerror = (e) => { e.preventDefault(); say(${JSON.stringify(FAILED)} + ' ' + (e.message || 'the engine stopped')) }
    for (const line of waiting.splice(0)) engine.postMessage(line)
  }).catch((e) => say(${JSON.stringify(FAILED)} + ' ' + e.message))
  say(${JSON.stringify(UP)})
</script>`

export interface Engine {
  /** Look at a position (a FEN), each of the engine's info lines about it, deeper as it goes, to `seen`; a new
   * position stops the last, whose lines stop. */
  look: (fen: string, seen: (line: string) => void) => void
  /** Stop looking (a position that has ended): the search stops, and its lines with it. */
  stop: () => void
  /** End it, mid-search or not; its page goes with its EngineView. */
  quit: () => void
}

/** What an engine's EngineView connects: how to tell its page a line, and what to do with each word of it. */
interface Wires { tell: ((line: string) => void) | null; heard: (word: string) => void; failed: () => void }
const WIRES = new WeakMap<Engine, Wires>()

const unheard = () => {}

/** A position to look at, and who hears the engine's lines about it. */
interface Search { fen: string; seen: (line: string) => void }

/** An engine for a board, its page in an EngineView the board draws. `ready`: it answers; `failed`: it couldn't
 * start, it stopped answering, or its page went (the phone short of memory): the board says so and lets its
 * EngineView go, and its moves still play. */
export function startEngine(on: { ready: () => void; failed: () => void }): Engine {
  let ready = false
  let over = false
  let owed: ReturnType<typeof setTimeout> | undefined
  const wires: Wires = { tell: null, heard: () => {}, failed: () => {} }
  const tell = (line: string) => wires.tell?.(line)
  const fail = () => {
    if (over) return
    over = true
    clearTimeout(quiet)
    clearTimeout(owed)
    wires.tell = null
    on.failed()
  }
  let quiet = setTimeout(fail, QUIET)
  const owe = () => {
    clearTimeout(owed)
    owed = setTimeout(fail, OWED)
  }
  // One search at a time, as on the website: a new position waits for the last search's bestmove (after a stop),
  // then for the engine to have cleared what it learnt (ucinewgame, then readyok), so every line is of the
  // position being searched, and no earlier one changes its result.
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
    searching.seen = unheard
    tell('stop')
    owe()
  }
  wires.failed = fail
  wires.heard = (word) => {
    if (over) return
    if (word.startsWith(FAILED)) return fail()
    if (!ready) {
      clearTimeout(quiet)
      quiet = setTimeout(fail, QUIET)
    }
    if (word === UP) tell('uci')
    else if (word === 'uciok') tell('isready')
    else if (word === 'readyok' && !ready) {
      ready = true
      clearTimeout(quiet)
      on.ready()
      go()
    } else if (word === 'readyok' && clearing) search() // the newest position asked for, if several came meanwhile
    else if (word.startsWith('bestmove')) {
      clearTimeout(owed)
      searching = null
      go()
    } else if (searching && word.startsWith('info ')) searching.seen(word)
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
    quit() {
      over = true
      clearTimeout(quiet)
      clearTimeout(owed)
      wires.tell = null
    },
  }
  WIRES.set(engine, wires)
  return engine
}

/** An engine's page, unseen: drawn anywhere in the board while it's open, and gone (its worker with it) when the
 * board goes. */
export function EngineView({ engine }: { engine: Engine }) {
  const page = useRef<WebView>(null)
  const wires = WIRES.get(engine)
  useEffect(() => {
    if (!wires) return
    wires.tell = (line) => page.current?.postMessage(line)
    return () => { wires.tell = null }
  }, [wires])
  if (!wires) return null
  return (
    <WebView
      ref={page}
      source={{ html: PAGE, baseUrl: ASSETS }}
      style={s.unseen}
      containerStyle={s.unseen}
      pointerEvents="none"
      importantForAccessibility="no-hide-descendants"
      accessibilityElementsHidden
      onMessage={(e) => wires.heard(e.nativeEvent.data)}
      onError={wires.failed}
      onRenderProcessGone={wires.failed}
      // The page never goes anywhere: every navigation reaches onShouldStartLoadWithRequest, which refuses it (an
      // address outside a narrower list would go to the phone's browser instead).
      originWhitelist={['*']}
      onShouldStartLoadWithRequest={() => false}
      setSupportMultipleWindows={false}
      javaScriptCanOpenWindowsAutomatically={false}
      // It reads the engine's files beside it, in the app's assets (which allowFileAccess, off, doesn't cover).
      allowFileAccessFromFileURLs
      allowFileAccess={false}
      allowUniversalAccessFromFileURLs={false}
      domStorageEnabled={false}
      cacheEnabled={false}
    />
  )
}

// One pixel, not none: a WebView that isn't drawn may be held back as a page out of sight.
const s = StyleSheet.create({ unseen: { position: 'absolute', width: 1, height: 1, opacity: 0, flex: 0 } })
