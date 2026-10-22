// Where this phone's fan came from, as the web app tells it (web/src/visit.ts, server.visit_source): the tags on the
// site's link that opened the app (Android App Links: a campaign's post, a shared page; web/src/visitTags.ts), or one
// opened while it runs, told once a phone, so the launch numbers count fans a link brought to the app as well as
// those it brought to the site. Run inside the account's screens, which wait for this phone's fan (account.tsx): the
// server keeps it under that fan. Marked told once the server has it, so one that didn't get there is told next time.

import AsyncStorage from '@react-native-async-storage/async-storage'
import { useEffect } from 'react'
import { Linking } from 'react-native'
import { linkVisit } from '../../web/src/visitTags'
import { api } from './api'

const NOTED = 'stann.source-noted'

/** Tells the server the tags of the link that opened the app, or of one opened while it runs, the first time a link
 * has any. */
export function useVisitNoted(): void {
  useEffect(() => {
    let telling = false
    const heard = (url: string | null) => {
      const visit = url ? linkVisit(url) : null
      if (!visit || telling) return
      telling = true
      AsyncStorage.getItem(NOTED)
        .then((noted) => (noted ? undefined : api.visitSource(visit.tags, visit.path).then(() => AsyncStorage.setItem(NOTED, '1'))))
        .catch(() => { /* only a source not counted: told on the next tagged link */ })
        .finally(() => { telling = false })
    }
    Linking.getInitialURL().then(heard).catch(() => {})
    const sub = Linking.addEventListener('url', ({ url }) => heard(url))
    return () => sub.remove()
  }, [])
}
