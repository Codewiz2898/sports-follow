package io.stann.recorder

import android.content.ContentValues
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.Typeface
import android.net.Uri
import android.os.Handler
import android.os.HandlerThread
import android.provider.MediaStore
import androidx.media3.common.MediaItem
import androidx.media3.common.MimeTypes
import androidx.media3.common.util.UnstableApi
import androidx.media3.effect.Presentation
import androidx.media3.transformer.Composition
import androidx.media3.transformer.EditedMediaItem
import androidx.media3.transformer.EditedMediaItemSequence
import androidx.media3.transformer.Effects
import androidx.media3.transformer.ExportException
import androidx.media3.transformer.ExportResult
import androidx.media3.transformer.Transformer
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

const val TAG = "SlamReplay"
const val NAME = "Slam" // the app's new name (BHA-348); user-facing copy in this spike says it

private const val PAPER = 0xFFF3F3EF.toInt()
private const val INK = 0xFF0D0D0D.toInt()
private const val BLUE = 0xFF1F5BEF.toInt()
private const val CARD_MS = 1800L

/** The branded end: a still "clipped on Slam" card, joined to the clip (both re-encoded, Transformer), and the
 *  result put in the gallery (Movies/Slam). */
@UnstableApi
object EndCard {
  private val thread by lazy { HandlerThread("slam-export").apply { start() } }

  /** Draws the card at the clip's size: paper, an ink block, the name in Anton as the logo leans. */
  fun draw(context: Context, w: Int, h: Int, file: File) {
    val bmp = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888)
    val c = Canvas(bmp)
    c.drawColor(PAPER)
    val anton = font(context, "slam_anton_slanted") ?: Typeface.DEFAULT_BOLD
    val manrope = font(context, "slam_manrope") ?: Typeface.DEFAULT_BOLD
    val p = Paint(Paint.ANTI_ALIAS_FLAG)
    // The ink block, a sport-colour-style slab across the middle.
    val block = RectF(w * 0.08f, h * 0.22f, w * 0.92f, h * 0.78f)
    p.color = INK
    c.drawRoundRect(block, h * 0.05f, h * 0.05f, p)
    p.typeface = manrope
    p.color = Color.WHITE
    p.textSize = h * 0.07f
    p.letterSpacing = 0.12f
    p.textAlign = Paint.Align.CENTER
    c.drawText("CLIPPED ON", w / 2f, block.top + h * 0.15f, p)
    p.typeface = anton
    p.letterSpacing = 0f
    p.textSize = h * 0.26f
    p.color = Color.WHITE
    c.drawText(NAME.uppercase(), w / 2f, block.top + h * 0.42f, p)
    // The badge's blue as an underline, and where to get it.
    p.color = BLUE
    c.drawRect(w / 2f - w * 0.09f, block.top + h * 0.455f, w / 2f + w * 0.09f, block.top + h * 0.475f, p)
    p.typeface = manrope
    p.color = INK
    p.textSize = h * 0.055f
    p.letterSpacing = 0.04f
    c.drawText("Follow your players · get the app", w / 2f, h * 0.89f, p)
    file.outputStream().use { bmp.compress(Bitmap.CompressFormat.PNG, 100, it) }
    bmp.recycle()
  }

  private fun font(context: Context, name: String): Typeface? {
    val id = context.resources.getIdentifier(name, "font", context.packageName)
    return if (id == 0) null else runCatching { context.resources.getFont(id) }.getOrNull()
  }

  /** The clip with the card after it, at [dest]. Blocks until it's done (call it off the main thread). */
  fun join(context: Context, raw: File, card: File, w: Int, h: Int, hasAudio: Boolean, dest: File) {
    val size = Effects(listOf(), listOf(Presentation.createForWidthAndHeight(w, h, Presentation.LAYOUT_SCALE_TO_FIT)))
    val clip = EditedMediaItem.Builder(MediaItem.fromUri(Uri.fromFile(raw))).setEffects(size).build()
    val still = EditedMediaItem.Builder(
      MediaItem.Builder().setUri(Uri.fromFile(card)).setMimeType(MimeTypes.IMAGE_PNG).setImageDurationMs(CARD_MS).build(),
    ).setFrameRate(30).setEffects(size).build()
    val items = listOf(clip, still)
    val sequence = if (hasAudio) EditedMediaItemSequence.withAudioAndVideoFrom(items) else EditedMediaItemSequence.withVideoFrom(items)
    val composition = Composition.Builder(sequence).build()
    val done = CountDownLatch(1)
    var failure: Exception? = null
    Handler(thread.looper).post {
      Transformer.Builder(context)
        .setLooper(thread.looper)
        .setVideoMimeType(MimeTypes.VIDEO_H264)
        .setAudioMimeType(MimeTypes.AUDIO_AAC)
        .addListener(object : Transformer.Listener {
          override fun onCompleted(composition: Composition, exportResult: ExportResult) = done.countDown()
          override fun onError(composition: Composition, exportResult: ExportResult, exportException: ExportException) {
            failure = exportException
            done.countDown()
          }
        })
        .build()
        .start(composition, dest.path)
    }
    if (!done.await(120, TimeUnit.SECONDS)) throw IllegalStateException("end card took over 2 minutes")
    failure?.let { throw it }
  }

  /** Copies [file] into the gallery, Movies/Slam; returns its content URI. */
  fun toGallery(context: Context, file: File): Uri {
    val stamp = SimpleDateFormat("yyyyMMdd-HHmmss", Locale.US).format(Date())
    val values = ContentValues().apply {
      put(MediaStore.Video.Media.DISPLAY_NAME, "$NAME-$stamp.mp4")
      put(MediaStore.Video.Media.MIME_TYPE, "video/mp4")
      put(MediaStore.Video.Media.RELATIVE_PATH, "Movies/$NAME")
      put(MediaStore.Video.Media.IS_PENDING, 1)
    }
    val resolver = context.contentResolver
    val uri = checkNotNull(resolver.insert(MediaStore.Video.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY), values))
    resolver.openOutputStream(uri)!!.use { out -> file.inputStream().use { it.copyTo(out) } }
    resolver.update(uri, ContentValues().apply { put(MediaStore.Video.Media.IS_PENDING, 0) }, null, null)
    return uri
  }
}
