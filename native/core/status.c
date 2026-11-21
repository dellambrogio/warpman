#include "status.h"

#include <dirent.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

double wm_monotonic(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec * 1e-9;
}

void wm_hsv_to_rgb(double h, double s, double v, double rgb[3])
{
    if (s == 0.0) {
        rgb[0] = rgb[1] = rgb[2] = v;
        return;
    }
    int i = (int)(h * 6.0);
    double f = h * 6.0 - i;
    double p = v * (1.0 - s), q = v * (1.0 - s * f), t = v * (1.0 - s * (1.0 - f));
    i %= 6;
    if (i < 0)
        i += 6;
    double r[6][3] = {{v, t, p}, {q, v, p}, {p, v, t}, {p, q, v}, {t, p, v}, {v, p, q}};
    memcpy(rgb, r[i], sizeof r[i]);
}

/* first line of <dir>/<name>, stripped; 0 on success */
static int read_attr(const char *dir, const char *name, char *buf, size_t len)
{
    char path[1024];
    snprintf(path, sizeof path, "%.900s/%s", dir, name);
    FILE *f = fopen(path, "r");
    if (!f)
        return -1;
    int ok = fgets(buf, (int)len, f) != NULL;
    fclose(f);
    if (!ok)
        return -1;
    size_t n = strlen(buf);
    while (n && (buf[n - 1] == '\n' || buf[n - 1] == ' ' || buf[n - 1] == '\r'))
        buf[--n] = 0;
    return 0;
}

static int name_cmp(const void *a, const void *b)
{
    return strcmp(*(char *const *)a, *(char *const *)b);
}

/* sorted entries of a power_supply directory (full paths); returns the count */
static int supplies(const char *root, char paths[][512], int max)
{
    DIR *d = opendir(root);
    if (!d)
        return 0;
    char *names[64];
    int n = 0;
    struct dirent *e;
    while ((e = readdir(d)) && n < 64 && n < max) {
        if (e->d_name[0] == '.')
            continue;
        names[n++] = strdup(e->d_name);
    }
    closedir(d);
    qsort(names, n, sizeof *names, name_cmp);
    for (int i = 0; i < n; i++) {
        snprintf(paths[i], 512, "%s/%s", root, names[i]);
        free(names[i]);
    }
    return n;
}

int wm_on_battery(const char *root)
{
    char paths[64][512], buf[64];
    int n = supplies(root, paths, 64);
    int battery = 0, external = -1, discharging = 0;
    for (int i = 0; i < n; i++) {
        if (read_attr(paths[i], "type", buf, sizeof buf))
            continue;
        if (strcmp(buf, "Battery") == 0) {
            battery = 1;
            if (read_attr(paths[i], "status", buf, sizeof buf) == 0)
                discharging |= strcmp(buf, "Discharging") == 0;
        } else if (!strcmp(buf, "Mains") || !strcmp(buf, "USB") || !strcmp(buf, "USB_C") ||
                   !strcmp(buf, "USB_PD")) {
            if (read_attr(paths[i], "online", buf, sizeof buf) == 0)
                external = (external == 1) || strcmp(buf, "1") == 0;
        }
    }
    if (!battery)
        return 0;
    /* No adapter reported at all: go by the battery itself. ("Not charging" can mean
     * plugged in with a charge limit, so only "Discharging" counts.) */
    return external == -1 ? discharging : !external;
}

static int cpu_times(const char *path, unsigned long long *total, unsigned long long *idle)
{
    FILE *f = fopen(path, "r");
    if (!f)
        return -1;
    char line[512];
    int ok = fgets(line, sizeof line, f) != NULL;
    fclose(f);
    if (!ok || strncmp(line, "cpu", 3) != 0)
        return -1;
    unsigned long long v[16];
    int n = 0;
    char *p = line + 3;
    while (n < 16) {
        char *end;
        unsigned long long x = strtoull(p, &end, 10);
        if (end == p)
            break;
        v[n++] = x;
        p = end;
    }
    if (n < 4)
        return -1;
    *total = 0;
    for (int i = 0; i < n; i++)
        *total += v[i];
    *idle = v[3] + (n > 4 ? v[4] : 0);      /* idle + iowait */
    return 0;
}

