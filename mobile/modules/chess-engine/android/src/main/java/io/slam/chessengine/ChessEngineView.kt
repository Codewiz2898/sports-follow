package io.slam.chessengine

import android.annotation.SuppressLint
import android.content.Context
import android.view.View
import android.webkit.RenderProcessGoneDetail
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.webkit.JavaScriptReplyProxy
import androidx.webkit.WebViewAssetLoader
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import expo.modules.kotlin.AppContext
import expo.modules.kotlin.viewevent.EventDispatcher
import expo.modules.kotlin.views.ExpoView
import java.io.ByteArrayInputStream

// The app's own address for what it serves its page, from its assets: WebViewAssetLoader's, which no site has.
// Only the engine's folder is served there (assets/engine: its page and files), none of the app's other assets.
private const val HOST = "appassets.androidplatform.net"
private const val FOLDER = "engine"
private const val PAGE = "https://$HOST/$FOLDER/index.html"
// Every response the page gets carries this policy, the worker's script too (a worker takes its policy from its
// own script's response): its own files and WebAssembly, and nothing else, so no network.
private val POLICY = mapOf(
  "Content-Security-Policy" to "default-src 'none'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self'; connect-src 'self'",
)

/** The engine's page in a WebView of its own, drawn too small to see while a board is open (the app's
 *  src/sports/chessEngine.tsx): its files come from the app's assets/engine at https://appassets.androidplatform.net/engine/
 *  (WebViewAssetLoader, which also types the .wasm application/wasm, as streaming compile needs), never from
 *  file: URLs, so the WebView needs no file access at all. A request for anything else is refused here, whatever
 *  the page asks, and so is every navigation; it keeps nothing (no storage, no cache). Its words come out as
 *  "onWord" events, and `tell` gives it a line, over the WebView's message channel, open to this address alone;
 *  "!gone" is its own, when Android has ended the page's process (the board can start another). */
@SuppressLint("ViewConstructor", "SetJavaScriptEnabled")
class ChessEngineView(context: Context, appContext: AppContext) : ExpoView(context, appContext) {
  override val shouldUseAndroidLayout = true
  private val onWord by EventDispatcher()
  private var reply: JavaScriptReplyProxy? = null
  private var page: WebView? = null

  init {
    importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS
    val files = WebViewAssetLoader.AssetsPathHandler(context)
    val assets = WebViewAssetLoader.Builder()
      .addPathHandler("/$FOLDER/") { path -> if (path.split('/').any { it == ".." || it == "." }) null else files.handle("$FOLDER/$path") }
      .build()
    val web = WebView(context)
    web.settings.apply {
      javaScriptEnabled = true
      allowFileAccess = false
      allowContentAccess = false
      domStorageEnabled = false
      cacheMode = WebSettings.LOAD_NO_CACHE
      setSupportMultipleWindows(false)
      javaScriptCanOpenWindowsAutomatically = false
      setGeolocationEnabled(false)
    }
    web.webViewClient = object : WebViewClient() {
      override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse {
        val served = if (request.url.host == HOST) assets.shouldInterceptRequest(request.url) else null
        if (served?.data == null) return refused()
        served.responseHeaders = served.responseHeaders.orEmpty() + POLICY
        return served
      }

      override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest) = true

      // Its process ended (the phone short of memory, often while the app was behind another): the board hears so,
      // and the app goes on.
      override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
        say("!gone")
        end()
        return true
      }
    }
    addView(web, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.MATCH_PARENT))
    page = web
    if (WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER)) {
      WebViewCompat.addWebMessageListener(web, "app", setOf("https://$HOST")) { _, message, _, isMainFrame, proxy ->
        if (isMainFrame) {
          reply = proxy
          message.data?.let(::say)
        }
      }
      web.loadUrl(PAGE)
    } else {
      post { say("!failed this phone's WebView is too old") }
    }
  }

  private fun say(word: String) = onWord(mapOf("word" to word))

  /** A line for the engine, once its page has spoken. */
  fun tell(line: String) {
    reply?.postMessage(line)
  }

  /** The page goes, its worker with it. */
  fun end() {
    reply = null
    page?.let {
      page = null
      removeView(it)
      it.destroy()
    }
  }

  private fun refused() = WebResourceResponse("text/plain", "utf-8", 403, "Refused", POLICY, ByteArrayInputStream(ByteArray(0)))
}
