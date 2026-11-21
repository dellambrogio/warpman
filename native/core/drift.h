/* The drift animation, independent of any display (port of warpman/drift.py).
 *
 * WmSim holds a scene whose lens centres wander organically, switches scenes and keeps
 * the rainbow colouring. With the same seed it follows the Python Sim exactly.
 */
#pragma once

#include "pyrandom.h"
#include "scene.h"

typedef enum { WM_RAINBOW_OFF, WM_RAINBOW_WAVE, WM_RAINBOW_RIPPLE, WM_RAINBOW_CYCLE } WmRainbowMode;

#define WM_RAINBOW_STOPS 12
#define WM_RAINBOW_SAT 0.85
#define WM_RAINBOW_BOOST 1.6

typedef struct {
    double speed;           /* lens speed in px/s at 1280 wide */
    int lenses;             /* 0 = random 1-3 */
    int has_seed;
    int64_t seed;           /* seed of the first scene */
    int has_hue;
    double hue;
    int has_saturation;
    double saturation;
    WmRainbowMode rainbow;
    double rainbow_speed;   /* spectrum cycles per second */
} WmSimArgs;

typedef struct {
    double heading, turn, base_speed, phase, base_radius, base_strength;
} WmDrifter;

typedef struct {
    WmRainbowMode mode;
    double speed;
    double phase;
    double wave_vec[2];
    double center[2];
    double lam;
} WmRainbow;

typedef struct {
    WmSimArgs args;
    double t;
    int have_scene;
    WmScene scene;
    WmDrifter drifters[WM_MAX_LENSES];
    PyRandom rng;           /* the scene's rng; the drifters keep drawing from it */
    PyRandom global;        /* seeds of later scenes (Python: the global random module) */
    WmRainbow rainbow;
    int quiet;              /* don't log scenes to stderr */
} WmSim;

void wm_sim_init(WmSim *sim, const WmSimArgs *args, int w, int h);
void wm_sim_new_scene(WmSim *sim, int w, int h);
void wm_sim_resize(WmSim *sim, int w, int h);
void wm_sim_step(WmSim *sim, double dt);
/* Rainbow.update(t, scene): call before rendering when the rainbow is on. */
void wm_rainbow_update(WmRainbow *r, double t, const WmScene *s);

const char *wm_rainbow_name(WmRainbowMode m);
int wm_rainbow_parse(const char *s, WmRainbowMode *out);    /* 0 on success */
