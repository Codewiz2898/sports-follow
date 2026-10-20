// The app shares the API's types and a few pure helpers with the web app (web/src/types.ts, format.ts,
// follow.ts, sports/naming.ts), and its icons (web/public/icons), so Metro watches those folders as well.

const fs = require('node:fs')
const path = require('node:path')
const { getDefaultConfig } = require('expo/metro-config')

const config = getDefaultConfig(__dirname)
// babel.config.js writes the app's name into the code it transforms; a new name has Metro transform it again.
const { name } = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'sports_follow', 'app.json'), 'utf8'))
config.cacheVersion = `${config.cacheVersion ?? ''}:${name}`
config.watchFolders = [...(config.watchFolders ?? []), path.resolve(__dirname, '../web/src'), path.resolve(__dirname, '../web/public/icons')]

module.exports = config
