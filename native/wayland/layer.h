#pragma once

#include "opts.h"

/* Run the layer-shell client until killed, --duration, or the compositor goes away
 * (all exit code 0). Returns 1 if it couldn't start (no compositor support, no GL). */
int wm_layer_run(const WmOpts *o);
