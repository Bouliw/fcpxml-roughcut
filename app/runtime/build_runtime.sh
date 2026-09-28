#!/bin/bash
# Builds the engine runtime that Roughcut downloads at first launch, for Apple silicon: a relocatable Python with the
# packages the scripts import, ffmpeg and ffprobe (LGPL build, with libass for the captions, VideoToolbox) and
# whisper-cli (Metal). Everything is built from pinned sources (versions.sh) against the system libraries only: no
# Homebrew library ends up inside. The result is one archive and its SHA-256.
#
# Usage: app/runtime/build_runtime.sh [output_dir]
#   RATE=           a download speed limit if wanted, e.g. RATE=3M (curl --limit-rate); none by default
#   SIGN_IDENTITY=  "Developer ID Application: ..." to sign every binary for notarization (default: ad hoc)
#   JOBS=           parallel build jobs (default: all cores)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
OUT="${1:-$ROOT/build}"
SRC="${SRC:-$ROOT/build/runtime-src}"        # downloads (sources, wheels), kept from one build to the next
WORK="${WORK:-/tmp/roughcut-runtime-build}"  # where it is built: build paths end up inside the binaries, so a neutral one
LIMIT=(); [ -n "${RATE:-}" ] && LIMIT=(--limit-rate "$RATE")
JOBS="${JOBS:-$(sysctl -n hw.ncpu)}"
export MACOSX_DEPLOYMENT_TARGET=14.0 PYTHONDONTWRITEBYTECODE=1
source "$HERE/versions.sh"

DEPS="$WORK/deps"; STAGE="$WORK/stage"
rm -rf "$WORK"
mkdir -p "$SRC" "$DEPS" "$OUT"
log() { printf '\n== %s\n' "$*"; }

fetch() {  # fetch URL SHA256 -> path of the checked download (resumes, limited speed)
  local url="$1" sha="$2" file="$SRC/$(basename "${1%%\?*}")"
  if [ ! -f "$file.ok" ]; then
    curl -L --fail --silent --show-error --retry 3 ${LIMIT[@]+"${LIMIT[@]}"} -C - -o "$file" "$url"
    local got; got="$(shasum -a 256 "$file" | cut -d' ' -f1)"
    if [ -n "$sha" ] && [ "$got" != "$sha" ]; then echo "checksum mismatch for $url: $got" >&2; rm -f "$file"; exit 1; fi
    [ -n "$sha" ] || echo "  unpinned: $(basename "$file") $got  (add it to versions.sh)" >&2
    touch "$file.ok"
  fi
  echo "$file"
}

unpack() {  # unpack archive -> its folder under $WORK/build
  local dir="$WORK/build/$(basename "$1" | sed -E 's/\.tar\.(gz|xz)$//')"
  rm -rf "$dir"; mkdir -p "$dir"
  tar -xf "$1" -C "$dir" --strip-components 1
  echo "$dir"
}

# Only our own static libraries are visible to pkg-config, and none from Homebrew
export PKG_CONFIG="${PKG_CONFIG:-$(command -v pkg-config)}"
[ -x "$PKG_CONFIG" ] || { echo "pkg-config is needed to build (brew install pkg-config)" >&2; exit 1; }
export PKG_CONFIG_LIBDIR="$DEPS/lib/pkgconfig" PKG_CONFIG_PATH=""
export CFLAGS="-O2 -mmacosx-version-min=$MACOSX_DEPLOYMENT_TARGET" CXXFLAGS="-O2 -mmacosx-version-min=$MACOSX_DEPLOYMENT_TARGET"
export LDFLAGS="-mmacosx-version-min=$MACOSX_DEPLOYMENT_TARGET"
PATH_CLEAN="/usr/bin:/bin:/usr/sbin:/sbin"

log "Python"
rm -rf "$STAGE"; mkdir -p "$STAGE/bin" "$STAGE/licenses"
tar -xf "$(fetch "$PYTHON_URL" "$PYTHON_SHA")" -C "$STAGE"
PY="$STAGE/python/bin/python3"
"$PY" -c 'import sys; print(sys.version)'

