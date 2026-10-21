// SPIKE (recorder, BHA-314): instant replay of the screen while a game is in front
// (android/src/main/java/io/stann/recorder). Never merged; null in a build without the module.

import { type NativeModule, requireOptionalNativeModule } from 'expo'

export type Audio = 'game' | 'mic' | 'both' | 'none'

export type StartOptions = {
  audio: Audio
  seconds: number
  height: number
  fps: number
  mbps: number
  /** Join the end card when the clip is saved (during the game), rather than leave the clip bare. */
  cardNow: boolean
  bubble: boolean
  /** The floating button's window is FLAG_SECURE, to keep it out of the clip. */
  secure: boolean
  /** Android's consent dialog: its own choice (single app or the screen) or the whole screen only. */
  consent: 'choice' | 'display'
}

export type Clip = { uri?: string; seconds?: number; muxMs?: number; cardMs?: number; mb?: number; endCard?: boolean; audio?: Audio; size?: string; savedAt?: number; cardError?: string; error?: string }

export type State = {
  running: boolean
  seconds?: number
  keep?: number
  size?: string
  fps?: number
  mbps?: number
  audio?: Audio
  frames?: number
  upS?: number
  saving?: boolean
  saves?: number
  lastSave?: Clip | null
  bubble?: boolean
  visible?: boolean | null
  audioError?: string | null
  error?: string | null
  stats?: string | null
}

type Events = { onState(state: State): void }

declare class SlamRecorder extends NativeModule<Events> {
  start(options: StartOptions): Promise<boolean>
  save(): void
  stop(): void
  status(): State
  canOverlay(): boolean
  openOverlaySettings(): void
  setBubble(on: boolean, secure: boolean): void
  /** The saved clips, as JSON. */
  clips(): string
  share(uri: string): void
  open(uri: string): void
}

export default requireOptionalNativeModule<SlamRecorder>('SlamRecorder')
