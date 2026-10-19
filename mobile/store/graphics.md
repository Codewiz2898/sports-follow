# Graphics

What Play's store listing needs, by its rules on 19 Oct 2026
([Google's page](https://support.google.com/googleplay/android-developer/answer/9866151)), and where each
comes from. `make_graphics.py` draws the first two from the app's own assets and fits the screenshots.

| Asset | Play's rule | File | Made by |
|---|---|---|---|
| App icon | 512 × 512, 32-bit PNG, under 1 MB. Play rounds the corners itself, so no mask of our own | `icon-512.png` | `make_graphics.py`: the badge on a full paper square, the same picture as the web app's `maskable-512.png` (`web/scripts/make_icons.py`'s geometry) |
| Feature graphic | 1024 × 500, JPEG or 24-bit PNG with no alpha. Shown above the listing and in promotions | `feature-1024x500.png` | `make_graphics.py`: the badge and the name as the header pairs them, the site's one-line pitch in the leaning display type, one plain line |
| Phone screenshots | At least 2, up to 8. JPEG or 24-bit PNG without alpha; each side 320 to 3,840 px; the long side no more than twice the short. 1080 × 1920 (9:16) or more gets the listing into Play's larger promotion spots, which asks at least 4 | `screenshots/NN-name.png` | you, on the emulator (below), then `make_graphics.py --screenshots DIR` |
| Tablet screenshots (7" and 10") | Optional. Only if the app is offered on tablets | none | skip; the app is portrait, phone first |
| Promo video | Optional, a YouTube link | none | skip for now |

```bash
python mobile/store/make_graphics.py                       # icon and feature graphic
python mobile/store/make_graphics.py --screenshots ~/Desktop/stann-shots
```

(From the repo root with its venv; Pillow is in `requirements.txt`. The fonts come from
`mobile/node_modules`, so `npm install` in `mobile/` first.)

## The screenshots to take

An emulator's 1080 × 2400 capture is 20:9, longer than Play's 2:1 limit, so the script cuts it to 1080 × 2160
keeping the top. Take them on `stann_pixel` (Android 16, 1080 × 2400) against production (`stann.in` is the
app's default API), signed out, with a fan who already follows a few players so every page has something
to show: Kohli, Gukesh, Salah and one esports player cover four sports' colours.

```bash
export JAVA_HOME=~/.local/jdk-17/Contents/Home ANDROID_HOME=~/.local/android-sdk
~/.local/android-sdk/emulator/emulator -avd stann_pixel &
cd mobile && npm run android                               # a debug build; the pages are the same
~/.local/android-sdk/platform-tools/adb exec-out screencap -p > ~/Desktop/stann-shots/1-today.png
```

Take them in this order, on a day with a live game if possible (a live board is the best first picture):

1. **Today**, with a live game of a followed player on top, then the results rail (`1-today.png`).
2. **A player's page** with the live board or last result and their line (`2-player.png`).
3. **An alert**: pull down the notification shade with a stann moment card opened (Alerts' Settings has
   "Send a test alert"; a real one looks better) (`3-alert.png`).
4. **The lock screen with a live card**, from a followed game that's on (`4-lockscreen.png`).
5. **Search** with results across sports for a name like "Rohit" (`5-search.png`).
6. **Following**, the fan's players (`6-following.png`).

Six at 9:16 gets the larger promotion spots too. No device frames, no text overlays for the first upload;
Play forbids other companies' logos and device imagery in them, and a clean capture of the real pages is
the project's look. Pictures of players come from the photos the app already shows with their credits
(`/credits`), so a screenshot with one is fine.

Set the emulator's clock to a plausible time first (it shows in the status bar), and clear notifications
from other apps.

## Checking what the script made

`make_graphics.py` prints each file's size and mode: the icon and feature graphic must say `RGB` (no alpha),
and a screenshot `1080x2160 RGB`. Open them once before uploading; Play shows the icon at 48 px in lists, so
the badge has to read small.