wheels() {  # wheels REQUIREMENTS DIR: resolve with pip, download each wheel with the speed limit, check its hash
  local req="$1" dir="$2"
  mkdir -p "$dir"
  "$PY" -m pip install --quiet --disable-pip-version-check --dry-run --ignore-installed --only-binary=:all: \
    --report "$dir/report.json" -r "$req"
  "$PY" - "$dir/report.json" "$dir" > "$dir/list.txt" <<'PYEOF'
import json, sys
for item in json.load(open(sys.argv[1]))['install']:
    info = item['download_info']
    print(info['url'], info['archive_info']['hashes']['sha256'], item['metadata']['name'], item['metadata']['version'])
PYEOF
  while read -r url sha name version; do
    local f="$dir/$(basename "$url")"
    [ -f "$f" ] && [ "$(shasum -a 256 "$f" | cut -d' ' -f1)" = "$sha" ] && continue
    curl -L --fail --silent --show-error --retry 3 ${LIMIT[@]+"${LIMIT[@]}"} -o "$f" "$url"
    [ "$(shasum -a 256 "$f" | cut -d' ' -f1)" = "$sha" ] || { echo "hash mismatch: $url" >&2; exit 1; }
  done < "$dir/list.txt"
  cut -d' ' -f3,4 "$dir/list.txt"
}

log "Python packages"
wheels "$HERE/requirements.txt" "$SRC/wheels"
"$PY" -m pip install --quiet --disable-pip-version-check --no-index --find-links "$SRC/wheels" --no-compile \
  -r "$HERE/requirements.txt"
"$PY" -m pip freeze --disable-pip-version-check > "$STAGE/python-packages.txt"

log "Build tools (meson, ninja), outside the runtime"
if [ ! -x "$WORK/tools/bin/meson" ]; then
  "$PY" -m venv "$WORK/tools"
  PY_SAVED="$PY"; PY="$WORK/tools/bin/python3"
  wheels "$HERE/build-tools.txt" "$SRC/tool-wheels" > /dev/null
  "$PY" -m pip install --quiet --disable-pip-version-check --no-index --find-links "$SRC/tool-wheels" -r "$HERE/build-tools.txt"
  PY="$PY_SAVED"
fi
TOOLS="$WORK/tools/bin"

meson_build() {  # meson_build SOURCE_DIR [options...]: static library into $DEPS
  local dir="$1"; shift
  (cd "$dir" && PATH="$TOOLS:$PATH_CLEAN" "$TOOLS/meson" setup _build --prefix="$DEPS" --libdir=lib --buildtype=release \
     --default-library=static -Db_ndebug=true "$@" > "$WORK/meson-$(basename "$dir").log" \
   && PATH="$TOOLS:$PATH_CLEAN" "$TOOLS/ninja" -C _build install > /dev/null)
}

log "FreeType"
d="$(unpack "$(fetch "$FREETYPE_URL" "$FREETYPE_SHA")")"
meson_build "$d" -Dbrotli=disabled -Dbzip2=disabled -Dharfbuzz=disabled -Dpng=disabled -Dzlib=internal -Dtests=disabled
log "FriBidi"
d="$(unpack "$(fetch "$FRIBIDI_URL" "$FRIBIDI_SHA")")"
meson_build "$d" -Ddocs=false -Dtests=false -Dbin=false
log "HarfBuzz"
d="$(unpack "$(fetch "$HARFBUZZ_URL" "$HARFBUZZ_SHA")")"
meson_build "$d" -Dfreetype=enabled -Dglib=disabled -Dgobject=disabled -Dcairo=disabled -Dicu=disabled \
  -Dchafa=disabled -Dpng=disabled -Dzlib=disabled -Dgraphite2=disabled -Draster=disabled -Dvector=disabled \
  -Dgpu=disabled -Dsubset=disabled -Dtests=disabled -Ddocs=disabled -Dutilities=disabled -Dintrospection=disabled
log "libass"
d="$(unpack "$(fetch "$LIBASS_URL" "$LIBASS_SHA")")"
meson_build "$d" -Dfontconfig=disabled -Dcoretext=enabled -Ddirectwrite=disabled -Dtest=disabled -Dprofile=disabled \
  -Dfuzz=disabled -Dcheckasm=disabled -Dlibunibreak=disabled
cp "$d/COPYING" "$STAGE/licenses/libass.txt"

