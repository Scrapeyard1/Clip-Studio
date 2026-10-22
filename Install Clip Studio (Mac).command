#!/bin/bash
# Double-click to set up Clip Studio on a Mac. One time, about 20 minutes (mostly downloading).
# Safe to run again: anything already installed is skipped.

cd "$(dirname "$0")" || exit 1
LOG="$PWD/install-log.txt"
exec > >(tee "$LOG") 2>&1

step() { printf "\n\033[1;33m==> %s\033[0m\n" "$1"; }
fail() {
  printf "\n\033[1;31mSetup stopped: %s\033[0m\n" "$1"
  echo "Everything above is saved in install-log.txt (in the Clip Studio folder). Send that file to Elijah."
  read -r -p "Press Return to close this window."
  exit 1
}
find_brew() {
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    if [ -x "$b" ]; then eval "$("$b" shellenv)"; return 0; fi
  done
  return 1
}

printf "\033[1mClip Studio setup\033[0m\n"
echo "This installs what Clip Studio needs. Keep this window open until it says Done."

# 1. Homebrew: the free tool Macs use to install FFmpeg and Python
command -v brew >/dev/null 2>&1 || find_brew
if ! command -v brew >/dev/null 2>&1; then
  step "Installing Homebrew. It will ask for your Mac password (nothing shows while you type; that's normal) and to press Return."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" \
    || fail "Homebrew didn't install. Check the internet connection and double-click Install again."
  find_brew || fail "Homebrew installed but couldn't be found. Restart the Mac and double-click Install again."
fi

# 2. Python 3.12 and FFmpeg
step "Installing Python and FFmpeg (skipped if you already have them)"
brew list python@3.12 >/dev/null 2>&1 || brew install python@3.12 || fail "Python didn't install."
brew list ffmpeg >/dev/null 2>&1 || brew install ffmpeg || fail "FFmpeg didn't install."
PY="$(brew --prefix python@3.12)/bin/python3.12"
[ -x "$PY" ] || fail "Python 3.12 wasn't found after installing it."

# 3. Clip Studio's own private copy of Python and its parts (nothing else on the Mac is touched)
step "Setting up Clip Studio"
[ -x .venv/bin/python ] || "$PY" -m venv .venv || fail "Couldn't create Clip Studio's Python folder."
.venv/bin/python -m pip install --upgrade pip --quiet || fail "Couldn't update pip."
.venv/bin/python -m pip install -r requirements.txt || fail "Some of Clip Studio's parts didn't install."

# 4. Models: speech recognition, face detection, photo enhancement (~1.2 GB)
step "Downloading the speech, face and photo models (about 1.2 GB)"
.venv/bin/python scripts/get_models.py || fail "A model didn't download. Check the internet connection and double-click Install again."

step "Done"
echo "Double-click \"Open Clip Studio (Mac)\" to start it."
echo "The first time, add a Claude API key in Clip Studio's Settings."
read -r -p "Press Return to close this window."
