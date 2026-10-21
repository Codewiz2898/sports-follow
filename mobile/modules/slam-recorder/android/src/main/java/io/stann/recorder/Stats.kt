package io.stann.recorder

import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import android.os.Debug
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.os.Process
import android.os.SystemClock
import android.util.Log
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** What the buffer costs, every 10 seconds, to logcat (tag SlamReplay) and a CSV the spike reads afterwards
 *  (Android/data/<package>/files/replay-stats-*.csv): battery, heat, our CPU, frames and bytes encoded. */
class Stats(private val context: Context, private val sample: () -> Map<String, Any?>) {
  private val main = Handler(Looper.getMainLooper())
  private val pm = context.getSystemService(PowerManager::class.java)
  private val bm = context.getSystemService(BatteryManager::class.java)
  private val start = SystemClock.elapsedRealtime()
  private var lastWall = start
  private var lastCpu = Process.getElapsedCpuTime()
  private var note: String? = null
  val file: File = File(
    context.getExternalFilesDir(null) ?: context.filesDir,
    "replay-stats-" + SimpleDateFormat("yyyyMMdd-HHmmss", Locale.US).format(Date()) + ".csv",
  )
  private var header = false

  private val tick = object : Runnable {
    override fun run() {
      runCatching { line() }.onFailure { Log.w(TAG, "stats", it) }
      main.postDelayed(this, 10_000)
    }
  }

  fun start() = apply { main.postDelayed(tick, 10_000) }
  fun stop() {
    main.removeCallbacks(tick)
    runCatching { line() }
  }

  /** Something that happened (a save and how long it took), on the next line. */
  fun mark(text: String) { note = text }

  private fun line() {
    val now = SystemClock.elapsedRealtime()
    val cpu = Process.getElapsedCpuTime()
    val battery = context.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
    val level = battery?.let { 100.0 * it.getIntExtra(BatteryManager.EXTRA_LEVEL, 0) / it.getIntExtra(BatteryManager.EXTRA_SCALE, 100) }
    val row = linkedMapOf<String, Any?>(
      "t_s" to (now - start) / 1000,
      "clock" to SimpleDateFormat("HH:mm:ss", Locale.US).format(Date()),
      "battery_pct" to level,
      "plugged" to (battery?.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) ?: 0),
      "batt_temp_c" to battery?.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, 0)?.div(10.0),
      "current_ma" to bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CURRENT_NOW) / 1000,
      "charge_mah" to bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CHARGE_COUNTER) / 1000,
      "thermal" to pm.currentThermalStatus,
      "headroom" to "%.2f".format(pm.getThermalHeadroom(10)),
      "cpu_pct_core" to "%.1f".format(100.0 * (cpu - lastCpu) / (now - lastWall).coerceAtLeast(1)),
      "heap_mb" to (Runtime.getRuntime().let { it.totalMemory() - it.freeMemory() } + Debug.getNativeHeapAllocatedSize()) / 1_048_576,
    )
    row.putAll(sample())
    row["note"] = note
    note = null
    lastWall = now
    lastCpu = cpu
    if (!header) {
      file.appendText(row.keys.joinToString(",") + "\n")
      header = true
    }
    val csv = row.values.joinToString(",") { it?.toString()?.replace(",", ";") ?: "" }
    file.appendText(csv + "\n")
    Log.i(TAG, "stats " + row.entries.joinToString(" ") { "${it.key}=${it.value}" })
  }
}
