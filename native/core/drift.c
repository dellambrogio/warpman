#include "drift.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

static void drifter_init(WmDrifter *d, const WmLens *l, PyRandom *rng, double speed)
{
    d->heading = py_uniform(rng, 0, 2 * M_PI);
    d->turn = 0.0;
    d->base_speed = speed * py_uniform(rng, 0.6, 1.4);
    d->phase = py_uniform(rng, 0, 100);
    d->base_radius = l->radius;
    d->base_strength = l->strength;
}

static void drifter_step(WmDrifter *d, WmLens *l, PyRandom *rng, double dt, double t,
                         double w, double h)
{
    /* turn rate wanders and decays -> smooth curvy paths, no jitter */
    d->turn += py_gauss(rng, 0, 1.2) * sqrt(dt);
    d->turn *= exp(-0.8 * dt);
    d->heading += d->turn * dt;

    /* steer back when outside a soft box around the screen */
    double mx = 0.1 * w, my = 0.1 * h;
    double tx = l->cx < mx ? mx - l->cx : l->cx > w - mx ? (w - mx) - l->cx : 0;
    double ty = l->cy < my ? my - l->cy : l->cy > h - my ? (h - my) - l->cy : 0;
    if (tx != 0 || ty != 0) {
        double want = atan2(ty, tx);
        double diff = py_fmod(want - d->heading + M_PI, 2 * M_PI) - M_PI;
        double k = 1.5 * dt;
        d->heading += diff * (k < 1.0 ? k : 1.0);
    }

    double p = d->phase + t;
    double speed = d->base_speed * (0.75 + 0.25 * sin(p * 0.37) + 0.1 * sin(p * 0.91));
    l->cx += cos(d->heading) * speed * dt;
    l->cy += sin(d->heading) * speed * dt;
    /* gentle breathing of size and strength */
    l->radius = d->base_radius * (1 + 0.08 * sin(p * 0.21));
    l->strength = d->base_strength * (1 + 0.12 * sin(p * 0.29 + 1.3));
}

void wm_sim_init(WmSim *sim, const WmSimArgs *args, int w, int h)
{
    memset(sim, 0, sizeof *sim);
    sim->args = *args;
    py_seed_os(&sim->global);
    sim->rainbow.mode = args->rainbow;
    sim->rainbow.speed = args->rainbow_speed;
    wm_sim_new_scene(sim, w, h);
}

void wm_sim_new_scene(WmSim *sim, int w, int h)
{
    static const int counts[] = {1, 1, 2, 2, 3};
    int first = !sim->have_scene;
    int64_t seed = first && sim->args.has_seed ? sim->args.seed
                                                : (int64_t)py_randbelow(&sim->global, 1u << 30);
    py_seed(&sim->rng, seed);
    int n = sim->args.lenses ? sim->args.lenses : counts[py_randbelow(&sim->rng, 5)];
    wm_random_scene(&sim->scene, &sim->rng, w, h, n, 0.2,
                    sim->args.has_hue ? &sim->args.hue : NULL, seed);
    if (sim->args.has_saturation)
        sim->scene.saturation = sim->args.saturation;
    double speed = sim->args.speed * (w > h ? w : h) / 1280;
    for (int i = 0; i < sim->scene.n_lenses; i++)
        drifter_init(&sim->drifters[i], &sim->scene.lenses[i], &sim->rng, speed);
    sim->have_scene = 1;
    if (!sim->quiet)
        fprintf(stderr, "seed %lld: %d lens(es), hue %.0f, spacing %.0f\n", (long long)seed,
                sim->scene.n_lenses, sim->scene.hue, sim->scene.spacing);
}

void wm_sim_resize(WmSim *sim, int w, int h)
{
    WmScene *s = &sim->scene;
    if (w == s->width && h == s->height)
        return;
    double sx = (double)w / s->width, sy = (double)h / s->height;
    for (int i = 0; i < s->n_lenses; i++) {
        s->lenses[i].cx *= sx;
        s->lenses[i].cy *= sy;
        sim->drifters[i].base_radius *= sx > sy ? sx : sy;
    }
    s->width = w;
    s->height = h;
}

void wm_sim_step(WmSim *sim, double dt)
{
    sim->t += dt;
    WmScene *s = &sim->scene;
    for (int i = 0; i < s->n_lenses; i++)
        drifter_step(&sim->drifters[i], &s->lenses[i], &sim->rng, dt, sim->t, s->width, s->height);
}

void wm_rainbow_update(WmRainbow *r, double t, const WmScene *s)
{
    r->phase = py_fmod(t * r->speed, 1.0);
    double w = s->width, h = s->height;
    double lam = 0.6 * hypot(w, h);
    if (r->mode == WM_RAINBOW_WAVE) {
        r->wave_vec[0] = 0.93 * lam;
        r->wave_vec[1] = 0.37 * lam;
    } else if (r->mode == WM_RAINBOW_RIPPLE) {
        if (s->n_lenses) {
            r->center[0] = s->lenses[0].cx;
            r->center[1] = s->lenses[0].cy;
        } else {
            r->center[0] = w / 2;
            r->center[1] = h / 2;
        }
        r->lam = lam * 0.5;
    }
}

static const char *const rainbow_names[] = {"off", "wave", "ripple", "cycle"};

const char *wm_rainbow_name(WmRainbowMode m)
{
    return rainbow_names[m];
}

int wm_rainbow_parse(const char *s, WmRainbowMode *out)
{
    for (int i = 0; i < 4; i++) {
        if (strcmp(s, rainbow_names[i]) == 0) {
            *out = (WmRainbowMode)i;
            return 0;
        }
    }
    return -1;
}
