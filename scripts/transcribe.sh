#!/bin/bash
# Word-level transcription with whisper.cpp.
# Usage: transcribe.sh <clip> <output_dir> [language=auto] [audio_track=0]
# Model: $WHISPER_MODEL (default ggml-large-v3-turbo.bin) in $WHISPER_MODEL_DIR (default ~/.cache/whisper-cpp).
# ROUGHCUT_HESITATIONS=1: Whisper started on a text full of hesitations, so it writes them (languages.py).
# The language given is the main one: passages in another language are transcribed in theirs (languages.py).
set -e
command -v whisper-cli >/dev/null && command -v ffmpeg >/dev/null || export PATH="$PATH:/opt/homebrew/bin:/usr/local/bin"  # Homebrew only when the caller gives no tools
CLIP="$1"; OUT="$2"; LANG_="${3:-auto}"; TRACK="${4:-0}"
[ -n "$OUT" ] || { echo "usage: transcribe.sh <clip> <output_dir> [language=auto] [audio_track=0]" >&2; exit 1; }
MODEL_DIR="${WHISPER_MODEL_DIR:-$HOME/.cache/whisper-cpp}"
MODEL="$MODEL_DIR/${WHISPER_MODEL:-ggml-large-v3-turbo.bin}"
[ -f "$CLIP" ] || { echo "clip not found: $CLIP" >&2; exit 1; }
LARGE="$MODEL_DIR/ggml-large-v3.bin"
if [ ! -f "$MODEL" ] && [ -z "$WHISPER_MODEL" ] && [ -f "$LARGE" ]; then echo "large-v3-turbo not found: using large-v3" >&2; MODEL="$LARGE"; fi
[ -f "$MODEL" ] || { echo "model not found: $MODEL (see check_env.sh)" >&2; exit 1; }
mkdir -p "$OUT"
BASE="$(basename "${CLIP%.*}")"
# The 16 kHz audio goes to a temporary folder: next to the output it could overwrite the clip itself (a .wav)
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
ffmpeg -hide_banner -loglevel error -y -i "$CLIP" -map 0:a:$TRACK -vn -ac 1 -ar 16000 -c:a pcm_s16le "$TMP/audio.wav"
# -ml 1 -sow: one segment per word, with its timestamps; -oj: JSON output. One pass keeps one language (another one
# comes out translated, or left out): languages.py asks the language of every 30 s and, when several are spoken,
# transcribes each run in its own; with one language, it is one pass as before
if command -v python3 >/dev/null 2>&1; then
  python3 "$(dirname "$0")/languages.py" "$TMP/audio.wav" --model "$MODEL" --language "$LANG_" --out "$TMP/transcript.json" \
    ${ROUGHCUT_HESITATIONS:+--hesitations}
else
  whisper-cli -m "$MODEL" -f "$TMP/audio.wav" -l "$LANG_" -ml 1 -sow -oj -of "$TMP/transcript" -np
fi
mv "$TMP/transcript.json" "$OUT/$BASE.json"  # whole or not at all: a run stopped halfway leaves nothing to trip on
echo "$OUT/$BASE.json"
