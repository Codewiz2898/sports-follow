// The web app's icons (web/src/components/Icons.tsx), the same strokes drawn with react-native-svg.

import Svg, { Circle, Path } from 'react-native-svg'
import type { ReactNode } from 'react'
import { colour } from './theme'

interface IconProps { size?: number; color?: string; strokeWidth?: number }

function icon(children: ReactNode, defaultStroke = 2.2) {
  return function Icon({ size = 22, color = colour.text, strokeWidth = defaultStroke }: IconProps) {
    return (
      <Svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round">
        {children}
      </Svg>
    )
  }
}

export const SearchIcon = icon(<><Circle cx="11" cy="11" r="7" /><Path d="m20 20-4-4" /></>)
export const BackIcon = icon(<Path d="m15 5-7 7 7 7" />)
export const CheckIcon = icon(<Path d="m5 12 5 5 9-10" />, 2.6)
export const PlusIcon = icon(<Path d="M12 5v14M5 12h14" />, 2.6)
export const HomeIcon = icon(<Path d="M3 11.5 12 4l9 7.5V20a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z" />)
export const RefreshIcon = icon(<Path d="M20 11a8 8 0 1 0-2.3 5.7M20 5v6h-6" />)
export const SparkIcon = icon(<Path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1" />)
export const ChevronIcon = icon(<Path d="m9 6 6 6-6 6" />)
export const UserIcon = icon(<><Circle cx="12" cy="8" r="4" /><Path d="M4 21a8 8 0 0 1 16 0" /></>)
export const BellIcon = icon(<Path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.9 1.9 0 0 0 3.4 0" />)
export const CloseIcon = icon(<Path d="M6 6l12 12M18 6 6 18" />, 2.6)
export const OutIcon = icon(<Path d="M7 17 17 7M8 7h9v9" />)
/** Play, filled (a video's play button, Watch live's). */
export function PlayIcon({ size = 22, color = colour.text }: { size?: number; color?: string }) {
  return <Svg width={size} height={size} viewBox="0 0 24 24" fill={color}><Path d="M8 5v14l11-7z" /></Svg>
}
export const ShareIcon = icon(<><Circle cx="18" cy="5" r="2.6" /><Circle cx="6" cy="12" r="2.6" /><Circle cx="18" cy="19" r="2.6" /><Path d="m8.4 13.3 7.2 4.3M15.6 6.4l-7.2 4.3" /></>)
