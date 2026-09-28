#!/bin/bash
# Builds Roughcut.app and Roughcut-<version>.dmg with the Command Line Tools alone (no Xcode): the Swift executable,
# the engine's scripts and settings, the icon, and Info.plist carrying where the app downloads the engine's tools at
# first launch and their SHA-256 (app/runtime/versions.sh).
#
# Usage: app/build_app.sh
#   SIGN_IDENTITY=   "Developer ID Application: Name (TEAMID)" for a signature Apple knows (default: ad hoc)
#   NOTARY_PROFILE=  a notarytool keychain profile (xcrun notarytool store-credentials) to notarize and staple the DMG
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
VERSION="$(cat app/VERSION)"
BUILD="$(git rev-list --count HEAD 2>/dev/null || echo 1)"
source app/runtime/versions.sh
ENGINE="build/Roughcut-engine-$RUNTIME_VERSION-arm64.tar.gz"
ENGINE_URL="https://github.com/Bouliw/fcpxml-roughcut/releases/download/$RUNTIME_RELEASE/$(basename "$ENGINE")"
ENGINE_SHA="$RUNTIME_SHA"
if [ -f "$ENGINE" ] && [ "$(shasum -a 256 "$ENGINE" | cut -d' ' -f1)" != "$ENGINE_SHA" ]; then
  echo "note: $ENGINE is not the published runtime ($RUNTIME_RELEASE); the app downloads the published one" >&2
fi

echo "== Swift"
swift build -c release --package-path app --arch arm64 2>&1 | tail -1
BIN="$(swift build -c release --package-path app --arch arm64 --show-bin-path)/Roughcut"

echo "== Bundle"
APP="build/Roughcut.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/engine"
cp "$BIN" "$APP/Contents/MacOS/Roughcut"
strip -S -x "$APP/Contents/MacOS/Roughcut"  # no debug map: it names the folders the app was built in
sed -e "s/__VERSION__/$VERSION/" -e "s/__BUILD__/$BUILD/" -e "s/__ENGINE_SHA__/$ENGINE_SHA/" -e "s|__ENGINE_URL__|$ENGINE_URL|" \
  -e "s/__ENGINE_VERSION__/$RUNTIME_VERSION/" \
  app/Info.plist > "$APP/Contents/Info.plist"
printf 'APPL????' > "$APP/Contents/PkgInfo"
rsync -a --exclude __pycache__ --exclude '*.pyc' scripts config "$APP/Contents/Resources/engine/"
cp app/engine/roughcut "$APP/Contents/Resources/engine/roughcut"  # the one way in, for the app, the Quick Action and scripts
cp LICENSE "$APP/Contents/Resources/LICENSE.txt"
cp THIRD_PARTY_NOTICES.md "$APP/Contents/Resources/THIRD_PARTY_NOTICES.txt"
cp app/Credits.html "$APP/Contents/Resources/Credits.html"  # shown by About Roughcut
swift app/icon/make_icon.swift build/AppIcon.iconset
iconutil -c icns build/AppIcon.iconset -o "$APP/Contents/Resources/AppIcon.icns"

echo "== Signature"
if [ -n "${SIGN_IDENTITY:-}" ]; then
  codesign --force --options runtime --timestamp -s "$SIGN_IDENTITY" "$APP"
else
  codesign --force -s - "$APP"  # ad hoc: macOS asks once to confirm the first opening
fi
codesign --verify --strict --verbose=1 "$APP"
if grep -r -l -a -F "$HOME" "$APP"; then echo "the files above contain $HOME" >&2; exit 1; fi
python3 app/check_macos.py "$APP" --target 14.0  # LSMinimumSystemVersion: nothing in the app needs a newer macOS

echo "== Disk image"
DMG="build/Roughcut-$VERSION.dmg"
STAGE="build/dmg"
rm -rf "$STAGE" "$DMG"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -quiet -volname "Roughcut" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
rm -rf "$STAGE"
if [ -n "${SIGN_IDENTITY:-}" ]; then codesign --force --timestamp -s "$SIGN_IDENTITY" "$DMG"; fi
if [ -n "${NOTARY_PROFILE:-}" ]; then
  xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
  xcrun stapler staple "$DMG"
fi
(cd build && shasum -a 256 "$(basename "$DMG")") | tee build/SHA256SUMS.txt
du -sh "$APP" "$DMG"
