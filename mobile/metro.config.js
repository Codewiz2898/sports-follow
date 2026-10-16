// The app shares the API's types and a few pure helpers with the web app (web/src/types.ts, format.ts,
// follow.ts, sports/naming.ts), and its icons (web/public/icons), so Metro watches those folders as well.

const path = require('node:path')
const { getDefaultConfig } = require('expo/metro-config')

const config = getDefaultConfig(__dirname)
config.watchFolders = [...(config.watchFolders ?? []), path.resolve(__dirname, '../web/src'), path.resolve(__dirname, '../web/public/icons')]

module.exports = config
