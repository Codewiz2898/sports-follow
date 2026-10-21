// The app shares the API's types and a few pure helpers with the web app (web/src/types.ts, format.ts,
// follow.ts, sports/naming.ts, sports/facts), and its icons (web/public/icons), so Metro watches those folders as well.

const fs = require('node:fs')
const path = require('node:path')
const { getDefaultConfig } = require('expo/metro-config')

const config = getDefaultConfig(__dirname)
// babel.config.js writes the app's name into the code it transforms; a new name has Metro transform it again.
const { name } = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'sports_follow', 'app.json'), 'utf8'))
config.cacheVersion = `${config.cacheVersion ?? ''}:${name}`
config.watchFolders = [...(config.watchFolders ?? []), path.resolve(__dirname, '../web/src'), path.resolve(__dirname, '../web/public/icons')]
// A package the shared files import (chess.js, for a chess board's Play from here: web/src/sports/facts/chessLine.ts)
// is the app's own (package.json), as if the app imported it, never web/node_modules: a build of the app may have
// no web packages, and with them, one copy is the app's.
const WEB = path.resolve(__dirname, '../web/src') + path.sep
const ROOT = path.join(__dirname, 'package.json')
config.resolver.resolveRequest = (context, name, platform) =>
  context.resolveRequest(context.originModulePath.startsWith(WEB) && !name.startsWith('.') ? { ...context, originModulePath: ROOT } : context, name, platform)

module.exports = config
