#!/bin/sh
# Background for swaylock-plugin: live warpman drift animation.
#   swaylock-plugin --command /path/to/drift-lock-bg.sh
#
# Runs layer.py (next to this script), a Wayland layer-shell client that renders with OpenGL
# straight onto each output at its native size.
# (swaylock-plugin restarts this script automatically if it ever exits.)
# Errors go to $XDG_RUNTIME_DIR/warpman-lock.log.

here=$(dirname "$(readlink -f "$0")")
fps=${DRIFT_FPS:-24}
hue=${DRIFT_HUE:-120} sat=${DRIFT_SATURATION:-0.5}            # green tint
status=--status; [ "${DRIFT_STATUS:-1}" = 0 ] && status=   # CPU / battery overlay
still=--still-on-battery; [ "${DRIFT_ANIMATE_ON_BATTERY:-0}" = 1 ] && still=
log=${XDG_RUNTIME_DIR:-/tmp}/warpman-lock.log

repo=$(dirname "$(dirname "$here")")
export PYTHONPATH="$repo${PYTHONPATH:+:$PYTHONPATH}"     # the warpman package, installed or not
# use the repo venv if it exists (see pyproject.toml), else system python
py="$repo/.venv/bin/python"
"$py" -c 'import cairo' 2>/dev/null || py=python3   # missing or incomplete venv

# kill only our own children (never `kill 0`: that could hit swaylock-plugin itself)
trap 'pkill -TERM -P $$; exit 0' TERM INT HUP

"$py" "$here/layer.py" --fps "$fps" --new-scene-every "${DRIFT_SCENE_SECONDS:-300}" \
        $status $still --hue "$hue" --saturation "$sat" 2>"$log" &
wait $!   # signals are only handled while waiting
code=$?
echo "layer.py exited ($code)" >>"$log"
# Failed (e.g. no GL): the lock stays on its plain colour. Back off before
# swaylock-plugin restarts us, so a broken setup doesn't respawn in a tight loop.
sleep 10 &
wait $!
exit "$code"
