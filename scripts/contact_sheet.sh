#!/bin/bash
# Contact sheets of a clip: one thumbnail every N seconds, 30 per sheet (6x5).
# Usage: contact_sheet.sh <clip> <output_dir> [N=5]
set -e
export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH
CLIP="$1"; OUT="$2"; N="${3:-5}"
mkdir -p "$OUT"
BASE="$(basename "${CLIP%.*}")"
ffmpeg -hide_banner -loglevel error -y -i "$CLIP" -vf "fps=1/$N,scale=480:-2,tile=6x5" -q:v 4 "$OUT/${BASE}_sheet_%02d.jpg"
ls "$OUT"/${BASE}_sheet_*.jpg
echo "Thumbnail k (0-29) on sheet p (1, 2...) = second ((p-1)*30 + k) * $N of the clip."
