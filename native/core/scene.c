#include "scene.h"

#include <math.h>

const double WM_BACKGROUND[3] = {0.005, 0.005, 0.005};

void wm_random_scene(WmScene *s, PyRandom *rng, int w, int h, int n_lenses,
                     double pinch_chance, const double *hue, int64_t seed)
{
    static const double spacings[] = {110, 128, 160, 175};
    if (n_lenses > WM_MAX_LENSES)
        n_lenses = WM_MAX_LENSES;
    double big = w > h ? w : h;
    s->width = w;
    s->height = h;
    s->spacing = spacings[py_randbelow(rng, 4)];
    s->offset[0] = s->offset[1] = 0.0;
    s->n_lenses = n_lenses;
    for (int i = 0; i < n_lenses; i++) {
        WmLens *l = &s->lenses[i];
        l->radius = py_uniform(rng, 0.22, 0.38) * big;
        double st = py_uniform(rng, 0.3, 0.55);
        l->strength = st * (py_random(rng) > pinch_chance ? 1 : -1);
        l->cx = py_uniform(rng, 0.15, 0.85) * w;
        l->cy = py_uniform(rng, 0.2, 0.8) * h;
    }
    s->hue = hue ? *hue : py_uniform(rng, 0, 360);
    s->saturation = py_uniform(rng, 0.08, 0.25);
    s->brightness = 1.0;
    s->line_scale = 1.0;
    s->seed = seed;
}

/* Iterate the grid lines exactly like warpman.scene.grid_arrays: positions accumulate
 * by repeated addition, starting at (offset % spacing) - margin. */
typedef struct {
    int vertical;
    double pos;     /* x of a vertical line, y of a horizontal one */
    int n;          /* samples along it */
} GridLine;

static int grid_lines(const WmScene *s, double step, GridLine *out, int max)
{
    double margin = s->spacing;
    int count = 0;
    double x = py_fmod(s->offset[0], s->spacing) - margin;
    while (x <= s->width + margin) {
        if (out && count < max)
            out[count] = (GridLine){1, x, (int)((s->height + 2 * margin) / step) + 1};
        count++;
        x += s->spacing;
    }
    double y = py_fmod(s->offset[1], s->spacing) - margin;
    while (y <= s->height + margin) {
        if (out && count < max)
            out[count] = (GridLine){0, y, (int)((s->width + 2 * margin) / step) + 1};
        count++;
        y += s->spacing;
    }
    return count;
}

int wm_grid_line_count(const WmScene *s)
{
    return grid_lines(s, 4.0, NULL, 0);
}

int wm_line_styles(const WmScene *s, double step, WmLineStyle *out, int max)
{
    GridLine lines[1024];
    int n = grid_lines(s, step, lines, 1024);
    if (n > max || n > 1024)
        return -1;
    double peak = 0;
    int have_peak = 0;
    for (int i = 0; i < s->n_lenses; i++) {
        double p = fabs(s->lenses[i].strength) * s->lenses[i].radius * 0.286;
        if (!have_peak || p > peak)
            peak = p;
        have_peak = 1;
    }
    if (!have_peak)
        peak = 1;
    if (peak == 0)
        peak = 1;

    PyRandom rng;
    py_seed(&rng, s->seed);
    double margin = s->spacing;
    for (int li = 0; li < n; li++) {
        const GridLine *g = &lines[li];
        double dmax = -INFINITY;
        for (int k = 0; k < g->n; k++) {
            double along = -margin + k * step;
            double x = g->vertical ? g->pos : along;
            double y = g->vertical ? along : g->pos;
            double ox = 0, oy = 0;
            for (int i = 0; i < s->n_lenses; i++) {
                const WmLens *l = &s->lenses[i];
                double dx = x - l->cx, dy = y - l->cy;
                double r = hypot(dx, dy);
                if (r > 0 && r < l->radius) {
                    double t = r / l->radius;
                    double u = 1 - t * t;
                    double kk = l->strength * l->radius * t * (u * u) / r;
                    ox += dx * kk;
                    oy += dy * kk;
                }
            }
            double d = hypot(ox, oy);
            if (d > dmax || isnan(d))
                dmax = d;
        }
        double bend = dmax / peak;
        if (1.5 < bend)
            bend = 1.5;
        double jitter = py_uniform(&rng, 0.85, 1.15);
        double sat = s->saturation + 0.1 * bend;
        out[li] = (WmLineStyle){
            .vertical = g->vertical,
            .width = (0.7 + 1.6 * bend) * jitter * s->line_scale,
            .value = (0.2 + 0.14 * bend) * jitter * s->brightness,
            .sat = 1 < sat ? 1 : sat,
        };
    }
    return n;
}

size_t wm_grid_segments(const WmScene *s, double step, float *segs, float *ids)
{
    GridLine lines[1024];
    int n = grid_lines(s, step, lines, 1024);
    if (n > 1024)
        n = 1024;
    double margin = s->spacing;
    size_t count = 0;
    for (int li = 0; li < n; li++) {
        const GridLine *g = &lines[li];
        for (int k = 0; k + 1 < g->n; k++) {
            if (segs) {
                double a = -margin + k * step, b = -margin + (k + 1) * step;
                float *o = segs + 4 * count;
                if (g->vertical) {
                    o[0] = (float)g->pos; o[1] = (float)a; o[2] = (float)g->pos; o[3] = (float)b;
                } else {
                    o[0] = (float)a; o[1] = (float)g->pos; o[2] = (float)b; o[3] = (float)g->pos;
                }
                ids[count] = (float)li;
            }
            count++;
        }
    }
    return count;
}
