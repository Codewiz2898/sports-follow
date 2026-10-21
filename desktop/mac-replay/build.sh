#!/bin/bash
# Builds build/SlamReplay.app (ad-hoc signed). Run from anywhere.
# Each rebuild changes the ad-hoc signature, and macOS ties the Screen Recording grant to it:
# after a rebuild, expect to allow it again (a Developer ID build wouldn't have this problem).
set -euo pipefail
cd "$(dirname "$0")"
swift build -c release
APP=build/SlamReplay.app
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp .build/release/SlamReplay "$APP/Contents/MacOS/SlamReplay"
cp Support/Info.plist "$APP/Contents/Info.plist"
mkdir -p "$APP/Contents/Resources/Fonts"
cp ../../sports_follow/data/fonts/{Anton-Regular,Manrope,Fredoka}.ttf "$APP/Contents/Resources/Fonts/"
codesign --force --sign - --identifier io.slam.replayspike "$APP"
codesign -dv "$APP" 2>&1 | grep -E "Identifier|CDHash|Signature"
echo "built $APP"
