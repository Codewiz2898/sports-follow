// The app's name as fans see it, from sports_follow/app.json (config.APP_NAME), the one place it's
// written: the build (vite.config.ts) fills it in here, as it does in index.html, the manifest and the
// service worker, so every part of one build says the same name. The API names itself by the same value.
declare const __APP_NAME__: string

export const APP_NAME = __APP_NAME__
