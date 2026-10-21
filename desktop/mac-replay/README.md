# Slam Replay for Mac (spike, never merged)

The Mac twin of BHA-351's Android instant replay: a small menu-bar app that keeps the last 30 s of the
Dota 2 window, with the game's own sound, in memory, and writes it to `~/Movies/Slam` on ⌃⇧S.
A throwaway prototype on branch `spike/mac-replay`. It never gets a PR and nothing in it ships.

## How it works

| Step | What | File |
| --- | --- | --- |
| Pick | macOS's own sharing picker (no Screen Recording permission needed); `--dota` finds the Dota 2 window itself instead, which needs the permission | `AppController.swift`, `Recorder.swift` |
| Capture | ScreenCaptureKit: the picked window and its app's sound, scaled to fit 1600×900, 60 fps, NV12 | `Recorder.swift` |
| Encode | VideoToolbox hardware H.264, 10 Mbps, a keyframe forced every 2 s, no B-frames; AAC-LC 128 kbps | `VideoEncoder.swift`, `AudioEncoder.swift` |
| Keep | Whole GOPs in memory, 30–32 s; nothing on disk | `ReplayRing.swift` |
| Save | ⌃⇧S (Carbon hotkey), the menu, or `kill -USR1 <pid>`: passthrough MP4 in ~/Movies/Slam, plus a `.json` sidecar with the moment | `ClipWriter.swift`, `AppController.swift` |
| Moment | Slam's match API at the tap (`stann.in/api/match/…`), else Dota's top bar read off the frame (kills + clock, Vision on a 3× crop) | `MatchFeed.swift`, `Moment.swift`, `FPSProbe.swift` |
| Replay cut | The picked overlays (canvas, 21 Oct): E1 title card over the first 2 s, B1 scorebug, E2 REPLAY slate, the last 5 s at half speed under C2 cinema bars, "clipped on \| slam" end card | `ClipEditor.swift`, `Overlays.swift` |
| Montage | Today's clips (or picked ones): last 8 s each, scorebug-style titles, end card, ≤ 60 s; also 9:16 | `Montage.swift` |
| Measure | A JSON line every 5 s in `~/Library/Logs/SlamReplay/replay.log`: our CPU, energy, memory, capture/encoder fps, ring, audio level | `ProcessStats.swift` |

Burned-in type is Slam's own (Anton, Manrope, Fredoka from `sports_follow/data/fonts`, copied into the app).

## Run it

Needs the Command Line Tools (Swift 6), macOS 15.2 or later. No Xcode.

```bash
./build.sh                                               # build/SlamReplay.app, ad-hoc signed, fonts inside
open build/SlamReplay.app                                # the picker opens: pick the game (or browser) window
open build/SlamReplay.app --args --match https://stann.in/match/pandascore_esports/1753729
./measure.sh with-recorder 60                            # CPU/power per process (top) and GPU use (ioreg)
.build/release/SlamReplay --selftest 40                  # synthetic frames through encoders, ring, writer, cut
.build/release/SlamReplay --from-frames ~/dota_clips     # a Dota `startmovie` export (jpg + wav) through it all
.build/release/SlamReplay --montage [--vertical] [clips…]
.build/release/SlamReplay --strip <stann.in match link | live>   # the moment a save would read
.build/release/SlamReplay --probe <clip.mp4>             # what's in a clip, does it decode, how loud
.build/release/SlamReplay --still <clip.mp4> <s> <out.png>
.build/release/SlamReplay --ocr <clip.mp4> <s>           # what the top-bar reader sees
```

Other flags: `--seconds`, `--fps`, `--bitrate`, `--max-width`, `--max-height`, `--hevc`, `--no-audio`, `--no-edit`.

Findings: Linear BHA-357 (Mac twin of BHA-351).
