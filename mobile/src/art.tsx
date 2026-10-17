// A sport's ground as faint line art behind a card (the web's Backdrop, web/src/sports/index.tsx): the
// sport's own art (web/src/sports/facts/), turned, placed and sized by the game's seed (facts.artPlace) so a
// row of cards from one sport doesn't repeat, in the card's ink, cropped by the card.

import { useState } from 'react'
import { StyleSheet, View } from 'react-native'
import { SvgXml } from 'react-native-svg'
import { artPlace, factsOf } from '../../web/src/sports/facts'

const STROKE = 1.2 // px on screen, whatever the art's size (the web's vector-effect: non-scaling-stroke)

/** The art, its strokes scaled so they come out STROKE px wide at `size` px: the 100-unit box is drawn
 * size/100 px a unit. */
function markup(art: string, size: number, colour: string): string {
  const unit = 100 / size
  const scaled = art.replace(/stroke-width="([\d.]+)"/g, (_, w: string) => `stroke-width="${(Number(w) / 1.2) * STROKE * unit}"`)
  return `<svg viewBox="0 0 100 100" fill="none" stroke="${colour}" stroke-width="${STROKE * unit}" stroke-linecap="round" stroke-linejoin="round" color="${colour}">${scaled.replace(/currentColor/g, colour)}</svg>`
}

/** Fills its card (which clips it: overflow hidden) with the sport's art; nothing for a sport that draws none. */
export function Backdrop({ sport, seed, colour, opacity = 0.18 }: { sport: string; seed: string; colour: string; opacity?: number }) {
  const [box, setBox] = useState<{ w: number; h: number } | null>(null)
  const art = factsOf(sport)?.art
  if (!art) return null
  const at = artPlace(seed)
  const size = box ? at.size * Math.min(box.w, 320) : 0
  return (
    <View style={StyleSheet.absoluteFill} pointerEvents="none" onLayout={(e) => setBox({ w: e.nativeEvent.layout.width, h: e.nativeEvent.layout.height })}>
      {box && size > 0 ? (
        <View style={{ position: 'absolute', left: (box.w * at.x) / 100 - size / 2, top: (box.h * at.y) / 100 - size / 2, width: size, height: size, opacity, transform: [{ rotate: `${at.turn}deg` }] }}>
          <SvgXml xml={markup(art, size, colour)} width={size} height={size} />
        </View>
      ) : null}
    </View>
  )
}
