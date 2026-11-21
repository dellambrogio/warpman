#!/bin/sh
# Lock an X11 session (i3, ...) with xsecurelock and the warpman drift animation.
# Replaces i3lock / i3lock-fancy:
#   bindsym $mod+BackSpace exec --no-startup-id /path/to/plugins/i3/lock.sh
#   exec --no-startup-id xss-lock --transfer-sleep-lock -- /path/to/plugins/i3/lock.sh
# Extra xsecurelock settings can be set in the environment (see `man xsecurelock`).

here=$(dirname "$(readlink -f "$0")")
: > "${XDG_RUNTIME_DIR:-/tmp}/warpman-lock-x11.log"     # fresh log per lock
export XSECURELOCK_SAVER="$here/saver.sh"
export XSECURELOCK_SAVER_RESET_ON_AUTH_CLOSE=${XSECURELOCK_SAVER_RESET_ON_AUTH_CLOSE:-0}
export XSECURELOCK_SHOW_DATETIME=${XSECURELOCK_SHOW_DATETIME:-1}
exec xsecurelock "$@"
