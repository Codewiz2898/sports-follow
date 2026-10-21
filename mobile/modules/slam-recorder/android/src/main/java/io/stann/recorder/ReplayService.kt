package io.stann.recorder

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.ClipData
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaCodecList
import android.media.MediaFormat
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.net.Uri
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import android.view.WindowManager
import androidx.media3.common.util.UnstableApi
import java.io.File
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicLong
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

/** The instant replay: a foreground service of type mediaProjection that mirrors the screen into a hardware
 *  H.264 encoder and keeps the last N seconds in memory (Ring), with the game's sound or the mic (AudioCapture).
 *  Its notification's "Save last 30s", or the floating button (Bubble), writes them out, joins the end card and
 *  puts the clip in the gallery. SPIKE: never in a build that ships. */
@UnstableApi
class ReplayService : Service() {
  companion object {
    const val START = "io.stann.recorder.START"
    const val SAVE = "io.stann.recorder.SAVE"
    const val STOP = "io.stann.recorder.STOP"
    const val BUBBLE = "io.stann.recorder.BUBBLE"
    private const val CHANNEL = "slam_replay"
    private const val CHANNEL_SAVED = "slam_replay_saved"
    private const val ID = 7301

    @Volatile var current: ReplayService? = null
    @Volatile var onChange: ((Map<String, Any?>) -> Unit)? = null

    fun status(): Map<String, Any?> = current?.state() ?: mapOf("running" to false)
  }

  private val main = Handler(Looper.getMainLooper())
  private val videoThread = HandlerThread("slam-video").apply { start() }
  private val video = Handler(videoThread.looper)
  private val saver = Executors.newSingleThreadExecutor()
  private var projection: MediaProjection? = null
  private var display: VirtualDisplay? = null
  private var encoder: MediaCodec? = null
  private var audio: AudioCapture? = null
  private var ring = Ring(30_000_000)
  private var keepS = 30
  private var bubble: Bubble? = null
  private var stats: Stats? = null
  private var w = 0
  private var h = 0
  private var fps = 30
  private var mbps = 5.0
  private var audioMode = "none"
  private var cardNow = true
  private val frames = AtomicLong()
  private val encoded = AtomicLong()
  private var lastFrames = 0L
  private var lastBytes = 0L
  private var lastTick = SystemClock.elapsedRealtime()
  private var startedAt = 0L
  @Volatile private var saving = false
  private val queued = java.util.concurrent.atomic.AtomicInteger()
  @Volatile private var saves = 0
  @Volatile private var lastSave: Map<String, Any?>? = null
  @Volatile private var visible: Boolean? = null
  @Volatile private var contentSize: String? = null
  @Volatile private var error: String? = null

  override fun onBind(intent: Intent?): IBinder? = null

