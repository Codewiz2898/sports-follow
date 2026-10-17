// An address the app has no page for (Expo Router's +not-found), as the web's catch-all route: a stann.in link
// under one of the app's folders (App Links, app.config.ts) that names nothing in it.

import { Page } from '../page'
import { NotFound } from '../ui'

export default function NotFoundScreen() {
  return <Page><NotFound /></Page>
}
