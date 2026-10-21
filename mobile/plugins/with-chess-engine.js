// The engine of a chess analysis board (Play from here, src/sports/chessEngine.tsx): the very files the website
// serves, web/src/sports/engine/ (Stockfish 18, lite and single-threaded, as published, with its licence), go into
// the app's own assets, read from there at each build, so there's one copy of the 7 MB binary in the repo and the
// app downloads nothing. A WebView reads them at file:///android_asset/ straight out of the installed app, which
// needs no file access beyond the app's own assets (the WebView's allowFileAccess stays off).
//
// Not Metro's assets: Metro bundles a .js file as code, never as a file, and Android names a raw resource without
// its extension, so the engine's .js and .wasm would be one name twice. android/ is generated (CLAUDE.md), so the
// line is written here at each prebuild.
const { withAppBuildGradle } = require('expo/config-plugins')

const ASSETS = `
// The chess engine's files, the website's own (plugins/with-chess-engine.js): at file:///android_asset/.
android.sourceSets.main.assets.srcDirs += file("../../../web/src/sports/engine")
`

module.exports = (config) =>
  withAppBuildGradle(config, (mod) => {
    if (!mod.modResults.contents.includes('web/src/sports/engine')) mod.modResults.contents += ASSETS
    return mod
  })
