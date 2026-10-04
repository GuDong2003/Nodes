#!/bin/sh
set -eu

display="${DISPLAY:-:99}"
display_number="${display#:}"
display_number="${display_number%%.*}"
socket="/tmp/.X11-unix/X${display_number}"
lock="/tmp/.X${display_number}-lock"
log="/tmp/nodes-xvfb.log"

cleanup() {
  if [ -n "${xvfb_pid:-}" ]; then
    kill "$xvfb_pid" 2>/dev/null || true
    wait "$xvfb_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT HUP INT TERM

# A previous Xvfb can leave its socket and lock behind after an unclean stop.
# Only keep them when the display is actually responsive; otherwise remove the
# stale runtime files before starting a fresh server.
if xdpyinfo -display "$display" >/dev/null 2>&1; then
  xvfb_pid=""
else
  rm -f "$socket" "$lock"
  Xvfb "$display" -screen 0 1920x1080x24 -nolisten tcp >"$log" 2>&1 &
  xvfb_pid=$!
fi

i=0
while ! xdpyinfo -display "$display" >/dev/null 2>&1; do
  if [ -n "$xvfb_pid" ] && ! kill -0 "$xvfb_pid" 2>/dev/null; then
    cat "$log" >&2
    exit 1
  fi
  i=$((i + 1))
  if [ "$i" -ge 100 ]; then
    echo "Xvfb did not make $display responsive" >&2
    cat "$log" >&2
    exit 1
  fi
  sleep 0.1
done

set +e
"$@"
status=$?
set -e
exit "$status"
