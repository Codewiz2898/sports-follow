package io.stann.recorder

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.PixelFormat
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.view.Gravity
import android.view.MotionEvent
import android.view.ViewConfiguration
import android.view.WindowManager
import android.widget.TextView
import kotlin.math.abs

/** The floating save button over the game: a small blue disc the player drags anywhere and taps to keep the
 *  last 30 seconds. Needs "Display over other apps". With [secure], the window is FLAG_SECURE, which keeps it
 *  out of the recording (Android draws a secure window black, or not at all, on a capture). */
class Bubble(private val context: Context, private val onTap: () -> Unit) {
  private val wm = context.getSystemService(WindowManager::class.java)
  private val main = Handler(Looper.getMainLooper())
  private var view: TextView? = null
  private val density = context.resources.displayMetrics.density
  private fun dp(v: Int) = (v * density).toInt()

  val showing: Boolean get() = view != null

  @SuppressLint("ClickableViewAccessibility")
  fun show(secure: Boolean) {
    hide()
    if (!Settings.canDrawOverlays(context)) return
    val v = TextView(context).apply {
      text = "SAVE\n30s"
      gravity = Gravity.CENTER
      setTextColor(0xFFFFFFFF.toInt())
      textSize = 11f
      typeface = Typeface.DEFAULT_BOLD
      setLineSpacing(0f, 0.9f)
      background = GradientDrawable().apply {
        shape = GradientDrawable.OVAL
        setColor(0xD91F5BEF.toInt())
        setStroke(dp(2), 0xFFFFFFFF.toInt())
      }
      contentDescription = "Save the last 30 seconds"
    }
    var flags = WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS
    if (secure) flags = flags or WindowManager.LayoutParams.FLAG_SECURE
    val lp = WindowManager.LayoutParams(
      dp(56), dp(56), WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY, flags, PixelFormat.TRANSLUCENT,
    ).apply {
      gravity = Gravity.TOP or Gravity.START
      x = dp(12)
      y = dp(140)
    }
    val slop = ViewConfiguration.get(context).scaledTouchSlop
    var downX = 0f; var downY = 0f; var startX = 0; var startY = 0; var moved = false
    v.setOnTouchListener { _, e ->
      when (e.actionMasked) {
        MotionEvent.ACTION_DOWN -> { downX = e.rawX; downY = e.rawY; startX = lp.x; startY = lp.y; moved = false }
        MotionEvent.ACTION_MOVE -> {
          val dx = e.rawX - downX; val dy = e.rawY - downY
          if (abs(dx) > slop || abs(dy) > slop) moved = true
          if (moved) { lp.x = startX + dx.toInt(); lp.y = startY + dy.toInt(); wm.updateViewLayout(v, lp) }
        }
        MotionEvent.ACTION_UP -> if (!moved) onTap()
      }
      true
    }
    wm.addView(v, lp)
    view = v
  }

  /** Says something on the button for a moment ("SAVED"), then goes back. */
  fun flash(text: String) = main.post {
    val v = view ?: return@post
    v.text = text
    main.postDelayed({ view?.text = "SAVE\n30s" }, 1800)
  }

  fun hide() {
    view?.let { runCatching { wm.removeView(it) } }
    view = null
  }
}
