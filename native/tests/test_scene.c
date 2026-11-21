/* Grid geometry and the lens warp. */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>

#include "scene.h"

static int failures;

static void expect(const char *what, int ok)
{
    printf("%-50s %s\n", what, ok ? "ok" : "FAILED");
    failures += !ok;
}

int main(void)
{
    WmScene s = {.width = 1280, .height = 800, .spacing = 160, .brightness = 1, .line_scale = 1,
                 .saturation = 0.15, .seed = 3};
    /* lines: x from -160 to 1440 step 160 (11), y from -160 to 960 (8) */
    expect("grid line count", wm_grid_line_count(&s) == 19);

    size_t n = wm_grid_segments(&s, 4.0, NULL, NULL);
    float *segs = malloc(n * 4 * sizeof(float)), *ids = malloc(n * sizeof(float));
    wm_grid_segments(&s, 4.0, segs, ids);
    /* vertical: int((800 + 320) / 4) + 1 = 281 points -> 280 segments; horizontal: 401 -> 400 */
    expect("segment count", n == 11 * 280 + 8 * 400);
    expect("first segment starts at (-160, -160)", segs[0] == -160 && segs[1] == -160 && segs[3] == -156);
    expect("segments are contiguous", segs[4] == segs[0] && segs[5] == segs[3]);
    expect("last segment belongs to the last line", ids[n - 1] == 18);

    WmLineStyle st[64];
    int k = wm_line_styles(&s, 16.0, st, 64);
    expect("no lenses: one style per line", k == 19);
    int straight = 1;
    for (int i = 0; i < k; i++)
        straight &= st[i].width >= 0.7 * 0.85 && st[i].width <= 0.7 * 1.15 && st[i].sat == 0.15;
    expect("no lenses: unbent widths and saturation", straight);
    expect("vertical lines first", st[0].vertical && st[10].vertical && !st[11].vertical);
    expect("too many lines is an error", wm_line_styles(&s, 16.0, st, 5) == -1);

    s.n_lenses = 1;
    s.lenses[0] = (WmLens){640, 400, 300, 0.5};
    k = wm_line_styles(&s, 16.0, st, 64);
    /* the vertical line through x = 480 crosses the lens and bends; x = -160 doesn't */
    expect("a bent line is wider than a straight one", st[4].width / st[0].width > 1.3);
    expect("bent lines are more saturated", st[4].sat > 0.15 && st[0].sat == 0.15);

    free(segs);
    free(ids);
    return failures ? 1 : 0;
}
