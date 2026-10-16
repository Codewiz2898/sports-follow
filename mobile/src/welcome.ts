// Whether this phone has been through the welcome, or skipped it: Today sends a fan with no players there once
// (the web's welcomed(), pages/WelcomePage.tsx).

import AsyncStorage from '@react-native-async-storage/async-storage'
import { useEffect, useState } from 'react'

const WELCOMED = 'stann.welcomed'
let known: boolean | null = null

export function markWelcomed() {
  known = true
  AsyncStorage.setItem(WELCOMED, 'done').catch(() => {})
}

/** null while the phone's answer is read; true when storage fails (never loop back to the welcome). */
export function useWelcomed(): boolean | null {
  const [welcomed, setWelcomed] = useState<boolean | null>(known)
  useEffect(() => {
    if (known != null) return
    AsyncStorage.getItem(WELCOMED)
      .then((v) => { known = v === 'done'; setWelcomed(known) })
      .catch(() => { known = true; setWelcomed(true) })
  }, [])
  return welcomed
}
