// Expo's own preset, plus the web build's one define (web/vite.config.ts): __APP_NAME__ becomes stann's name from
// sports_follow/app.json, the one place it's written, so the shared modules that read it (web/src/app.ts, through
// predictions.ts and share.ts) work in the app too. Without it the app stops on its first screen.

const fs = require('node:fs')
const path = require('node:path')

const { name } = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'sports_follow', 'app.json'), 'utf8'))
if (!name) throw new Error('sports_follow/app.json has no name')

/** `__APP_NAME__` read anywhere, as the string. */
function appName({ types: t }) {
  return {
    visitor: {
      Identifier(p) {
        if (p.node.name === '__APP_NAME__' && p.isReferencedIdentifier()) p.replaceWith(t.stringLiteral(name))
      },
    },
  }
}

module.exports = (api) => {
  api.cache.using(() => name)
  return { presets: ['babel-preset-expo'], plugins: [appName] }
}