log "ffmpeg (LGPL)"
d="$(unpack "$(fetch "$FFMPEG_URL" "$FFMPEG_SHA")")"
(cd "$d" && PATH="$PATH_CLEAN:/opt/homebrew/bin" ./configure --prefix="$WORK/ffmpeg" --pkg-config="$PKG_CONFIG" \
   --pkg-config-flags=--static --enable-static --disable-shared --disable-doc --disable-ffplay --disable-debug \
   --disable-network --disable-autodetect --enable-videotoolbox --enable-audiotoolbox \
   --enable-libass --enable-libfreetype --enable-libfribidi --enable-libharfbuzz --enable-zlib \
   --extra-cflags="$CFLAGS" --extra-ldflags="$LDFLAGS" --extra-libs="-lc++" > "$WORK/ffmpeg-configure.log" \
 && make -j"$JOBS" > "$WORK/ffmpeg-make.log" 2>&1 && make install > /dev/null)
cp "$WORK/ffmpeg/bin/ffmpeg" "$WORK/ffmpeg/bin/ffprobe" "$STAGE/bin/"
cp "$d/COPYING.LGPLv2.1" "$STAGE/licenses/ffmpeg-LGPL-2.1.txt"
cp "$d/LICENSE.md" "$STAGE/licenses/ffmpeg.md"
for lib in freetype fribidi harfbuzz; do
  src="$(ls -d "$WORK/build/$lib"-* | head -1)"
  for f in LICENSE.TXT docs/FTL.TXT COPYING; do [ -f "$src/$f" ] && cp "$src/$f" "$STAGE/licenses/$lib-$(basename "$f")"; done
done

log "whisper.cpp (Metal)"
d="$(unpack "$(fetch "$WHISPER_URL" "$WHISPER_SHA")")"
(cd "$d" && PATH="$PATH_CLEAN:/opt/homebrew/bin" cmake -B _build -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF \
   -DGGML_METAL=ON -DGGML_METAL_EMBED_LIBRARY=ON -DGGML_NATIVE=OFF -DGGML_OPENMP=OFF -DWHISPER_SDL2=OFF \
   -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_SERVER=OFF -DCMAKE_OSX_DEPLOYMENT_TARGET="$MACOSX_DEPLOYMENT_TARGET" \
   > "$WORK/whisper-cmake.log" \
 && cmake --build _build --config Release -j"$JOBS" --target whisper-cli > "$WORK/whisper-build.log" 2>&1)
cp "$d/_build/bin/whisper-cli" "$STAGE/bin/"
cp "$d/LICENSE" "$STAGE/licenses/whisper.cpp.txt"

log "Trimming Python"
P="$STAGE/python/lib/python3.12"
rm -rf "$P/test" "$P/idlelib" "$P/tkinter" "$P/turtledemo" "$P/ensurepip" "$P/lib2to3" "$P/pydoc_data" \
  "$STAGE/python/lib/"tcl* "$STAGE/python/lib/"tk* "$STAGE/python/lib/"itcl* "$STAGE/python/lib/"libtcl* \
  "$STAGE/python/lib/"libtk* "$STAGE/python/lib/"thread* "$STAGE/python/share" "$P/site-packages/PyObjCTest"
rm -f "$P/lib-dynload/_tkinter"*.so "$P/lib-dynload/readline"*.so  # GUI toolkit and the line editor: not used
"$PY" -m pip uninstall --quiet --yes pip > /dev/null 2>&1 || true
find "$STAGE/python" -name __pycache__ -type d -prune -exec rm -rf {} +
cp "$STAGE/python/lib/python3.12/LICENSE.txt" "$STAGE/licenses/python.txt" 2>/dev/null || true

log "Licenses of the Python packages"
"$STAGE/python/bin/python3" - "$STAGE" <<'PYEOF'
import importlib.metadata as md, os, shutil, sys
stage = sys.argv[1]
lines = []
for dist in sorted(md.distributions(), key=lambda d: d.metadata['Name'].lower()):
    name, version = dist.metadata['Name'], dist.version
    lic = dist.metadata.get('License-Expression') or dist.metadata.get('License') or ''
    if not lic or len(lic) > 80:
        lic = ', '.join(c.split('::')[-1].strip() for c in dist.metadata.get_all('Classifier') or [] if c.startswith('License ::')) or lic[:80]
    lines.append(f'{name} {version}: {lic}')
    for f in dist.files or []:
        if 'licen' in f.name.lower() or 'copying' in f.name.lower() or f.name.lower() in ('notice', 'notice.txt', 'authors'):
            src = dist.locate_file(f)
            if os.path.isfile(src):
                dst = os.path.join(stage, 'licenses', 'python-packages', name, str(f).replace('/', '_'))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy(src, dst)
open(os.path.join(stage, 'licenses', 'python-packages.txt'), 'w').write('\n'.join(lines) + '\n')
print('\n'.join(lines))
PYEOF

