package io.stann.notifications

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Rect
import android.graphics.Typeface
import android.text.TextPaint
import android.text.TextUtils
import android.util.TypedValue
import androidx.core.content.res.ResourcesCompat
import java.util.Locale
import kotlin.math.ceil
import kotlin.math.max
import kotlin.math.min

/**
 * The app's display type as pictures, for a notification's layout: Anton, leaning for a moment's word and
 * upright for scores and sides (web/src/styles.css's .dsp and .num). Android draws a notification's layout in
 * its own process, which can't load the app's fonts, so the app draws these words itself and the layout shows
 * them as images (res/values/stann_styles.xml's StannType). Each is cut to the words' own ink, at the phone's
 * density, so it shows at its size in sp.
 */
internal class Type(c: Context) {
  private val metrics = c.resources.displayMetrics
  private val leaning = font(c, R.font.stann_anton_slanted)
  private val upright = font(c, R.font.stann_anton)

  /** About how wide a notification's opened content is, in dp: the screen less Android's icon column and
   *  margins. Words that wouldn't fit what a layout leaves them are cut short ("…"). */
  val wide = metrics.widthPixels / metrics.density - 100f

  fun word(text: String, sp: Float, colour: Int, maxDp: Float) = draw(text, sp, colour, leaning, maxDp)

  fun number(text: String, sp: Float, colour: Int, maxDp: Float) = draw(text, sp, colour, upright, maxDp)

  private fun draw(text: String, sp: Float, colour: Int, face: Typeface?, maxDp: Float): Bitmap? {
    val words = text.trim().uppercase(Locale.ROOT)
    if (words.isEmpty()) return null
    val paint = TextPaint(Paint.ANTI_ALIAS_FLAG).apply {
      typeface = face ?: Typeface.DEFAULT_BOLD
      textSize = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_SP, sp, metrics)
      color = colour
    }
    val shown = TextUtils.ellipsize(words, paint, max(1f, maxDp) * metrics.density, TextUtils.TruncateAt.END).toString()
    if (shown.isEmpty()) return null
    val ink = Rect().also { paint.getTextBounds(shown, 0, shown.length, it) }
    val pad = ceil(metrics.density).toInt() // a leaning letter's corner, and smoothing, past its bounds
    val left = min(0, ink.left)
    val width = ceil(max(paint.measureText(shown), ink.right.toFloat())).toInt() - left + 2 * pad
    val height = ink.height() + 2 * pad
    return Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888).apply {
      density = metrics.densityDpi
      Canvas(this).drawText(shown, (pad - left).toFloat(), (pad - ink.top).toFloat(), paint)
    }
  }

  private fun font(c: Context, id: Int): Typeface? = try {
    ResourcesCompat.getFont(c, id)
  } catch (_: Exception) {
    null // the phone's bold, then
  }
}
