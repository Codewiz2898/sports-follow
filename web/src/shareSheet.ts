// How the web app shares a link (components/Share.tsx): the phone's share sheet where the browser has one (Web
// Share), else the link copied, else shown to copy by hand; and how that went, so a share is counted only once it's
// made (a league's invite, for the launch numbers: BUG-193). Free of React and of the page: a test drives it with a
// made-up navigator.

/** How it went: shared from the sheet, copied, shown to copy by hand, or not at all (the sheet or the prompt closed). */
export type Sent = 'shared' | 'copied' | 'shown' | 'cancelled'

/** What of the browser's navigator sharing needs: each may be missing (an older browser, a page not on HTTPS). */
export interface Device {
  share?: (data: { title: string; url: string }) => Promise<void>
  canShare?: (data: { title: string; url: string }) => boolean
  clipboard?: { writeText: (text: string) => Promise<void> }
}

/** Share `url` as `title` by the best way `device` has; `prompt` shows it to copy by hand (window.prompt: null when
 * the fan cancels). A sheet that fails for any reason but the fan closing it falls back to copying. */
export async function sendLink(device: Device, title: string, url: string, prompt: (message: string, value: string) => string | null): Promise<Sent> {
  if (device.share && (!device.canShare || device.canShare({ title, url }))) {
    try {
      await device.share({ title, url })
      return 'shared'
    } catch (e) {
      if ((e as Error).name === 'AbortError') return 'cancelled' // the fan closed the sheet
    }
  }
  try {
    if (!device.clipboard) throw new Error('no clipboard')
    await device.clipboard.writeText(url)
    return 'copied'
  } catch {
    return prompt('Copy this link', url) === null ? 'cancelled' : 'shown' // no clipboard here (a page not on HTTPS)
  }
}
