// The engine of a chess board's Play from here, in a worker of this page, started as the website starts it
// (web/src/sports/chessEngine.ts): told where its .wasm is in its own address's hash, and nothing after a comma
// (",worker" would make it a threaded build's helper, which starts no engine). Its files are the website's, put
// beside this one at each build (android/build.gradle). What the app says over `app` (the WebView's message
// channel, ChessEngineView.kt) reaches the engine, and what the engine says, the app; "!up" and "!failed" are the
// page's own words, which no engine line starts with.
const engine = new Worker('stockfish-18-lite-single.js#' + encodeURIComponent('stockfish-18-lite-single.wasm'))
engine.onmessage = (e) => app.postMessage(String(e.data))
engine.onerror = (e) => {
  e.preventDefault()
  app.postMessage('!failed ' + (e.message || 'the engine stopped'))
}
app.onmessage = (e) => engine.postMessage(e.data)
app.postMessage('!up')
