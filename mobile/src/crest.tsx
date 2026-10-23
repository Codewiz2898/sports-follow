// A team's crest, the web's components/Crest.tsx: its logo from our own origin, in a disc where it would vanish on the
// colour under it, or our badge (its short code in a white ring) where it has none, its sport's logos are off, or it
// doesn't load. Ringed (ring) in the colour of what's around it, as the team banner's seam rings it; there a logo wider
// than tall (a flag, or a wordmark) sits whole on a 3:2 plate of its disc's colour, which a flag fills.

import { Image } from 'expo-image'
import { useState } from 'react'
import { Text, View } from 'react-native'
import { initials } from '../../web/src/format'
import { absolute, type Look } from './api'
import { colour, font } from './theme'

const RING = 4  // the ring's width (.team-mark .crest)

export function Crest({ look, name, size = 40, disc = null, ring }: { look: Look | null | undefined; name: string; size?: number; disc?: string | null; ring?: string }) {
  const [failed, setFailed] = useState<string | null>(null)  // the address that didn't load
  const [wide, setWide] = useState(false)
  const r = ring ? RING : 0
  if (look?.logo && failed !== look.logo) {
    const logo = look.logo
    const plate = wide && ring != null
    const w = plate ? size * 1.1 : size
    const h = plate ? w / 1.5 : size
    const round = plate ? 14 : disc ? size / 2 : 0
    const img = plate ? { width: w, height: h } : disc ? { width: size * 0.7, height: size * 0.7 } : { width: size, height: size, borderRadius: size * 0.08 }
    return (
      <View importantForAccessibility="no-hide-descendants" style={{ padding: r, borderRadius: round + r, backgroundColor: ring }}>
        <View style={{ width: w, height: h, borderRadius: round, overflow: 'hidden', alignItems: 'center', justifyContent: 'center', backgroundColor: disc ?? undefined }}>
          <Image source={{ uri: absolute(logo) }} style={img} contentFit="contain" onError={() => setFailed(logo)} onLoad={(e) => setWide(e.source.width > e.source.height * 1.25)} />
        </View>
      </View>
    )
  }
  const short = (look?.short || initials(name)).slice(0, 4)
  return (
    <View importantForAccessibility="no-hide-descendants" style={{ padding: r, borderRadius: size / 2 + r, backgroundColor: ring }}>
      <View style={{ width: size, height: size, borderRadius: size / 2, borderWidth: Math.max(2, size / 22), borderColor: colour.text, backgroundColor: colour.surface, alignItems: 'center', justifyContent: 'center' }}>
        <Text allowFontScaling={false} style={{ fontFamily: font.display, fontSize: size * (short.length > 3 ? 0.27 : 0.34), color: colour.text, textTransform: 'uppercase' }}>{short}</Text>
      </View>
    </View>
  )
}
