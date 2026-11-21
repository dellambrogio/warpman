#pragma once

#include "opts.h"

/* Run the X11 saver until killed, --duration, or the window goes away (exit code 0).
 * Returns 1 if it couldn't start (no display, no GL). */
int wm_saver_run(const WmOpts *o);
