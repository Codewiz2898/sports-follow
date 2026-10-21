package io.stann.recorder

import android.annotation.SuppressLint
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioPlaybackCaptureConfiguration
import android.media.AudioRecord
import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaFormat
import android.media.MediaRecorder
import android.media.projection.MediaProjection
import android.util.Log
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.log10
import kotlin.math.max
import kotlin.math.sqrt

/** The clip's sound: the game's own (AudioPlaybackCapture, which only hears apps that allow it), the mic, or
 *  both mixed, encoded to AAC into the ring. It measures each source's loudness, which is how the spike tells
 *  whether BGMI lets itself be heard (a capture it refuses reads as digital silence). */
class AudioCapture(private val projection: MediaProjection, private val mode: String, private val ring: Ring) {
  private val rate = 48_000
  private val frames = 1024 // per AAC packet
  @Volatile private var running = false
  private var thread: Thread? = null
  private var game: AudioRecord? = null
  private var mic: AudioRecord? = null

  // Loudest chunk since the last stats line, in dBFS (-120 = digital silence).
  @Volatile var gameDb = -120.0
  @Volatile var micDb = -120.0
  @Volatile var error: String? = null

  val micSilenced: Boolean? get() = mic?.activeRecordingConfiguration?.isClientSilenced

  fun takeLevels(): Pair<Double, Double> {
    val l = gameDb to micDb
    gameDb = -120.0; micDb = -120.0
    return l
  }

  @SuppressLint("MissingPermission") // the app asks for RECORD_AUDIO before it starts the service
  fun start() {
    try {
      if (mode == "game" || mode == "both") {
        val config = AudioPlaybackCaptureConfiguration.Builder(projection)
          .addMatchingUsage(AudioAttributes.USAGE_GAME)
          .addMatchingUsage(AudioAttributes.USAGE_MEDIA)
          .addMatchingUsage(AudioAttributes.USAGE_UNKNOWN)
          .build()
        game = AudioRecord.Builder()
          .setAudioFormat(format(AudioFormat.CHANNEL_IN_STEREO))
          .setBufferSizeInBytes(bufferSize(AudioFormat.CHANNEL_IN_STEREO))
          .setAudioPlaybackCaptureConfig(config)
          .build()
      }
      if (mode == "mic" || mode == "both") {
        mic = AudioRecord.Builder()
          .setAudioSource(MediaRecorder.AudioSource.MIC)
          .setAudioFormat(format(AudioFormat.CHANNEL_IN_MONO))
          .setBufferSizeInBytes(bufferSize(AudioFormat.CHANNEL_IN_MONO))
          .build()
      }
    } catch (e: Exception) {
      error = "audio: ${e.message}"
      Log.w(TAG, "audio didn't start", e)
      return
    }
    running = true
    thread = Thread({ loop() }, "slam-audio").apply { start() }
  }

  fun stop() {
    running = false
    thread?.join(1000)
  }

  private fun format(channels: Int) =
    AudioFormat.Builder().setEncoding(AudioFormat.ENCODING_PCM_16BIT).setSampleRate(rate).setChannelMask(channels).build()

  private fun bufferSize(channels: Int) = max(AudioRecord.getMinBufferSize(rate, channels, AudioFormat.ENCODING_PCM_16BIT), frames * 4) * 4

  private fun loop() {
    val encoder = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_AUDIO_AAC)
    val f = MediaFormat.createAudioFormat(MediaFormat.MIMETYPE_AUDIO_AAC, rate, 2).apply {
      setInteger(MediaFormat.KEY_AAC_PROFILE, MediaCodecInfo.CodecProfileLevel.AACObjectLC)
      setInteger(MediaFormat.KEY_BIT_RATE, 128_000)
      setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, frames * 4 * 2)
    }
    encoder.configure(f, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
    encoder.start()
    game?.startRecording()
    mic?.startRecording()
    val g = ShortArray(frames * 2)
    val m = ShortArray(frames)
    val out = ShortArray(frames * 2)
    val bytes = ByteBuffer.allocate(frames * 4).order(ByteOrder.LITTLE_ENDIAN)
    val info = MediaCodec.BufferInfo()
    var lastPts = 0L
    try {
      while (running) {
        val gn = game?.read(g, 0, g.size, AudioRecord.READ_BLOCKING) ?: 0
        val mn = mic?.read(m, 0, m.size, AudioRecord.READ_BLOCKING) ?: 0
        val n = if (game != null) gn / 2 else mn // stereo frames this round
        if (n <= 0) continue
        if (game != null) gameDb = max(gameDb, db(g, gn))
        if (mic != null) micDb = max(micDb, db(m, mn))
        for (k in 0 until n) {
          val gl = if (game != null) g[2 * k].toInt() else 0
          val gr = if (game != null) g[2 * k + 1].toInt() else 0
          val mv = if (mic != null && k < mn) m[k].toInt() else 0
          out[2 * k] = clamp(gl + mv)
          out[2 * k + 1] = clamp(gr + mv)
        }
        // The clock, not the sample count: a capture with nothing playing may stall, and the sound must stay
        // under its picture.
        var pts = System.nanoTime() / 1000 - n * 1_000_000L / rate
        if (pts <= lastPts) pts = lastPts + 1
        lastPts = pts
        bytes.clear()
        bytes.asShortBuffer().put(out, 0, n * 2)
        val inIndex = encoder.dequeueInputBuffer(20_000)
        if (inIndex >= 0) {
          val buf = encoder.getInputBuffer(inIndex)!!
          buf.clear()
          buf.put(bytes.array(), 0, n * 4)
          encoder.queueInputBuffer(inIndex, 0, n * 4, pts, 0)
        }
        drain(encoder, info)
      }
    } catch (e: Exception) {
      error = "audio: ${e.message}"
      Log.w(TAG, "audio stopped", e)
    } finally {
      runCatching { game?.stop(); game?.release() }
      runCatching { mic?.stop(); mic?.release() }
      runCatching { encoder.stop(); encoder.release() }
    }
  }

  private fun drain(encoder: MediaCodec, info: MediaCodec.BufferInfo) {
    while (true) {
      val i = encoder.dequeueOutputBuffer(info, 0)
      when {
        i == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED -> ring.audioFormat = encoder.outputFormat
        i < 0 -> return
        else -> {
          if (info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG == 0 && info.size > 0) {
            val buf = encoder.getOutputBuffer(i)!!
            buf.position(info.offset); buf.limit(info.offset + info.size)
            val data = ByteArray(info.size).also { buf.get(it) }
            ring.addAudio(Sample(data, info.presentationTimeUs, info.flags))
          }
          encoder.releaseOutputBuffer(i, false)
        }
      }
    }
  }

  private fun clamp(v: Int): Short = v.coerceIn(Short.MIN_VALUE.toInt(), Short.MAX_VALUE.toInt()).toShort()

  private fun db(s: ShortArray, n: Int): Double {
    if (n <= 0) return -120.0
    var sum = 0.0
    for (k in 0 until n) sum += s[k].toDouble() * s[k]
    val rms = sqrt(sum / n)
    return if (rms < 1e-9) -120.0 else 20 * log10(rms / 32768.0)
  }
}
