#!/bin/sh
# Hyperframes, pinned, with no self-update, no telemetry and no skills written into the project.
# Usage: hf.sh render DIR -o out.mp4 --fps 25 [--variables '{"value": 12}']   (any hyperframes command)
# A newer version is a deliberate change: edit HF_VERSION after reading its release notes.
HF_VERSION=0.8.79
export HYPERFRAMES_NO_UPDATE_CHECK=1 HYPERFRAMES_NO_AUTO_INSTALL=1 HYPERFRAMES_SKIP_SKILLS=1 HYPERFRAMES_NO_TELEMETRY=1 DO_NOT_TRACK=1
if command -v hyperframes >/dev/null 2>&1 && [ "$(hyperframes --version 2>/dev/null)" = "$HF_VERSION" ]; then
  exec hyperframes "$@"
fi
exec npx --yes "hyperframes@$HF_VERSION" "$@"
