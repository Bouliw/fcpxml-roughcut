#!/bin/bash
# Generates a fake "talking head" take to try the pipeline on: a synthetic voice (macOS `say`)
# with a false start, a retake, long pauses and a hesitation, over an ffmpeg test pattern.
# 1080p at 29.97 fps with a 01:00:00:00 timecode, like camera footage.
# Usage: examples/make_demo_clip.sh [OUT_DIR]   (default: examples/demo)
set -euo pipefail
export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH

OUT="${1:-$(dirname "$0")/demo}"
VOICE="${DEMO_VOICE:-Samantha}"
mkdir -p "$OUT"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# [[slnc N]] inserts N milliseconds of silence
say -v "$VOICE" -o "$TMP/voice.aiff" \
  "Hi everyone, today we're going to... [[slnc 1000]] No, let me start again. [[slnc 1200]] \
Hi everyone! Today I'll show you how to edit a video from the terminal. [[slnc 2000]] \
Um... [[slnc 900]] First, Whisper transcribes every word with its timestamp. [[slnc 1500]] \
Then the cuts are written to a JSON file. [[slnc 1800]] \
And the script builds a Final Cut Pro timeline, accurate to the frame. [[slnc 1200]] Your turn!"

# Clip length = voice + 1 s of room tone
DUR="$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$TMP/voice.aiff")"
DUR="$(python3 -c "print(round($DUR + 1, 2))")"
ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "testsrc2=size=1920x1080:rate=30000/1001" \
  -i "$TMP/voice.aiff" -af apad -t "$DUR" \
  -c:v libx264 -pix_fmt yuv420p -c:a aac -ar 48000 -ac 2 \
  -timecode 01:00:00:00 "$OUT/talking-head.mp4"

echo "$OUT/talking-head.mp4"
