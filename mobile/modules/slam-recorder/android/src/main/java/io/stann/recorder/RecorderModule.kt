package io.stann.recorder

import android.app.Activity
import android.content.ClipData
import android.content.Context
import android.content.Intent
import android.media.projection.MediaProjectionConfig
import android.media.projection.MediaProjectionManager
import android.net.Uri
import android.provider.Settings
import androidx.media3.common.util.UnstableApi
import expo.modules.kotlin.Promise
import expo.modules.kotlin.functions.Queues
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition

/** What the spike's page (src/app/recorder.tsx) asks of the recorder: start it (Android's consent first), save,
 *  stop, the floating button, the clips saved and sharing one. It hears the service change ("onState"). */
@UnstableApi
class RecorderModule : Module() {
  private var pending: Promise? = null
  private var options: Map<String, Any?> = emptyMap()

  override fun definition() = ModuleDefinition {
    Name("SlamRecorder")
    Events("onState")

    OnCreate { ReplayService.onChange = { sendEvent("onState", it) } }
    OnDestroy { ReplayService.onChange = null }

    AsyncFunction("start") { opts: Map<String, Any?>, promise: Promise ->
      val activity = appContext.currentActivity ?: return@AsyncFunction promise.reject("NO_ACTIVITY", "no activity", null)
      val mpm = context.getSystemService(MediaProjectionManager::class.java)
      // "choice" is Android's own dialog (single app or the whole screen, where the phone offers both);
      // "display" asks only for the whole screen.
      val intent = if (opts["consent"] == "display") mpm.createScreenCaptureIntent(MediaProjectionConfig.createConfigForDefaultDisplay())
      else mpm.createScreenCaptureIntent(MediaProjectionConfig.createConfigForUserChoice())
      pending = promise
      options = opts
      activity.startActivityForResult(intent, REQUEST)
    }.runOnQueue(Queues.MAIN)

    OnActivityResult { _, payload ->
      if (payload.requestCode != REQUEST) return@OnActivityResult
      val promise = pending ?: return@OnActivityResult
      pending = null
      val data = payload.data
      if (payload.resultCode != Activity.RESULT_OK || data == null) {
        promise.resolve(false)
        return@OnActivityResult
      }
      val o = options
      val start = Intent(context, ReplayService::class.java).setAction(ReplayService.START)
        .putExtra("code", payload.resultCode)
        .putExtra("data", data)
        .putExtra("audio", o["audio"] as? String ?: "game")
        .putExtra("seconds", (o["seconds"] as? Number)?.toInt() ?: 30)
        .putExtra("height", (o["height"] as? Number)?.toInt() ?: 720)
        .putExtra("fps", (o["fps"] as? Number)?.toInt() ?: 30)
        .putExtra("mbps", (o["mbps"] as? Number)?.toDouble() ?: 5.0)
        .putExtra("cardNow", o["cardNow"] as? Boolean ?: true)
        .putExtra("bubble", o["bubble"] as? Boolean ?: false)
        .putExtra("secure", o["secure"] as? Boolean ?: false)
      context.startForegroundService(start)
      promise.resolve(true)
    }

    Function("save") { send(ReplayService.SAVE) }
    Function("stop") { send(ReplayService.STOP) }
    Function("status") { ReplayService.status() }
    Function("canOverlay") { Settings.canDrawOverlays(context) }
    Function("openOverlaySettings") {
      val i = Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:${context.packageName}"))
      (appContext.currentActivity ?: context).startActivity(i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }
    Function("setBubble") { on: Boolean, secure: Boolean ->
      if (ReplayService.current == null) return@Function
      context.startService(Intent(context, ReplayService::class.java).setAction(ReplayService.BUBBLE).putExtra("on", on).putExtra("secure", secure))
    }
    Function("clips") { Clips.list(context) }
    Function("share") { uri: String ->
      val u = Uri.parse(uri)
      val send = Intent(Intent.ACTION_SEND).setType("video/mp4").putExtra(Intent.EXTRA_STREAM, u)
        .putExtra(Intent.EXTRA_TEXT, "Clipped on $NAME").addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
      send.clipData = ClipData.newRawUri("clip", u)
      val chooser = Intent.createChooser(send, "Share your clip")
      val activity = appContext.currentActivity
      if (activity != null) activity.startActivity(chooser) else context.startActivity(chooser.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }
    Function("open") { uri: String ->
      val view = Intent(Intent.ACTION_VIEW).setDataAndType(Uri.parse(uri), "video/mp4").addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
      (appContext.currentActivity ?: context).startActivity(view.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }
  }

  private fun send(action: String) {
    if (ReplayService.current == null) return
    context.startService(Intent(context, ReplayService::class.java).setAction(action))
  }

  private val context: Context
    get() = requireNotNull(appContext.reactContext) { "the app's context is gone" }

  companion object {
    private const val REQUEST = 7302
  }
}
