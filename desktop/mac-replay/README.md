# Slam Replay for Mac (spike, never merged)

The Mac twin of BHA-351's Android instant replay: a small menu-bar app that keeps the last 30 s of the
Dota 2 window, with the game's own sound, in memory, and writes it to `~/Movies/Slam` on ⌃⇧S.
A throwaway prototype on branch `spike/mac-replay`. It never gets a PR and nothing in it ships.

## How it works

| Step | What | File |
| --- | --- | --- |
| Capture | ScreenCaptureKit, filtered to one app (`SCContentFilter(display:including:[Dota 2])`), cropped to its biggest window, scaled to fit 1600×900, 30 fps, NV12. The app's audio only (`capturesAudio`, 48 kHz stereo). | `Recorder.swift` |
| Encode | VideoToolbox hardware H.264 (`--hevc` for HEVC), 6 Mbps, a keyframe every 2 s, no B-frames. AAC-LC 128 kbps through AVAudioConverter. | `VideoEncoder.swift`, `AudioEncoder.swift` |
| Keep | Whole GOPs in memory, the oldest dropped once the rest still covers 30 s (clips are 30–32 s). Nothing on disk. | `ReplayRing.swift` |
| Save | ⌃⇧S (Carbon hotkey, no Accessibility permission), the menu-bar item, or `kill -USR1 <pid>`. AVAssetWriter passthrough to MP4, no re-encode. Saves queue, and each keeps the 30 s before its own tap. | `ClipWriter.swift`, `HotKey.swift`, `AppController.swift` |
| Measure | Every 5 s a JSON line in `~/Library/Logs/SlamReplay/replay.log`: our CPU (% of one core), kernel energy estimate (mW), memory footprint, capture and encoder fps, ring size, game audio level. | `ProcessStats.swift` |
| Game FPS | `--fps-probe`: a one-off screenshot of the window's top strip every 5 s, read with Vision, so Dota's own FPS counter ends up in the log. `--probe-only` runs only this, for the run without the recorder. | `FPSProbe.swift` |

## Run it

Needs the Command Line Tools (Swift 6), macOS 15 or later. No Xcode.

```bash
./build.sh                                          # build/SlamReplay.app, ad-hoc signed
open build/SlamReplay.app --args --fps-probe        # record Dota 2 (it waits until Dota is running)
open build/SlamReplay.app --args --picker           # pick the window in the system picker instead
open build/SlamReplay.app --args --probe-only       # FPS probe only: the run without the recorder
./measure.sh with-recorder 120                      # CPU/power per process (top) and GPU use (ioreg)
.build/release/SlamReplay --selftest 40             # encoders, ring and writer on synthetic frames
.build/release/SlamReplay --probe ~/Movies/Slam/<clip>.mp4   # what's in a clip, does it decode, how loud
```

Start the app with `open` so macOS asks for the permission in the app's own name (run straight
from a terminal, the terminal would be the one asking). Other flags: `--target <bundle id>`,
`--seconds`, `--fps`, `--bitrate`, `--max-width`, `--max-height`, `--hevc`, `--no-audio`.

The ad-hoc signature changes with every build, and macOS ties the Screen Recording grant to it.
After a rebuild, allow the app again in System Settings.

Findings are in the Linear issue (Mac twin of BHA-351).
