// The app's look, the web app's own (web/src/styles.css, "Matchday"): a light paper ground, ink type, and each
// sport's colour as a solid block. Red means "happening now" and nothing else. The values here are that
// stylesheet's; each sport's colours come from the server (sports.tsx), never from here.

import { PixelRatio, StyleSheet, type StyleProp, type TextStyle } from 'react-native'

export const colour = {
  bg: '#F3F3EF',
  surface: '#FFFFFF',
  raised: '#E8E8E2',
  line: '#DEDED8',
  text: '#0D0D0D',
  muted: '#55575C',
  live: '#DC2016',
  liveText: '#B8190F', // red as text: 4.5:1 or better on every light surface
  danger: '#B42318',
  dangerBg: '#FDE8E6',
  inkMuted: '#A9ABB0', // muted text on ink (the board)
  inkLine: 'rgba(255,255,255,0.16)',
  inkRaised: '#26272A',
  follow: '#1F5BEF', // Follow on a Feed card (--follow): the boards' one blue, never a sport's identity
}

// The names the expo-font plugin gives the files in app.config.ts, by weight.
export const font = {
  display: 'Anton_400Regular',
  slanted: 'AntonSlanted', // Anton leaning 7 degrees (scripts/slant_font.py): Android draws a skewed Text upright
  logo: 'Fredoka_600SemiBold', // the name, in lower case like the badge's "s" (web/src/styles.css .brand)
  500: 'Manrope_500Medium',
  600: 'Manrope_600SemiBold',
  700: 'Manrope_700Bold',
  800: 'Manrope_800ExtraBold',
}

export const GUTTER = 16
export const RADIUS_CARD = 22
export const RADIUS_TILE = 20

/** Anton, upper case and leaning forward (.dsp): names, scores, headings. Its line is 0.95 of its size (the
 * site's 0.92). The lean is in the letters (font.slanted), not a skew, which Android doesn't draw on text. A
 * Text that adds bottom padding or margin, or a height, passes them through dspInk (Dsp's `style` does), or
 * the ink's room moves the page. */
export function dsp(size: number): TextStyle {
  return { fontFamily: font.slanted, fontSize: size, lineHeight: Math.round(size * 0.95), textTransform: 'uppercase', letterSpacing: size * 0.005, color: colour.text, ...dspInk(size) }
}

// How far Anton's ink sinks below a .dsp line, as a share of the size: the line (0.95 of the size, centred on
// the face as CSS centres it) ends 0.05 below the baseline, and an underscore, a comma, Q's tail, a cedilla or
// a comma below (Ç, Ș) reaches 0.31. The site lets ink overflow its line; Android cuts what a Text draws at
// its box, so `not_me` read "NOT ME".
const INK_BELOW = 0.27

/** The room dsp() gives that ink: padding under the line that as much negative margin takes back, so the
 * Text's box holds the ink and the page is laid out as before. It grows with the phone's font size, as the
 * letters do. Only below: a box grown upwards would take taps from what's above it (Android hits the later
 * view first, and its Text has no pointerEvents), so an accent on a first line's capital stays cut (BHA-362).
 * A style's own bottom padding and margin are added to the room; a percentage, `auto` or a height keeps them,
 * without it. The padding is whole pixels: Android's TextView draws into its bottom padding only when the box
 * is exactly its text and padding, which a part pixel breaks. */
export function dspInk(size: number, style?: StyleProp<TextStyle>): TextStyle {
  const own = StyleSheet.flatten(style) ?? {}
  const pad = own.paddingBottom ?? own.paddingVertical ?? own.padding ?? 0
  const margin = own.marginBottom ?? own.marginVertical ?? own.margin ?? 0
  if (typeof pad !== 'number' || typeof margin !== 'number' || own.height != null || own.minHeight != null) return { paddingBottom: pad, marginBottom: margin }
  const padding = PixelRatio.roundToNearestPixel(pad + size * PixelRatio.getFontScale() * INK_BELOW)
  return { paddingBottom: padding, marginBottom: margin - (padding - pad) }
}

/** Anton numbers, upright (.num). */
export function num(size: number): TextStyle {
  return { fontFamily: font.display, fontSize: size, lineHeight: Math.round(size * 1.05), fontVariant: ['tabular-nums'], letterSpacing: size * 0.01, color: colour.text }
}

export const t = StyleSheet.create({
  body: { fontFamily: font[500], fontSize: 15, lineHeight: 22, color: colour.text },
  tag: { fontFamily: font[800], fontSize: 11, letterSpacing: 1, textTransform: 'uppercase' },
  eyebrow: { fontFamily: font[800], fontSize: 11, letterSpacing: 1, textTransform: 'uppercase', color: colour.muted },
  title: { fontFamily: font[700], fontSize: 15, lineHeight: 19.5, color: colour.text },
  small: { fontFamily: font[500], fontSize: 13, lineHeight: 18.5, color: colour.text },
  tiny: { fontFamily: font[500], fontSize: 12, lineHeight: 17, color: colour.text },
  muted: { color: colour.muted },
  link: { fontFamily: font[800], textDecorationLine: 'underline' },
})
