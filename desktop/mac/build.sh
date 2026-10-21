#!/bin/bash
# Builds build/Slam.app, ad-hoc signed (for Bharath's Mac only, for now: no Developer ID, no notarization).
# Recording goes through macOS's sharing picker, which needs no Screen Recording permission, so a rebuild
# asks for nothing again; --dota (find Dota's window without the picker) does need it.
set -euo pipefail
cd "$(dirname "$0")"
swift build -c release
APP=build/Slam.app
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp .build/release/Slam "$APP/Contents/MacOS/Slam"
cp Support/Info.plist "$APP/Contents/Info.plist"
mkdir -p "$APP/Contents/Resources/Fonts"
cp ../../sports_follow/data/fonts/{Anton-Regular,Manrope,Fredoka}.ttf "$APP/Contents/Resources/Fonts/"
codesign --force --sign - --identifier io.slam.mac "$APP"
codesign -dv "$APP" 2>&1 | grep -E "Identifier|CDHash|Signature"
echo "built $APP"
