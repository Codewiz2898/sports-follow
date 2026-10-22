// Where this phone's fan came from, as the web app tells it (web/src/visit.ts, server.visit_source): the tags on the
// site's link that opened the app (Android App Links: a campaign's post, a shared page; web/src/visitTags.ts), or one
// opened while it runs, told once a phone, so the launch numbers count fans a link brought to the app as well as
// those it brought to the site. The server keeps it under this phone's fan, and only one it has made (its cookie), so
// it's told once the server has said who the phone is (account.tsx's `me`): a first launch slower than the account's
// longest wait starts the screens before that, and a visit told then wasn't kept, yet was marked told (BUG-315).
// Marked told once the server has it, so one that didn't get there is told next time.

import AsyncStorage from '@react-native-async-storage/async-storage'
import { useCallback, useEffect, useRef } from 'react'
import { Linking } from 'react-native'
import { linkVisit } from '../../web/src/visitTags'
import { useAccount } from './account'
import { api } from './api'

const NOTED = 'stann.source-noted'

/** Tells the server the tags of the link that opened the app, or of one opened while it runs, the first time a link
 * has any, once the phone's fan is made. */
export function useVisitNoted(): void {
  const known = useAccount().me !== null // the server has said who this phone is: its fan is made
  const knownNow = useRef(known)
  knownNow.current = known
  const waiting = useRef<ReturnType<typeof linkVisit>>(null) // a tagged link heard and not told yet
  const telling = useRef(false)
  const tell = useCallback(() => {
    const visit = waiting.current
    if (!visit || telling.current || !knownNow.current) return
    waiting.current = null
    telling.current = true
    AsyncStorage.getItem(NOTED)
      .then((noted) => (noted ? undefined : api.visitSource(visit.tags, visit.path).then(() => AsyncStorage.setItem(NOTED, '1'))))
      .catch(() => { /* only a source not counted: told on the next tagged link */ })
      .finally(() => { telling.current = false })
  }, [])
  useEffect(() => {
    const heard = (url: string | null) => {
      const visit = url ? linkVisit(url) : null
      if (!visit || telling.current) return
      waiting.current ??= visit // the first: the server keeps a fan's first
      tell()
    }
    Linking.getInitialURL().then(heard).catch(() => {})
    const sub = Linking.addEventListener('url', ({ url }) => heard(url))
    return () => sub.remove()
  }, [tell])
  useEffect(() => { if (known) tell() }, [known, tell])
}