if [ -n "${SIGN_IDENTITY:-}" ]; then
  log "Signing for notarization"
  find "$STAGE" -type f \( -perm -u+x -o -name '*.so' -o -name '*.dylib' \) -print0 | while IFS= read -r -d '' f; do
    file "$f" | grep -q Mach-O && codesign --force --timestamp --options runtime -s "$SIGN_IDENTITY" "$f"
  done
fi

log "Checks"
for b in ffmpeg ffprobe whisper-cli; do  # system libraries and frameworks only
  bad="$(otool -L "$STAGE/bin/$b" | tail -n +2 | awk '{print $1}' | grep -v -E '^(/usr/lib/|/System/Library/)' || true)"
  [ -z "$bad" ] || { echo "$b links outside the system: $bad" >&2; exit 1; }
done
"$STAGE/bin/ffmpeg" -hide_banner -L 2>&1 | sed -n 1,3p
filters="$("$STAGE/bin/ffmpeg" -hide_banner -filters 2>/dev/null)"; encoders="$("$STAGE/bin/ffmpeg" -hide_banner -encoders 2>/dev/null)"
grep -q ' ass ' <<< "$filters" || { echo "ffmpeg has no ass filter" >&2; exit 1; }
grep -q prores_ks <<< "$encoders" || { echo "ffmpeg has no prores_ks" >&2; exit 1; }
grep -q -- '--enable-gpl' <<< "$("$STAGE/bin/ffmpeg" -hide_banner -buildconf 2>&1)" && { echo "ffmpeg must stay LGPL" >&2; exit 1; }
"$STAGE/bin/whisper-cli" --help > /dev/null 2>&1 || { echo "whisper-cli does not run" >&2; exit 1; }
"$STAGE/python/bin/python3" -c 'import numpy, Vision, Quartz, anthropic; print("python ok: numpy", numpy.__version__)'

{
  echo "Roughcut engine runtime $RUNTIME_VERSION: third-party software, each under its own license (files in this folder)."
  echo "Built with app/runtime/build_runtime.sh of https://github.com/Bouliw/fcpxml-roughcut from these sources:"
  for v in PYTHON FFMPEG FREETYPE FRIBIDI HARFBUZZ LIBASS WHISPER; do u="${v}_URL"; h="${v}_SHA"; echo "  ${!u}  sha256 ${!h}"; done
  echo "Python packages (from PyPI): see python-packages.txt."
  echo; echo "ffmpeg and ffprobe are built under the LGPL 2.1 or later, with this configuration:"
  "$STAGE/bin/ffmpeg" -hide_banner -buildconf 2>&1 | sed 's/^/  /'
} > "$STAGE/licenses/README.txt"

cat > "$STAGE/runtime.json" <<JSON
{"runtime": $RUNTIME_VERSION, "python": "$(basename "$PYTHON_URL")", "ffmpeg": "$(basename "$FFMPEG_URL")",
 "whisper": "$(basename "$WHISPER_URL")", "built": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"}
JSON
log "No trace of this machine"
find "$STAGE/python/bin" -type f ! -name 'python3*' -delete  # console scripts: their first line names the build folder
find "$STAGE" -name __pycache__ -type d -prune -exec rm -rf {} +
if grep -r -l -a -F "$HOME" "$STAGE"; then echo "the files above contain $HOME: build elsewhere" >&2; exit 1; fi
python3 "$HERE/../check_macos.py" "$STAGE" --target "$MACOSX_DEPLOYMENT_TARGET"  # every binary runs on the oldest macOS supported
if grep -h -i -E "only available on macOS|unguarded-availability" "$WORK"/*.log; then echo "a newer macOS API is used unguarded" >&2; exit 1; fi

ARCHIVE="$OUT/Roughcut-engine-$RUNTIME_VERSION-arm64.tar.gz"
log "Archive"
COPYFILE_DISABLE=1 tar -czf "$ARCHIVE" --uid 0 --gid 0 --uname root --gname wheel -C "$STAGE" .  # no user of this Mac inside
owners="$(tar -tvzf "$ARCHIVE" | awk '{print $3 ":" $4}' | sort -u)"
[ "$owners" = "root:wheel" ] || { echo "the archive must be owned by root:wheel, not: $owners" >&2; exit 1; }
shasum -a 256 "$ARCHIVE" | tee "$ARCHIVE.sha256"
du -sh "$STAGE" "$ARCHIVE"
