// The engine's page (android/src/main/java/io/slam/chessengine): a view the app draws, unseen, while a chess
// board's Play from here is open (src/sports/chessEngine.tsx). Null in a build without the module (it's
// Android's only), where the board plays on without an engine.

import { requireNativeView, requireOptionalNativeModule } from 'expo'
import type { ComponentType, Ref } from 'react'
import type { StyleProp, ViewStyle } from 'react-native'

/** What the page can be told: a line for the engine. */
export interface EnginePageHandle {
  tell(line: string): Promise<void>
}

export interface EnginePageProps {
  /** Each word of the page: one of the engine's lines, or the page's own ("!up", "!failed …"). */
  onWord: (event: { nativeEvent: { word: string } }) => void
  style?: StyleProp<ViewStyle>
  ref?: Ref<EnginePageHandle>
}

const EnginePage: ComponentType<EnginePageProps> | null = requireOptionalNativeModule('ChessEngine') ? requireNativeView<EnginePageProps>('ChessEngine') : null

export default EnginePage