  override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
    when (intent?.action) {
      START -> runCatching { start(intent) }.onFailure {
        Log.e(TAG, "start", it)
        error = "start: ${it.message}"
        stopAll()
      }
      SAVE -> save()
      STOP -> stopAll()
      BUBBLE -> if (intent.getBooleanExtra("on", false)) bubble().show(intent.getBooleanExtra("secure", false)) else bubble?.hide()
    }
    changed()
    return START_NOT_STICKY
  }

  private fun bubble() = bubble ?: Bubble(this) { save() }.also { bubble = it }

  private fun start(intent: Intent) {
    if (projection != null) return
    keepS = intent.getIntExtra("seconds", 30)
    ring = Ring(keepS * 1_000_000L)
    fps = intent.getIntExtra("fps", 30)
    mbps = intent.getDoubleExtra("mbps", 5.0)
    cardNow = intent.getBooleanExtra("cardNow", true)
    val asked = intent.getStringExtra("audio") ?: "game"
    val micOk = checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
    audioMode = if (micOk) asked else "none"
    channels()
    var type = ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION
    if (audioMode != "none") type = type or ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
    // After the consent and before getMediaProjection: Android 14 throws otherwise.
    startForeground(ID, ongoing("Replay is on", "$NAME is keeping the last $keepS seconds. Tap Save to keep them."), type)
    val data = checkNotNull(intent.getParcelableExtra("data", Intent::class.java)) { "no consent" }
    val mpm = getSystemService(MediaProjectionManager::class.java)
    val p = checkNotNull(mpm.getMediaProjection(intent.getIntExtra("code", 0), data)) { "no projection" }
    p.registerCallback(object : MediaProjection.Callback() {
      override fun onStop() { main.post { stopAll() } }
      override fun onCapturedContentResize(width: Int, height: Int) { contentSize = "${width}x$height" }
      override fun onCapturedContentVisibilityChanged(isVisible: Boolean) { visible = isVisible }
    }, video)
    projection = p
    current = this
    startedAt = SystemClock.elapsedRealtime()
    startVideo(p, intent.getIntExtra("height", 720))
    if (audioMode != "none") audio = AudioCapture(p, audioMode, ring).also { it.start() }
    stats = Stats(this) { tick() }.start()
    if (intent.getBooleanExtra("bubble", false)) bubble().show(intent.getBooleanExtra("secure", false))
    Log.i(TAG, "started ${w}x$h@$fps ${mbps}Mbps audio=$audioMode keep=${keepS}s")
  }

  /** The screen, sideways (games are played in landscape), scaled to [height] lines, into the encoder. */
  private fun startVideo(p: MediaProjection, height: Int) {
    val bounds = getSystemService(WindowManager::class.java).maximumWindowMetrics.bounds
    val long = max(bounds.width(), bounds.height())
    val short = min(bounds.width(), bounds.height())
    val (ew, eh) = encodable(even(long.toDouble() * height / short), even(height.toDouble()))
    w = ew; h = eh
    val dpi = (resources.displayMetrics.densityDpi.toDouble() * h / short).roundToInt()
    val format = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, w, h).apply {
      setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface)
      setInteger(MediaFormat.KEY_BIT_RATE, (mbps * 1_000_000).toInt())
      setInteger(MediaFormat.KEY_FRAME_RATE, fps)
      setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 1)
      // The game may run at 60 or 90: the encoder takes only [fps] of them.
      setFloat(MediaFormat.KEY_MAX_FPS_TO_ENCODER, fps.toFloat())
      // A still screen sends no frames, so no key frames, and the ring can't cut at 30 s: repeat the last one.
      setLong(MediaFormat.KEY_REPEAT_PREVIOUS_FRAME_AFTER, 250_000)
    }
    val codec = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_VIDEO_AVC)
    codec.setCallback(object : MediaCodec.Callback() {
      override fun onInputBufferAvailable(codec: MediaCodec, index: Int) {}
      override fun onOutputBufferAvailable(codec: MediaCodec, index: Int, info: MediaCodec.BufferInfo) {
        try {
          if (info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG == 0 && info.size > 0) {
            val buf = codec.getOutputBuffer(index)!!
            buf.position(info.offset); buf.limit(info.offset + info.size)
            val bytes = ByteArray(info.size).also { buf.get(it) }
            ring.addVideo(Sample(bytes, info.presentationTimeUs, info.flags))
            frames.incrementAndGet()
            encoded.addAndGet(info.size.toLong())
          }
          codec.releaseOutputBuffer(index, false)
        } catch (_: IllegalStateException) { /* stopping */ }
      }
      override fun onError(codec: MediaCodec, e: MediaCodec.CodecException) { error = "encoder: ${e.message}"; Log.e(TAG, "encoder", e) }
      override fun onOutputFormatChanged(codec: MediaCodec, format: MediaFormat) { ring.videoFormat = format }
    }, video)
    codec.configure(format, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
    val surface = codec.createInputSurface()
    codec.start()
    encoder = codec
    display = p.createVirtualDisplay("slam-replay", w, h, dpi, DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR, surface, null, video)
  }

  private fun even(v: Double) = (v / 2).roundToInt() * 2

  /** The size, or the nearest the phone's H.264 encoder takes (multiples of 16). */
  private fun encodable(w: Int, h: Int): Pair<Int, Int> {
    val caps = MediaCodecList(MediaCodecList.REGULAR_CODECS).codecInfos
      .firstOrNull { it.isEncoder && it.supportedTypes.contains(MediaFormat.MIMETYPE_VIDEO_AVC) }
      ?.getCapabilitiesForType(MediaFormat.MIMETYPE_VIDEO_AVC)?.videoCapabilities
    if (caps == null || caps.isSizeSupported(w, h)) return w to h
    return (w / 16 * 16) to (h / 16 * 16)
  }

  private fun save() {
    if (projection == null) return
    val held = ring.snapshot() // the 30 s before this tap, even if another save is still being written
    queued.incrementAndGet()
    saving = true
    bubble?.flash("SAVING")
    val tapped = SystemClock.elapsedRealtime()
    saver.execute {
      val result = linkedMapOf<String, Any?>()
      try {
        val dir = File(cacheDir, "clips").apply { mkdirs() }
        val stamp = System.currentTimeMillis()
        val raw = File(dir, "raw-$stamp.mp4")
        val durUs = ring.write(held, raw)
        val muxMs = SystemClock.elapsedRealtime() - tapped
        var final = raw
        var cardMs = 0L
        if (cardNow) {
          val t = SystemClock.elapsedRealtime()
          val card = File(dir, "card-${w}x$h.png")
          if (!card.exists()) EndCard.draw(this, w, h, card)
          val joined = File(dir, "joined-$stamp.mp4")
          try {
            EndCard.join(this, raw, card, w, h, ring.audioFormat != null, joined)
            final = joined
          } catch (e: Exception) {
            Log.w(TAG, "end card", e)
            result["cardError"] = e.message
          }
          cardMs = SystemClock.elapsedRealtime() - t
        }
        val size = final.length()
        val uri = EndCard.toGallery(this, final)
        dir.listFiles()?.filter { it.name.endsWith(".mp4") }?.forEach { it.delete() }
        saves++
        result.putAll(mapOf(
          "uri" to uri.toString(), "seconds" to durUs / 1_000_000.0, "muxMs" to muxMs, "cardMs" to cardMs,
          "mb" to size / 1_048_576.0, "endCard" to (cardNow && result["cardError"] == null), "audio" to audioMode,
          "size" to "${w}x$h", "savedAt" to System.currentTimeMillis(),
        ))
        Clips.add(this, result)
        lastSave = result
        stats?.mark("saved mux=${muxMs}ms card=${cardMs}ms ${"%.1f".format(size / 1_048_576.0)}MB")
        Log.i(TAG, "saved $result")
        bubble?.flash("SAVED")
        notifySaved(uri, durUs / 1_000_000.0)
      } catch (e: Exception) {
        Log.e(TAG, "save", e)
        result["error"] = e.message
        lastSave = result
        bubble?.flash("ERROR")
      } finally {
        saving = queued.decrementAndGet() > 0
        changed()
      }
    }
  }

  private fun tick(): Map<String, Any?> {
    val now = SystemClock.elapsedRealtime()
    val f = frames.get(); val b = encoded.get()
    val secs = (now - lastTick).coerceAtLeast(1) / 1000.0
    val (gameDb, micDb) = audio?.takeLevels() ?: (null to null)
    val row = linkedMapOf<String, Any?>(
      "enc_fps" to "%.1f".format((f - lastFrames) / secs),
      "enc_kbps" to ((b - lastBytes) * 8 / 1000 / secs).roundToInt(),
      "ring_s" to "%.1f".format(ring.spanUs() / 1e6),
      "ring_mb" to "%.1f".format(ring.bytes / 1_048_576.0),
      "game_db" to gameDb?.let { "%.0f".format(it) },
      "mic_db" to micDb?.let { "%.0f".format(it) },
      "mic_silenced" to audio?.micSilenced,
      "visible" to visible,
      "content" to contentSize,
      "bubble" to bubble?.showing,
      "saves" to saves,
    )
    lastFrames = f; lastBytes = b; lastTick = now
    return row
  }

  private fun state(): Map<String, Any?> = mapOf(
    "running" to (projection != null),
    "seconds" to ring.spanUs() / 1e6,
    "keep" to keepS,
    "size" to "${w}x$h",
    "fps" to fps,
    "mbps" to mbps,
    "audio" to audioMode,
    "frames" to frames.get(),
    "upS" to (SystemClock.elapsedRealtime() - startedAt) / 1000,
    "saving" to saving,
    "saves" to saves,
    "lastSave" to lastSave,
    "bubble" to (bubble?.showing == true),
    "visible" to visible,
    "audioError" to audio?.error,
    "error" to error,
    "stats" to stats?.file?.path,
  )

  private fun changed() {
    val s = state()
    main.post { onChange?.invoke(s) }
  }

  private fun stopAll() {
    stats?.stop(); stats = null
    audio?.stop(); audio = null
    runCatching { display?.release() }; display = null
    runCatching { encoder?.stop(); encoder?.release() }; encoder = null
    val p = projection
    projection = null
    runCatching { p?.stop() }
    bubble?.hide()
    ring.clear()
    if (current === this) current = null
    stopForeground(STOP_FOREGROUND_REMOVE)
    stopSelf()
    changed()
  }

  override fun onDestroy() {
    if (projection != null) stopAll()
    videoThread.quitSafely()
    super.onDestroy()
  }

  // Notifications.

  private fun channels() {
    val nm = getSystemService(NotificationManager::class.java)
    nm.createNotificationChannel(NotificationChannel(CHANNEL, "Instant replay", NotificationManager.IMPORTANCE_LOW))
    nm.createNotificationChannel(NotificationChannel(CHANNEL_SAVED, "Saved clips", NotificationManager.IMPORTANCE_LOW))
  }

  private fun icon(): Int =
    resources.getIdentifier("notification_icon", "drawable", packageName).takeIf { it != 0 } ?: applicationInfo.icon

  private fun action(action: String, req: Int) =
    PendingIntent.getService(this, req, Intent(this, ReplayService::class.java).setAction(action), PendingIntent.FLAG_IMMUTABLE)

  private fun ongoing(title: String, text: String): Notification {
    val open = packageManager.getLaunchIntentForPackage(packageName)
      ?.let { PendingIntent.getActivity(this, 0, it, PendingIntent.FLAG_IMMUTABLE) }
    return Notification.Builder(this, CHANNEL)
      .setSmallIcon(icon())
      .setColor(0xFF1F5BEF.toInt())
      .setContentTitle(title)
      .setContentText(text)
      .setOngoing(true)
      .setContentIntent(open)
      .addAction(Notification.Action.Builder(null, "Save last ${keepS}s", action(SAVE, 1)).build())
      .addAction(Notification.Action.Builder(null, "Stop", action(STOP, 2)).build())
      .setForegroundServiceBehavior(Notification.FOREGROUND_SERVICE_IMMEDIATE)
      .build()
  }

  private fun notifySaved(uri: Uri, seconds: Double) {
    val nm = getSystemService(NotificationManager::class.java)
    val send = Intent(Intent.ACTION_SEND).setType("video/mp4").putExtra(Intent.EXTRA_STREAM, uri)
      .putExtra(Intent.EXTRA_TEXT, "Clipped on $NAME")
      .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    send.clipData = ClipData.newRawUri("clip", uri)
    val chooser = Intent.createChooser(send, "Share your clip").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    val share = PendingIntent.getActivity(this, 10 + saves, chooser, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
    val n = Notification.Builder(this, CHANNEL_SAVED)
      .setSmallIcon(icon())
      .setColor(0xFF1F5BEF.toInt())
      .setContentTitle("Clip saved · %.0f s".format(seconds))
      .setContentText("In your gallery, Movies/$NAME. Share it from here.")
      .setContentIntent(share)
      .setAutoCancel(true)
      .addAction(Notification.Action.Builder(null, "Share", share).build())
      .build()
    nm.notify(ID + 1, n)
    nm.notify(ID, ongoing("Replay is on · $saves saved", "$NAME is keeping the last $keepS seconds. Tap Save to keep them."))
  }
}

/** The clips this phone saved (the spike's list; the gallery holds the files). */
object Clips {
  private const val PREFS = "slam_recorder"

  fun add(context: Context, clip: Map<String, Any?>) {
    val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
    val list = org.json.JSONArray(prefs.getString("clips", "[]"))
    list.put(org.json.JSONObject(clip))
    prefs.edit().putString("clips", list.toString()).apply()
  }

  fun list(context: Context): String = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString("clips", "[]")!!
}
