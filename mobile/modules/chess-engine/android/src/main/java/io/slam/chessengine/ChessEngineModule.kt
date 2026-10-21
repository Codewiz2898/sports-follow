package io.slam.chessengine

import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition

/** The engine's page as a view the app draws (ChessEngineView): what it says comes as "onWord", `tell` gives it a
 *  line, and it goes when the view does. */
class ChessEngineModule : Module() {
  override fun definition() = ModuleDefinition {
    Name("ChessEngine")
    View(ChessEngineView::class) {
      Events("onWord")
      AsyncFunction("tell") { view: ChessEngineView, line: String -> view.tell(line) }
      OnViewDestroys { view: ChessEngineView -> view.end() }
    }
  }
}
