#!/bin/bash
# Checks the tools the pipeline needs. Installs nothing: prints the commands to run instead.
export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH
MODEL_DIR="${WHISPER_MODEL_DIR:-$HOME/.cache/whisper-cpp}"
MODEL="${WHISPER_MODEL:-ggml-large-v3-turbo.bin}"
missing=0
for t in ffmpeg ffprobe python3 xmllint; do
  if command -v "$t" >/dev/null; then echo "OK       $t"; else echo "MISSING  $t"; missing=1; fi
done
if command -v whisper-cli >/dev/null; then echo "OK       whisper-cli"; else
  echo "MISSING  whisper-cli  -> brew install whisper-cpp"; missing=1; fi
LARGE="$MODEL_DIR/ggml-large-v3.bin"
if [ -f "$MODEL_DIR/$MODEL" ]; then echo "OK       model $MODEL_DIR/$MODEL"
elif [ -z "$WHISPER_MODEL" ] && [ -f "$LARGE" ]; then echo "OK       model $LARGE (large-v3-turbo not found: transcribe.sh uses large-v3)"; else
  echo "MISSING  Whisper model -> mkdir -p \"$MODEL_DIR\" && curl -L -o \"$MODEL_DIR/$MODEL\" https://huggingface.co/ggerganov/whisper.cpp/resolve/main/$MODEL   (large-v3: ~3 GB, large-v3-turbo: ~1.6 GB)"; missing=1; fi
if ffmpeg -hide_banner -encoders 2>/dev/null | grep -q h264_videotoolbox; then echo "OK       hardware encoding (h264_videotoolbox)"; else echo "INFO     no hardware encoder: the preview will use libx264"; fi
APP="$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import fcp; print(fcp.find_app() or "")' "$(dirname "$0")")"
if [ -n "$APP" ]; then echo "OK       $(basename "$APP" .app): FCPXML files are checked against its DTD, --open imports them"
else echo "INFO     Final Cut Pro not found (set FCP_APP to its name or path): FCPXML files are not checked against its DTD"; fi
exit $missing
