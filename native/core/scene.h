/* Warped-grid scenes: lenses, the warp and per-line styles (port of warpman/scene.py).
 *
 * Only what the GL renderer needs: the undisplaced grid segments (warped on the GPU)
 * and each line's width / brightness / saturation, picked from a coarse CPU sample of
 * the warp. The arithmetic follows the NumPy code operation by operation, so styles
 * match the Python ones exactly for the same scene.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "pyrandom.h"

#define WM_MAX_LENSES 16

typedef struct {
    double cx, cy, radius, strength;    /* strength > 0 bulges, < 0 pinches */
} WmLens;

typedef struct {
    int width, height;
    double spacing;
    double offset[2];
    int n_lenses;
    WmLens lenses[WM_MAX_LENSES];
    double hue;             /* degrees */
    double saturation;
    double brightness;
    double line_scale;
    int64_t seed;           /* drives the per-line width/brightness jitter */
} WmScene;

typedef struct {
    int vertical;
    double width, value, sat;
} WmLineStyle;

extern const double WM_BACKGROUND[3];

/* warpman.scene.random_scene(rng, w, h, lenses=n, pinch_chance=..., hue=..., seed=...)
 * with spacing=None and random_offset=False. hue may be NULL (random). */
void wm_random_scene(WmScene *s, PyRandom *rng, int w, int h, int n_lenses,
                     double pinch_chance, const double *hue, int64_t seed);

/* Number of grid lines (vertical + horizontal) for the scene's size and spacing. */
int wm_grid_line_count(const WmScene *s);

/* warpman.scene.line_styles(scene, step): fills out[] (vertical lines first), returns
 * the number of lines, or -1 if there are more than max. */
int wm_line_styles(const WmScene *s, double step, WmLineStyle *out, int max);

/* Undisplaced polyline segments of every grid line, sampled every `step` px:
 * segs gets x0, y0, x1, y1 per segment and ids the line index. Returns the segment
 * count; with segs == NULL only counts. */
size_t wm_grid_segments(const WmScene *s, double step, float *segs, float *ids);
