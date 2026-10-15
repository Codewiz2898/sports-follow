// stann's look, as the web app's (web/src/styles.css): bold sport colour blocks on light paper, Anton for
// display, Manrope for text. Each sport's own colours come from the server (sports.tsx), never from here.

import { StyleSheet } from 'react-native'

export const colour = {
  paper: '#F3F3EF',
  surface: '#FFFFFF',
  raised: '#E8E8E2',
  line: '#DEDED8',
  ink: '#0D0D0D',
  muted: '#55575C',
  live: '#DC2016',
  liveText: '#B8190F',
  brand: '#1F5BEF', // stann's own blue, whatever the sports wear (web/scripts/make_icons.py)
}

// The names the expo-font plugin gives the files in app.config.ts.
export const font = {
  display: 'Anton_400Regular',
  body: 'Manrope_500Medium',
  bold: 'Manrope_700Bold',
  heavy: 'Manrope_800ExtraBold',
  logo: 'Fredoka_600SemiBold',
}

export const GUTTER = 16

export const type = StyleSheet.create({
  // Anton, upper case and leaning forward, as the web's .dsp.
  display: { fontFamily: font.display, textTransform: 'uppercase', transform: [{ skewX: '-7deg' }], letterSpacing: 0.2 },
  number: { fontFamily: font.display, fontVariant: ['tabular-nums'] },
  eyebrow: { fontFamily: font.heavy, fontSize: 11, letterSpacing: 1, textTransform: 'uppercase', color: colour.muted },
  body: { fontFamily: font.body, fontSize: 15, lineHeight: 21, color: colour.ink },
  bold: { fontFamily: font.bold, fontSize: 15, lineHeight: 21, color: colour.ink },
  small: { fontFamily: font.body, fontSize: 13, lineHeight: 18, color: colour.muted },
})
