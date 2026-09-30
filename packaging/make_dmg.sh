#!/usr/bin/env bash
# macOS release image: build SpriteGuru.app (build_app.sh), then a compressed, drag-to-install DMG
# (the app and an Applications shortcut) at dist/SpriteGuru-<version>-<arch>.dmg, verified and with
# its SHA-256 printed for the release notes.
set -euo pipefail
cd "$(dirname "$0")"
[ "$(uname)" = Darwin ] || { echo "make_dmg.sh runs on macOS only" >&2; exit 1; }
./build_app.sh
version=$(cd .. && uv run python -c "import spriteguru; print(spriteguru.__version__)")
arch=$(uname -m)
dmg=../dist/SpriteGuru-$version-$arch.dmg
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
ditto ../dist/SpriteGuru.app "$stage/SpriteGuru.app"
ln -s /Applications "$stage/Applications"
rm -f "$dmg"
hdiutil create -volname "SpriteGuru $version" -srcfolder "$stage" -ov -format UDZO -imagekey zlib-level=9 "$dmg" >/dev/null
hdiutil verify "$dmg" >/dev/null
echo "dmg: $dmg ($(du -h "$dmg" | cut -f1))"
shasum -a 256 "$dmg"
