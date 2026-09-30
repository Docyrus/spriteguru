#!/usr/bin/env bash
# Build the engine and the launcher, then place the engine in an engine/ folder: inside the macOS bundle's
# Resources (Contents/MacOS may hold only code, and the engine's data folders break the bundle's seal, RT5),
# elsewhere next to the launcher.
set -euo pipefail
cd "$(dirname "$0")"
uv run pyinstaller --noconfirm --distpath ../dist --workpath ../build/pyi engine.spec
uv run pyinstaller --noconfirm --distpath ../dist --workpath ../build/pyi launcher.spec
app=../dist/SpriteGuru.app
if [ -d "$app" ]; then
  target=$app/Contents/Resources
else
  target=../dist/SpriteGuru
fi
mkdir -p "$target/engine"
cp -R ../dist/spriteguru-engine/. "$target/engine/"
if [ -d "$app" ]; then
  plist=$app/Contents/Info.plist
  # OS6: the bundle carries the app's name and id
  name=$(/usr/libexec/PlistBuddy -c "Print :CFBundleName" "$plist")
  id=$(/usr/libexec/PlistBuddy -c "Print :CFBundleIdentifier" "$plist")
  [ "$name" = SpriteGuru ] && [ "$id" = com.spriteguru.studio ] || { echo "bundle is $name ($id)" >&2; exit 1; }
  echo "bundle: $name ($id)"
  # the bundle's version is the package's, and it says which macOS it needs
  want=$(cd .. && uv run python -c "import spriteguru; print(spriteguru.__version__)")
  have=$(/usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" "$plist")
  minos=$(/usr/libexec/PlistBuddy -c "Print :LSMinimumSystemVersion" "$plist")
  [ "$have" = "$want" ] || { echo "bundle version $have, package $want" >&2; exit 1; }
  echo "version: $have (macOS $minos or newer)"
  # B5: the bundle's icon must be the brand kit's, not PyInstaller's default
  icon=$(/usr/libexec/PlistBuddy -c "Print :CFBundleIconFile" "$plist")
  cmp "$app/Contents/Resources/${icon%.icns}.icns" ../brand/SpriteGuru.icns
  echo "icon: $icon matches brand/SpriteGuru.icns"
  # RT5: the engine was copied in after PyInstaller sealed the bundle, which breaks the seal, and a
  # downloaded copy with a broken seal is reported as damaged. Re-seal it (ad hoc: there is no
  # Developer ID yet) and fail the build unless the result verifies.
  codesign --force --deep --sign - "$app"
  codesign --verify --deep --strict "$app"
  echo "signature: ad hoc, sealed and verified"
fi
echo "built: $target"
