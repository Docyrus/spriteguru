#!/usr/bin/env bash
# Build the engine and the launcher, then place the engine in an engine/ folder next to the launcher.
set -euo pipefail
cd "$(dirname "$0")"
uv run pyinstaller --noconfirm --distpath ../dist --workpath ../build/pyi engine.spec
uv run pyinstaller --noconfirm --distpath ../dist --workpath ../build/pyi launcher.spec
app=../dist/SpriteGuru.app
if [ -d "$app" ]; then
  target=$app/Contents/MacOS
else
  target=../dist/SpriteGuru
fi
mkdir -p "$target/engine"
cp -R ../dist/spriteguru-engine/. "$target/engine/"
if [ -d "$app" ]; then
  plist=$app/Contents/Info.plist
  # N12: the bundle carries the app's name and id
  name=$(/usr/libexec/PlistBuddy -c "Print :CFBundleName" "$plist")
  id=$(/usr/libexec/PlistBuddy -c "Print :CFBundleIdentifier" "$plist")
  [ "$name" = SpriteGuru ] && [ "$id" = com.spriteplay.studio ] || { echo "bundle is $name ($id)" >&2; exit 1; }
  echo "bundle: $name ($id)"
  # B5: the bundle's icon must be the brand kit's, not PyInstaller's default
  icon=$(/usr/libexec/PlistBuddy -c "Print :CFBundleIconFile" "$plist")
  cmp "$app/Contents/Resources/${icon%.icns}.icns" ../brand/SpriteGuru.icns
  echo "icon: $icon matches brand/SpriteGuru.icns"
fi
echo "built: $target"
