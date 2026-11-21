/* GPU renderer for the warped grid, drawing straight into the current window's
 * framebuffer (port of warpman/gl.py's on-screen path).
 *
 * Two passes per frame:
 *  1. every polyline segment is an instanced quad whose fragment shader MIN-blends
 *     "distance to the segment + line id" into an RG32F texture (red: vertical lines,
 *     green: horizontal);
 *  2. a full-screen pass turns those distances into antialiased lines and composites
 *     background, vertical lines, then horizontal lines, like cairo.
 * The undisplaced segments are uploaded only when the grid changes; the vertex shader
 * warps them from the lens uniforms every frame.
 */
#pragma once

#include <epoxy/gl.h>

#include "drift.h"
#include "scene.h"
#include "status.h"

#define WM_GL_MAX_LINES 256
#define WM_GL_STEP 4.0          /* polyline sample spacing, as in warpman.scene.draw */
#define WM_GL_BEND_STEP 16.0    /* coarser sampling for the per-line styles */

typedef struct {
    GLuint geo, comp, ovl;                  /* programs */
    GLuint empty_vao, geo_vao, seg_buf, id_buf;
    GLuint key_tex, key_fbo;
    int tex_w, tex_h;
    int grid_w, grid_h;
    double grid_spacing, grid_offset[2];
    size_t n_segs;
    struct {
        GLint size, n_lenses, lenses, half_width, n_vertical;
    } ug;
    struct {
        GLint keys, style, background, hue, mode, phase, wave_vec, center, lam, boost,
            min_sat, stops, to_screen, height;
    } uc;
    struct {
        GLint rect, tex;
    } uo;
} WmGl;

/* Compile the shaders; needs a current GL 3.3 core context. 0 on success. */
int wm_gl_init(WmGl *g);
void wm_gl_destroy(WmGl *g);
/* Render a frame of size scene->width x scene->height into framebuffer 0.
 * rainbow may be NULL (plain tint); it must be updated for this frame. 0 on success. */
int wm_gl_render(WmGl *g, const WmScene *scene, const WmRainbow *rainbow);
/* Blend a premultiplied texture onto the current target at (x, y) (top-left, y down). */
void wm_gl_overlay(WmGl *g, GLuint tex, int tw, int th, int x, int y, int w, int h);

/* The status text as a strip along the bottom edge, redrawn only when it changes. */
typedef struct {
    GLuint tex;
    int tw, th;
    char key_text[160];
    long key_hue;
    int key_w, key_h;
} WmStatusStrip;

void wm_status_strip_draw(WmStatusStrip *ss, WmGl *g, WmStatus *st, double hue, int w, int h);
void wm_status_strip_destroy(WmStatusStrip *ss);
