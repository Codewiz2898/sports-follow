package io.stann.recorder

import android.media.MediaCodec
import android.media.MediaFormat
import android.media.MediaMuxer
import java.io.File
import java.nio.ByteBuffer

/** One encoded frame (video) or AAC packet (audio), with its time on the monotonic clock in µs. */
class Sample(val data: ByteArray, val ptsUs: Long, val flags: Int) {
  val key: Boolean get() = flags and MediaCodec.BUFFER_FLAG_KEY_FRAME != 0
}

/** The last [keepUs] of encoded screen and sound, in memory, starting on a key frame so it plays from its first
 *  frame. Nothing touches the disk until Save: the cost of the buffer is the encoder, not storage. */
class Ring(private val keepUs: Long) {
  private val video = ArrayDeque<Sample>()
  private val audio = ArrayDeque<Sample>()
  @Volatile var videoFormat: MediaFormat? = null
  @Volatile var audioFormat: MediaFormat? = null
  @Volatile var bytes = 0L
    private set

  @Synchronized fun addVideo(s: Sample) {
    video.addLast(s)
    bytes += s.data.size
    val cutoff = s.ptsUs - keepUs
    // Drop whole groups of pictures: keep from the last key frame at or before the cutoff.
    while (true) {
      val next = nextKey() ?: break
      if (video[next].ptsUs > cutoff) break
      repeat(next) { bytes -= video.removeFirst().data.size }
    }
    val first = video.first().ptsUs
    while (audio.isNotEmpty() && audio.first().ptsUs < first) bytes -= audio.removeFirst().data.size
  }

  @Synchronized fun addAudio(s: Sample) {
    audio.addLast(s)
    bytes += s.data.size
    val cutoff = s.ptsUs - keepUs - 2_000_000 // bounded even while the screen sends nothing
    while (audio.isNotEmpty() && audio.first().ptsUs < cutoff) bytes -= audio.removeFirst().data.size
  }

  /** The index of the first key frame after the head, if any. */
  private fun nextKey(): Int? {
    for (i in 1 until video.size) if (video[i].key) return i
    return null
  }

  @Synchronized fun spanUs(): Long = if (video.size < 2) 0 else video.last().ptsUs - video.first().ptsUs

  @Synchronized fun clear() {
    video.clear(); audio.clear(); bytes = 0
  }

  /** What's held right now: taken when the player taps Save, written after (saves queue behind each other). */
  @Synchronized fun snapshot(): Pair<List<Sample>, List<Sample>> = video.toList() to audio.toList()

  /** Writes [held] to an MP4 (no re-encoding: a remux, so it's fast). Returns its length in µs. */
  fun write(held: Pair<List<Sample>, List<Sample>>, file: File): Long {
    val (v, a) = held
    val vf = checkNotNull(videoFormat) { "no video yet" }
    check(v.isNotEmpty()) { "nothing buffered yet" }
    val af = audioFormat
    val muxer = MediaMuxer(file.path, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4)
    try {
      val vt = muxer.addTrack(vf)
      val at = if (af != null && a.isNotEmpty()) muxer.addTrack(af) else -1
      muxer.start()
      val base = v.first().ptsUs
      val end = v.last().ptsUs + 50_000 // sound stops with the picture
      val info = MediaCodec.BufferInfo()
      var i = 0
      var j = 0
      var lastAudio = -1L
      while (i < v.size || (at >= 0 && j < a.size)) {
        val takeVideo = at < 0 || j >= a.size || (i < v.size && v[i].ptsUs <= a[j].ptsUs)
        val s = if (takeVideo) v[i++] else a[j++]
        val pts = s.ptsUs - base
        if (pts < 0) continue
        if (!takeVideo) {
          if (pts <= lastAudio || s.ptsUs > end) continue
          lastAudio = pts
        }
        info.set(0, s.data.size, pts, s.flags)
        muxer.writeSampleData(if (takeVideo) vt else at, ByteBuffer.wrap(s.data), info)
      }
      muxer.stop()
      return v.last().ptsUs - base
    } finally {
      muxer.release()
    }
  }
}
