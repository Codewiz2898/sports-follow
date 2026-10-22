// Whether a part of the app that loads as it's needed (a lazy page, the chess board's Play from here) failed because
// the build this tab runs was replaced: then the tab reloads onto the new one (main.tsx). No browser here, so
// tests/test_web_stale_build.py runs it in Node.

/** The app's file that failed to load, as the browser's error names it ("Failed to fetch dynamically imported module:
 * https://stann.in/assets/chessAnalysis-3f2a9c.js", Vite's "Unable to preload CSS for /assets/…"), by its path on
 * this site; null where it doesn't (Safari's "Importing a module script failed."). */
export function failedFile(error: unknown): string | null {
  const said = error instanceof Error ? error.message : typeof error === 'string' ? error : ''
  return said.match(/\/assets\/[^\s'"?#]+/)?.[0] ?? null
}

/** What the tab can find out about its build: whether the device is online (navigator.onLine), what the server
 * answers for a file now (`status`: rejects when it can't be asked), and whether a newer build is waiting, or took
 * over from another tab (`newer`: the service worker's). */
export interface BuildLooks {
  online: boolean
  status: (file: string) => Promise<number>
  newer: () => Promise<boolean>
}

/**
 * Whether a file that failed to load means the build was replaced (BUG-308): online, and the server no longer has
 * the file (a 404: a deploy replaced the build, and the service worker dropped the old one's files), or, where the
 * error doesn't say which file, a newer build is waiting or in charge. Offline, or on a network that dropped the
 * file, it doesn't: the page says it couldn't open ("couldn't open the board"), where a reload would lose the fan's
 * place, and offline land on the app's shell.
 */
export async function buildReplaced(error: unknown, looks: BuildLooks): Promise<boolean> {
  if (!looks.online) return false
  const file = failedFile(error)
  try {
    return file ? (await looks.status(file)) === 404 : await looks.newer()
  } catch {
    return false  // nothing could be asked: not known to be a new build
  }
}
