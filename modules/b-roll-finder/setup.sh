#!/bin/sh
# Checks the tools this skill drives and installs only the missing ones (macOS with Homebrew; elsewhere it says what
# is missing). Run it once: sh modules/b-roll-finder/setup.sh   (--check: install nothing)
# It never installs a Whisper (mlx-whisper or other): the word-level transcript is Roughcut's, made with its
# whisper.cpp. It never touches the system Python: Pillow and websockets go into a venv of the skill's own.
set -u
CHECK=0; [ "${1:-}" = "--check" ] && CHECK=1
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
here=$(cd "$(dirname "$0")" && pwd)
HF_VERSION=$(sed -n 's/^HF_VERSION=//p' "$here/scripts/hf.sh")
missing=""
have() { command -v "$1" >/dev/null 2>&1; }
say() { printf '%-9s %s\n' "$1" "$2"; }
install() {  # what, brew formula
  if [ $CHECK = 1 ] || ! have brew; then say MISSING "$1 (brew install $2)"; missing="$missing $1"; return 1; fi
  say ADDING "$1 (brew install $2)"; brew install "$2" >/dev/null && return 0
  say FAILED "$1"; missing="$missing $1"; return 1
}

# search/download, compositing, contact sheets
have yt-dlp && say OK "yt-dlp $(yt-dlp --version)" || install yt-dlp yt-dlp
have ffmpeg && have ffprobe && say OK "ffmpeg" || install ffmpeg ffmpeg
{ have magick || have montage; } && say OK "ImageMagick" || install ImageMagick imagemagick

# Node >= 22, then Hyperframes, pinned, no self-update, no telemetry
node_major=$(node -v 2>/dev/null | sed 's/^v\([0-9]*\).*/\1/')
if [ -z "$node_major" ]; then install node node && node_major=$(node -v | sed 's/^v\([0-9]*\).*/\1/'); fi
if [ -n "$node_major" ] && [ "$node_major" -ge 22 ]; then
  say OK "Node $(node -v)"
  export HYPERFRAMES_NO_UPDATE_CHECK=1 HYPERFRAMES_NO_AUTO_INSTALL=1 HYPERFRAMES_SKIP_SKILLS=1 HYPERFRAMES_NO_TELEMETRY=1
  if [ "$(hyperframes --version 2>/dev/null)" = "$HF_VERSION" ]; then say OK "Hyperframes $HF_VERSION"
  elif [ $CHECK = 1 ]; then say MISSING "Hyperframes $HF_VERSION (npm install -g hyperframes@$HF_VERSION)"; missing="$missing hyperframes"
  else say ADDING "Hyperframes $HF_VERSION"; npm install -g "hyperframes@$HF_VERSION" >/dev/null 2>&1 || { say FAILED hyperframes; missing="$missing hyperframes"; }
  fi
  have hyperframes && hyperframes telemetry disable >/dev/null 2>&1
elif [ -n "$node_major" ]; then
  say OLD "Node $(node -v): Hyperframes needs 22 or later (brew upgrade node); left as is"; missing="$missing node>=22"
fi

# a Chrome for the page captures: Google Chrome if there, else the headless one Hyperframes keeps
if [ -d "/Applications/Google Chrome.app" ] || [ -d "/Applications/Chromium.app" ] || have google-chrome || have chromium; then
  say OK "Chrome"
elif ls "$HOME"/.cache/hyperframes/chrome/chrome-headless-shell/*/*/chrome-headless-shell >/dev/null 2>&1; then
  say OK "Chrome (Hyperframes' headless shell)"
elif [ $CHECK = 0 ] && have hyperframes; then
  say ADDING "headless Chrome (hf.sh browser ensure)"; sh "$here/scripts/hf.sh" browser ensure >/dev/null 2>&1 || { say FAILED chrome; missing="$missing chrome"; }
else say MISSING "Chrome (install Google Chrome, or run scripts/hf.sh browser ensure)"; missing="$missing chrome"
fi

# Pillow + websockets, in the skill's own venv (scripts/run uses it)
venv="${XDG_DATA_HOME:-$HOME/.local/share}/b-roll-finder/venv"
if [ -x "$venv/bin/python" ] && "$venv/bin/python" -c 'import PIL, websockets' 2>/dev/null; then say OK "Python venv ($venv)"
elif [ $CHECK = 1 ]; then say MISSING "Python venv with Pillow and websockets"; missing="$missing venv"
else
  say ADDING "Python venv with Pillow and websockets ($venv)"
  pillow="pillow==12.3.0"  # needs Python 3.10+; macOS's own python3 is 3.9
  python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || pillow="pillow==11.3.0"
  python3 -m venv "$venv" && "$venv/bin/pip" install -q "$pillow" "websockets==15.0.1" || { say FAILED venv; missing="$missing venv"; }
fi

# checked only: what Roughcut and Final Cut Pro already provide
ROUGHCUT=""
for c in "${ROUGHCUT_ENGINE:-}" "$here/../../scripts/make_fcpxml.py" "/Applications/Roughcut.app/Contents/Resources/engine/roughcut"; do
  [ -n "$c" ] && [ -e "$c" ] && ROUGHCUT=$c && break
done
[ -n "$ROUGHCUT" ] && say OK "Roughcut ($ROUGHCUT)" || { say MISSING "Roughcut (the app, or set ROUGHCUT_ENGINE to a clone of fcpxml-roughcut)"; missing="$missing roughcut"; }
{ [ -d "/Applications/Final Cut Pro.app" ] || [ -d "/Applications/Final Cut Pro Trial.app" ]; } && say OK "Final Cut Pro" || say INFO "Final Cut Pro not found: the FCPXML is not checked against its DTD"

if [ -n "$missing" ]; then echo "Missing:$missing"; exit 1; fi
echo "Ready."
