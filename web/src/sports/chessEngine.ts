// The engine of a fan's analysis board (Play from here): Stockfish 18's lite single-threaded WebAssembly build
// (engine/, its README says where it's from and its licence), in a Web Worker of the page, speaking UCI over
// postMessage: the conversation is facts/chessEngineCore.ts's, the Android app's too; this is its way there in a
// browser. Started only when a fan opens an analysis board, one per board, and ended when they leave it. Nothing
// it's told or says leaves the device.

import { talkToEngine, type Engine } from './facts/chessEngineCore'
// The build names both by their content (/assets/…-<hash>), so a new engine is new names and a fan's cached copy
// (the service worker keeps /assets/ once fetched) is never a stale one.
import script from './engine/stockfish-18-lite-single.js?url'
import wasm from './engine/stockfish-18-lite-single.wasm?url'

export { ENGINE_LICENCE, ENGINE_SOURCE, type Engine } from './facts/chessEngineCore'

// The worker script, told where its .wasm is: the build reads the address before a comma in its own address's
// hash (without one, it would look beside itself under its own name, which the build's hash changes). Nothing
// after the comma: ",worker" there would make it one of a threaded build's helpers, which starts no engine.
const ENGINE = `${script}#${encodeURIComponent(wasm)}`

/** An engine in a worker of the page, looking for its best `lines` (talkToEngine). `loading`: how much of it has
 * come, 0 to 1, while it downloads; `ready`: it answers; `failed`: it couldn't start (a browser without
 * WebAssembly, or a page that doesn't allow it). */
export function startEngine(on: { loading: (share: number) => void; ready: () => void; failed: () => void }, lines = 1): Engine {
  const worker = new Worker(ENGINE)
  // The build reports its download on a port of ours, if it's given one.
  const progress = new MessageChannel()
  const talk = talkToEngine({
    tell: (line) => worker.postMessage(line),
    end: () => {
      worker.terminate()
      progress.port1.close()
    },
  }, on, lines)
  progress.port1.onmessage = (e: MessageEvent<{ percent?: number }>) => {
    if (typeof e.data?.percent !== 'number') return
    talk.stirred()
    on.loading(e.data.percent)
  }
  worker.postMessage({ progressPort: progress.port2 }, [progress.port2])
  worker.onmessage = (e: MessageEvent<unknown>) => talk.heard(String(e.data))
  worker.onerror = (e) => {
    e.preventDefault()
    talk.failed()
  }
  talk.start()
  return talk.engine
}
