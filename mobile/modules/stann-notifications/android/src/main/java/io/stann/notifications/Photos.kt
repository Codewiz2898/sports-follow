package io.stann.notifications

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.BitmapShader
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Shader
import android.os.SystemClock
import android.util.Log
import java.io.ByteArrayOutputStream
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

/** A player's photo for a scorecard, in a circle: from this phone's cache, else fetched once, within DEADLINE
 *  all told however slowly it comes, and kept (the server never changes a photo at its address). Null for none:
 *  no photo, or it didn't come in time; the card shows without it. */
internal object Photos {
  private const val LOG = "StannNotifications"
  private const val SIZE = 120 // pixels: the circle at its largest
  private const val DEADLINE = 5000L // milliseconds for the whole download
  private const val MAX_BYTES = 4 * 1024 * 1024 // a photo is a few hundred KB (photos.py); past this it isn't one
  private const val KEPT = 200 // the newest this many are kept; the phone may clear them sooner

  fun get(c: Context, url: String?): Bitmap? {
    if (url.isNullOrBlank() || !(url.startsWith("https://") || url.startsWith("http://"))) return null
    val dir = File(c.cacheDir, "stann-photos")
    val file = File(dir, digest(url) + ".png")
    if (file.exists()) BitmapFactory.decodeFile(file.path)?.let { return it }
    val circle = download(url)?.let { decode(it) }?.let { circle(it) } ?: return null
    try {
      dir.mkdirs()
      file.outputStream().use { circle.compress(Bitmap.CompressFormat.PNG, 100, it) }
      dir.listFiles()?.sortedByDescending { it.lastModified() }?.drop(KEPT)?.forEach { it.delete() }
    } catch (e: Exception) {
      Log.w(LOG, "couldn't keep a photo", e)
    }
    return circle
  }

  private fun download(url: String): ByteArray? {
    val started = SystemClock.elapsedRealtime()
    return try {
      val connection = URL(url).openConnection() as HttpURLConnection
      connection.connectTimeout = DEADLINE.toInt()
      connection.readTimeout = DEADLINE.toInt()
      try {
        val out = ByteArrayOutputStream()
        val chunk = ByteArray(16 * 1024)
        connection.inputStream.use { input ->
          while (true) {
            val n = input.read(chunk)
            if (n < 0) break
            out.write(chunk, 0, n)
            if (out.size() > MAX_BYTES || SystemClock.elapsedRealtime() - started > DEADLINE) return null
          }
        }
        out.toByteArray()
      } finally {
        connection.disconnect()
      }
    } catch (e: Exception) {
      Log.w(LOG, "no photo from $url", e)
      null
    }
  }

  /** The photo, read at no more than about twice the circle's size, so a large one costs little memory. */
  private fun decode(bytes: ByteArray): Bitmap? {
    val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
    var sample = 1
    while (minOf(bounds.outWidth, bounds.outHeight) / (sample * 2) >= SIZE * 2) sample *= 2
    return BitmapFactory.decodeByteArray(bytes, 0, bytes.size, BitmapFactory.Options().apply { inSampleSize = sample })
  }

  private fun circle(image: Bitmap): Bitmap {
    val side = minOf(image.width, image.height)
    val size = minOf(side, SIZE)
    // The square from the top centre: a portrait's face is nearer its top than its middle.
    val square = Bitmap.createBitmap(image, (image.width - side) / 2, 0, side, side)
    val scaled = Bitmap.createScaledBitmap(square, size, size, true)
    val out = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888)
    val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply { shader = BitmapShader(scaled, Shader.TileMode.CLAMP, Shader.TileMode.CLAMP) }
    Canvas(out).drawCircle(size / 2f, size / 2f, size / 2f, paint)
    return out
  }

  private fun digest(url: String): String =
    MessageDigest.getInstance("SHA-1").digest(url.toByteArray()).joinToString("") { "%02x".format(it) }
}
