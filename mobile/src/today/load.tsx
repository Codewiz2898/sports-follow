// The follow list didn't load (the web's LoadFailed, pages/TodayPage.tsx): said, rather than "Pick your
// players" to a fan who has some, on Today and the Following page.

import { useState } from 'react'
import { Text } from 'react-native'
import { useFollowing } from '../following'
import { font } from '../theme'
import { Btn, Notice } from '../ui'

/** The follow list didn't load: say so, rather than "Pick your players" to a fan who has some. */
export function LoadFailed() {
  const { reload } = useFollowing()
  const [trying, setTrying] = useState(false)
  return (
    <Notice>
      <Text style={{ fontFamily: font[600], fontSize: 13, flex: 1 }}>Couldn't load your players.</Text>
      <Btn kind="link" label="Retry" busy={trying} onPress={() => { setTrying(true); reload().finally(() => setTrying(false)) }} />
    </Notice>
  )
}

