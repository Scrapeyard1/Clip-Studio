#!/bin/bash
# Double-click to start Clip Studio. It opens in your web browser.
# Keep the Terminal window open while you use it; closing the window stops Clip Studio.

cd "$(dirname "$0")" || exit 1
URL="http://localhost:8765"

if [ ! -x .venv/bin/python ]; then
  echo "Clip Studio isn't set up yet. Double-click \"Install Clip Studio (Mac)\" first."
  read -r -p "Press Return to close this window."
  exit 1
fi

# make FFmpeg (installed by Homebrew) findable, even if this Mac's Terminal isn't set up for Homebrew
for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
  if [ -x "$b" ]; then eval "$("$b" shellenv)"; break; fi
done

# already running (for example, opened twice)? just show it
if curl -fs "$URL/api/health" >/dev/null 2>&1; then
  open "$URL"
  echo "Clip Studio was already running. It's open in your browser."
  sleep 2
  exit 0
fi

printf "\033[1mStarting Clip Studio...\033[0m\n"
echo "Keep this window open while you use it. Close it to stop Clip Studio."
trap 'kill $PID 2>/dev/null' EXIT INT TERM HUP
export CLIPSTUDIO_LAUNCHER=1       # "Refresh & update" exits with code 3 to be started again on the new version
FIRST=1
while true; do
  NO_BROWSER=1 .venv/bin/python app/server.py &
  PID=$!
  if [ "$FIRST" = 1 ]; then
    FIRST=0
    READY=0
    for _ in $(seq 1 90); do
      if curl -fs "$URL/api/health" >/dev/null 2>&1; then READY=1; open "$URL"; break; fi
      if ! kill -0 $PID 2>/dev/null; then break; fi
      sleep 1
    done
    if [ "$READY" = 0 ] && kill -0 $PID 2>/dev/null; then
      echo "Clip Studio is taking unusually long to start. Leave this window open and go to $URL in your browser."
    fi
  fi
  wait $PID
  CODE=$?
  if [ "$CODE" = 3 ]; then
    echo
    echo "Updated. Starting the new version..."
    continue
  fi
  echo
  echo "Clip Studio stopped. If that wasn't on purpose, the message above says why. Send a screenshot of this window to Elijah."
  read -r -p "Press Return to close this window."
  exit "$CODE"
done
