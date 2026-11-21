/* CPU / battery status text and on-battery detection (port of warpman/status.py). */
#pragma once

#include <cairo.h>

/* True when a battery is present and no external power is online. sysfs_root is
 * normally "/sys/class/power_supply" (tests pass a fake tree). */
int wm_on_battery(const char *sysfs_root);

typedef struct {
    double interval;
    double next;                    /* monotonic time of the next refresh */
    int have_prev;
    unsigned long long prev_total, prev_idle;
    char text[160];
    const char *proc_stat;          /* "/proc/stat" */
    const char *sysfs_root;         /* "/sys/class/power_supply" */
} WmStatus;

void wm_status_init(WmStatus *st);
/* Refresh the text if the interval has passed; returns 1 if the text changed. */
int wm_status_update(WmStatus *st);
double wm_status_font_size(int h);
/* Draw the text at the bottom-right of a w x h area (font: wm_status_font_size(h) if
 * size <= 0). */
void wm_status_draw(const WmStatus *st, cairo_t *cr, int w, int h, double hue, double size);

double wm_monotonic(void);
void wm_hsv_to_rgb(double h, double s, double v, double rgb[3]);   /* colorsys.hsv_to_rgb */
