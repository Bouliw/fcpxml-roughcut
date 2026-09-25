#!/bin/bash
# Word-level transcription with whisper.cpp.
# Usage: transcribe.sh <clip> <output_dir> [language=auto] [audio_track=0]
# Model: $WHISPER_MODEL (default ggml-large-v3.bin) in $WHISPER_MODEL_DIR (default ~/.cache/whisper-cpp).
set -e
export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH
CLIP="$1"; OUT="$2"; LANG_="${3:-auto}"; TRACK="${4:-0}"
[ -n "$OUT" ] || { echo "usage: transcribe.sh <clip> <output_dir> [language=auto] [audio_track=0]"; exit 1; }
MODEL_DIR="${WHISPER_MODEL_DIR:-$HOME/.cache/whisper-cpp}"
MODEL="$MODEL_DIR/${WHISPER_MODEL:-ggml-large-v3.bin}"
[ -f "$CLIP" ] || { echo "clip not found: $CLIP"; exit 1; }
TURBO="$MODEL_DIR/ggml-large-v3-turbo.bin"
if [ ! -f "$MODEL" ] && [ -z "$WHISPER_MODEL" ] && [ -f "$TURBO" ]; then echo "large-v3 not found: using large-v3-turbo" >&2; MODEL="$TURBO"; fi
[ -f "$MODEL" ] || { echo "model not found: $MODEL (see check_env.sh)"; exit 1; }
mkdir -p "$OUT"
BASE="$(basename "${CLIP%.*}")"
ffmpeg -hide_banner -loglevel error -y -i "$CLIP" -map 0:a:$TRACK -vn -ac 1 -ar 16000 -c:a pcm_s16le "$OUT/$BASE.wav"
# -ml 1 -sow: one segment per word, with its timestamps; -oj: JSON output
whisper-cli -m "$MODEL" -f "$OUT/$BASE.wav" -l "$LANG_" -ml 1 -sow -oj -of "$OUT/$BASE" -np
rm -f "$OUT/$BASE.wav"
echo "$OUT/$BASE.json"
