#!/bin/sh
# xsecurelock saver: live warpman drift animation (X11 / i3).
#   XSECURELOCK_SAVER=/path/to/plugins/i3/saver.sh xsecurelock
# (or just run lock.sh, which does that)
#
# xsecurelock runs one copy per monitor, each with its window in $XSCREENSAVER_WINDOW,
# and restarts it if it exits. Errors go to $XDG_RUNTIME_DIR/warpman-lock-x11.log.

here=$(dirname "$(readlink -f "$0")")
fps=${DRIFT_FPS:-24}
hue=${DRIFT_HUE:-120} sat=${DRIFT_SATURATION:-0.5}            # green tint
status=--status; [ "${DRIFT_STATUS:-1}" = 0 ] && status=   # CPU / battery overlay
still=--still-on-battery; [ "${DRIFT_ANIMATE_ON_BATTERY:-0}" = 1 ] && still=
log=${XDG_RUNTIME_DIR:-/tmp}/warpman-lock-x11.log

repo=$(dirname "$(dirname "$here")")
export PYTHONPATH="$repo${PYTHONPATH:+:$PYTHONPATH}"     # the warpman package, installed or not
# use the repo venv if it exists (see pyproject.toml), else system python
py="$repo/.venv/bin/python"
"$py" -c 'import cairo' 2>/dev/null || py=python3   # missing or incomplete venv

trap 'pkill -TERM -P $$; exit 0' TERM INT HUP

"$py" "$here/saver.py" --fps "$fps" --new-scene-every "${DRIFT_SCENE_SECONDS:-300}" \
        $status $still --hue "$hue" --saturation "$sat" 2>>"$log" &
wait $!   # signals are only handled while waiting
code=$?
echo "saver.py exited ($code)" >>"$log"
# Failed (e.g. no GL): the lock stays black. Back off before xsecurelock restarts us,
# so a broken setup doesn't respawn in a tight loop.
if [ "$code" != 0 ]; then
    sleep 10 &
    wait $!
fi
exit "$code"
