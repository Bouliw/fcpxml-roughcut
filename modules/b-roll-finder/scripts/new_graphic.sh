#!/bin/sh
# A new Hyperframes graphic from a template of this skill, with GSAP copied in (no CDN at render time).
# Usage: new_graphic.sh TEMPLATE DEST     e.g. new_graphic.sh stat-card b-roll/graphics/b04-figure
set -eu
GSAP_VERSION=3.14.2
here=$(cd "$(dirname "$0")/.." && pwd)
tpl="$here/templates/$1"; dest=$2
[ -d "$tpl" ] || { echo "no template $1 (see $here/templates)" >&2; exit 1; }
[ ! -e "$dest" ] || { echo "$dest already exists" >&2; exit 1; }
cache="${XDG_CACHE_HOME:-$HOME/.cache}/b-roll-finder"
gsap="$cache/gsap-$GSAP_VERSION.min.js"
if [ ! -f "$gsap" ]; then
  mkdir -p "$cache"
  tmp=$(mktemp -d)
  (cd "$tmp" && npm pack "gsap@$GSAP_VERSION" --silent >/dev/null && tar xzf "gsap-$GSAP_VERSION.tgz")
  cp "$tmp/package/dist/gsap.min.js" "$gsap"
  rm -rf "$tmp"
fi
mkdir -p "$dest/vendor"
cp -R "$tpl/." "$dest/"
cp "$gsap" "$dest/vendor/gsap.min.js"
echo "$dest"