void wm_status_init(WmStatus *st)
{
    memset(st, 0, sizeof *st);
    st->interval = 2.0;
    st->proc_stat = "/proc/stat";
    st->sysfs_root = "/sys/class/power_supply";
    st->next = wm_monotonic() + 1.0;        /* let a real CPU sample accumulate first */
    st->have_prev = cpu_times(st->proc_stat, &st->prev_total, &st->prev_idle) == 0;
}

int wm_status_update(WmStatus *st)
{
    double now = wm_monotonic();
    if (now < st->next)
        return 0;
    st->next = now + st->interval;
    char text[sizeof st->text] = "";
    size_t len = 0;
    unsigned long long total, idle;
    int have = cpu_times(st->proc_stat, &total, &idle) == 0;
    if (have && st->have_prev) {
        unsigned long long dt = total - st->prev_total, di = idle - st->prev_idle;
        if (total > st->prev_total)
            len += snprintf(text + len, sizeof text - len, "CPU %.0f%%",
                            100.0 * (double)(dt - di) / (double)dt);
    }
    st->have_prev = have;
    st->prev_total = total;
    st->prev_idle = idle;

    char paths[64][512], buf[64];
    int n = supplies(st->sysfs_root, paths, 64), bats = 0;
    for (int i = 0; i < n; i++) {
        if (read_attr(paths[i], "type", buf, sizeof buf) || strcmp(buf, "Battery") != 0)
            continue;
        char cap[32], status[64];
        if (read_attr(paths[i], "capacity", cap, sizeof cap) ||
            read_attr(paths[i], "status", status, sizeof status))
            continue;
        char *end;
        long c = strtol(cap, &end, 10);
        if (end == cap)
            continue;
        const char *mark = !strcmp(status, "Charging") ? " +" :
                           !strcmp(status, "Discharging") ? " -" : "";
        len += snprintf(text + len, len < sizeof text ? sizeof text - len : 0, "%s%sBAT %ld%%%s",
                        len && !bats ? "   " : "", bats ? "  " : "", c, mark);
        bats++;
        if (len >= sizeof text)
            break;
    }
    int changed = strcmp(text, st->text) != 0;
    snprintf(st->text, sizeof st->text, "%s", text);
    return changed;
}

double wm_status_font_size(int h)
{
    double s = h / 110.0;
    return s > 8 ? s : 8;
}

void wm_status_draw(const WmStatus *st, cairo_t *cr, int w, int h, double hue, double size)
{
    if (!st->text[0])
        return;
    if (size <= 0)
        size = wm_status_font_size(h);
    cairo_select_font_face(cr, "monospace", CAIRO_FONT_SLANT_NORMAL, CAIRO_FONT_WEIGHT_NORMAL);
    cairo_set_font_size(cr, size);
    cairo_text_extents_t ext;
    cairo_text_extents(cr, st->text, &ext);
    double margin = size * 1.5;
    double x = w - margin - ext.x_advance, y = h - margin;
    cairo_set_source_rgba(cr, 0, 0, 0, 0.55);     /* soft backing so text stays legible */
    double pad = size * 0.3;
    cairo_rectangle(cr, x - pad, y + ext.y_bearing - pad, ext.x_advance + 2 * pad, ext.height + 2 * pad);
    cairo_fill(cr);
    double rgb[3];
    wm_hsv_to_rgb(hue / 360, 0.3, 0.85, rgb);
    cairo_set_source_rgb(cr, rgb[0], rgb[1], rgb[2]);
    cairo_move_to(cr, x, y);
    cairo_show_text(cr, st->text);
}
