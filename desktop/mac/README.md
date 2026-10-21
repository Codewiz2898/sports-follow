# Slam for Mac

A menu-bar app that keeps the last 30 s of a game's window in memory, with the game's own sound, and turns a
moment into a shareable clip on ⌃⇧S. The clip opens in a **Clip ready** panel to publish to Slam, save or share.
For Bharath's Mac only for now: an ad-hoc build, with no Developer ID and no notarization (BHA-366, under BHA-363).
It grew from the spike in BHA-357 (branch `spike/mac-replay`).

## The flow

1. **Pick.** macOS's own sharing picker opens, and you choose the game's window (Dota 2) or a browser window.
   No Screen Recording permission is needed. The ● Slam menu has **Choose window to clip…** to switch.
2. **Record.** The app keeps the last 30–32 s: hardware H.264 at 60 fps (up to 1600×900) and AAC, nothing on disk.
3. **⌃⇧S** (or the menu) saves the clip in `~/Movies/Slam` and makes **the cut**:
   - a title card over the first 2 s;
   - the scorebug;
   - the REPLAY slate and the last 5 s at half speed under cinema bars;
   - the animated end card (blue stamp or ink + Anton) with its sound.

   The numbers are Slam's live data for the match (`stann.in/api/match/…`, set under **Stat strip match…**, else
   the live Dota game), or else Dota's own top bar read off the frame.
4. **Clip ready.** The panel plays the cut. It shows its moment, the match (changeable) and a caption, with
   **Share…**, **Show in Finder** and **Publish to Slam**. It opens by itself unless the game is in front; **Recent
   clips** in the menu always has it.
5. **Publish.** The app makes a 720p copy and a poster frame, asks Slam for upload links, and puts both straight
   into Slam's bucket. Slam then puts the clip live in its match's, its competition's and its players' feeds
   (server: `sports_follow/clips.py`). Clips recorded from a browser window show a broadcast, which is its
   organiser's, so they're saved and shared but not published (BHA-369).

**Make montage of today's clips** joins the last 8 s of each of today's clips, titled, with the end card, in at most
60 s (and a 9:16 version). It opens in the same panel.

## Publishing key

Publishing needs this Mac's key, made once on the server by an admin:

```bash
.venv/bin/python -m sports_follow.clips key "Bharath's Mac"
```

Paste it into **Publishing settings…** in the menu, which keeps it in the login Keychain and checks it against
the server. On the server, `python -m sports_follow.clips keys` lists keys and `revoke <id>` stops one.

## Build, test, run

Needs the Command Line Tools (Swift 6) and macOS 15.2 or later. No Xcode.

```bash
./build.sh                                   # build/Slam.app, ad-hoc signed, Slam's fonts inside
swift test                                   # moments, match links, sources, what's published, a real cut
open build/Slam.app                          # the picker opens: pick the game's window
cp -R build/Slam.app ~/Applications/         # to start it from Spotlight
```

Developer tools, run from `.build/release/Slam` (no window, no permission):

| Flag | What |
| --- | --- |
| `--selftest [s]` | synthetic frames and a tone through the encoders, ring, writer and the cut |
| `--from-frames <dir> [--sample]` | a Dota `startmovie` export (jpg + wav) through it all |
| `--panel <cut.mp4> [--browser] [--shot out.png]` | the Clip ready panel for a file; `--shot` draws it to a picture |
| `--montage [--vertical] [clips…]` | a montage of today's clips, or of these |
| `--strip <stann.in link \| live>` | the moment a save would read from Slam's API |
| `--probe`, `--still`, `--ocr` | what's in a clip, one frame of it, what the top-bar reader sees |

Logs go to `~/Library/Logs/Slam/slam.log`, one JSON line every 5 s while recording: our CPU, memory, capture
and encoder fps, and the game's sound level.

## Files

| | |
| --- | --- |
| `Recorder.swift`, `VideoEncoder.swift`, `AudioEncoder.swift`, `ReplayRing.swift`, `ClipWriter.swift` | capture, encode, the 30 s ring, the passthrough save |
| `Moment.swift`, `MatchFeed.swift`, `TopBar.swift` | what was going on at the tap |
| `ClipEditor.swift`, `Overlays.swift`, `Sting.swift`, `Fonts.swift`, `Montage.swift` | the cut, its overlays and end card, its sound, the montage |
| `ClipPanel.swift`, `Publisher.swift`, `Source.swift` | Clip ready, publishing, the rights rule and settings |
| `AppController.swift`, `HotKey.swift`, `main.swift`, `Tools.swift` | the menu-bar app, ⌃⇧S, the developer tools |
