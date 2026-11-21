#include "gl_render.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* The shaders are warpman/gl.py's, with its f-string constants as #defines. */
#define HEADER \
    "#version 330\n" \
    "#define MAX_LINES 256\n" \
    "#define MAX_LINES_F 256.0\n" \
    "#define MAX_LENSES 16\n" \
    "#define QUANT 64.0\n" \
    "#define MAX_DIST 8.0\n" \
    "#define EMPTY 1e9\n" \
    "#define EMPTY_HALF 5e8\n"

#define EMPTY 1e9f

static const char *GEOMETRY_VS = HEADER
    "uniform vec2 size;\n"
    "uniform int n_lenses;\n"
    "uniform vec4 lenses[MAX_LENSES];    // cx, cy, radius, strength\n"
    "uniform float half_width[MAX_LINES];\n"
    "uniform float n_vertical;\n"
    "in vec4 seg;          // undisplaced x0, y0, x1, y1 (image pixels, y down)\n"
    "in float line_id;\n"
    "out vec2 v_a;\n"
    "out vec2 v_b;\n"
    "flat out float v_id;\n"
    "flat out float v_vertical;\n"
    "vec2 warp(vec2 p) {\n"
    "    vec2 o = vec2(0.0);\n"
    "    for (int i = 0; i < n_lenses; i++) {\n"
    "        vec4 l = lenses[i];\n"
    "        vec2 d = p - l.xy;\n"
    "        float r2 = dot(d, d);\n"
    "        float R2 = l.z * l.z;\n"
    "        if (r2 < R2) {\n"
    "            float u = 1.0 - r2 / R2;\n"
    "            o += d * (l.w * u * u);\n"
    "        }\n"
    "    }\n"
    "    return p + o;\n"
    "}\n"
    "void main() {\n"
    "    vec2 a = warp(seg.xy), b = warp(seg.zw);\n"
    "    vec2 d = b - a;\n"
    "    float len = length(d);\n"
    "    vec2 t = len > 0.0 ? d / len : vec2(1.0, 0.0);\n"
    "    vec2 n = vec2(-t.y, t.x);\n"
    "    float e = half_width[int(line_id)] + 1.5;\n"
    "    float along = (gl_VertexID & 1) == 0 ? -e : len + e;\n"
    "    float side = (gl_VertexID & 2) == 0 ? -e : e;\n"
    "    vec2 p = a + t * along + n * side;\n"
    "    v_a = a;\n"
    "    v_b = b;\n"
    "    v_id = line_id;\n"
    "    v_vertical = line_id < n_vertical ? 1.0 : 0.0;\n"
    "    gl_Position = vec4(p / size * 2.0 - 1.0, 0.0, 1.0);\n"
    "}\n";

static const char *GEOMETRY_FS = HEADER
    "in vec2 v_a;\n"
    "in vec2 v_b;\n"
    "flat in float v_id;\n"
    "flat in float v_vertical;\n"
    "out vec2 key;\n"
    "void main() {\n"
    "    vec2 p = gl_FragCoord.xy;\n"
    "    vec2 ab = v_b - v_a;\n"
    "    float h = clamp(dot(p - v_a, ab) / max(dot(ab, ab), 1e-12), 0.0, 1.0);\n"
    "    float dist = length(p - v_a - ab * h);\n"
    "    float k = floor(min(dist, MAX_DIST) * QUANT) * MAX_LINES_F + v_id;\n"
    "    key = v_vertical > 0.5 ? vec2(k, EMPTY) : vec2(EMPTY, k);\n"
    "}\n";

static const char *COMPOSITE_VS =
    "#version 330\n"
    "void main() {\n"
    "    vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);\n"
    "    gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);\n"
    "}\n";

static const char *COMPOSITE_FS = HEADER
    "uniform sampler2D keys;\n"
    "uniform vec3 style[MAX_LINES];       // width, value, sat\n"
    "uniform vec3 background;\n"
    "uniform float hue;                   // 0-1, plain tint\n"
    "uniform int mode;                    // 0 tint, 1 wave, 2 ripple, 3 cycle\n"
    "uniform float phase;\n"
    "uniform vec2 wave_vec;\n"
    "uniform vec2 center;\n"
    "uniform float lam;\n"
    "uniform float boost;\n"
    "uniform float min_sat;\n"
    "uniform int stops;\n"
    "uniform bool to_screen;\n"
    "uniform float height;\n"
    "out vec4 frag;\n"
    "vec3 hsv(float h, float s, float v) {\n"
    "    vec3 k = clamp(abs(mod(h * 6.0 + vec3(0.0, 4.0, 2.0), 6.0) - 3.0) - 1.0, 0.0, 1.0);\n"
    "    return v * mix(vec3(1.0), k, s);\n"
    "}\n"
    "vec3 rainbow(float param, float s, float v) {\n"
    "    float k = fract(param) * float(stops);\n"
    "    float k0 = floor(k);\n"
    "    vec3 c0 = hsv(fract(k0 / float(stops) - phase), s, v);\n"
    "    vec3 c1 = hsv(fract((k0 + 1.0) / float(stops) - phase), s, v);\n"
    "    return mix(c0, c1, k - k0);\n"
    "}\n"
    "vec3 line_color(vec3 st, vec2 q) {\n"
    "    if (mode == 0)\n"
    "        return hsv(hue, st.z, min(st.y, 1.0));\n"
    "    float s = max(st.z, min_sat);\n"
    "    float v = min(st.y * boost, 1.0);\n"
    "    if (mode == 1)\n"
    "        return rainbow(dot(q, wave_vec) / dot(wave_vec, wave_vec), s, v);\n"
    "    if (mode == 2)\n"
    "        return rainbow(length(q - center) / lam, s, v);\n"
    "    return hsv(phase, s, v);\n"
    "}\n"
    "float coverage(float d, float w) {\n"
    "    return clamp(min(0.5, d + 0.5 * w) - max(-0.5, d - 0.5 * w), 0.0, 1.0);\n"
    "}\n"
    "vec3 layer(vec3 col, float k, vec2 q) {\n"
    "    if (k >= EMPTY_HALF) return col;\n"
    "    float id = mod(k, MAX_LINES_F);\n"
    "    float d = floor(k / MAX_LINES_F) / QUANT;\n"
    "    vec3 st = style[int(id + 0.5)];\n"
    "    return mix(col, line_color(st, q), coverage(d, st.x));\n"
    "}\n"
    "void main() {\n"
    "    vec2 q = to_screen ? vec2(gl_FragCoord.x, height - gl_FragCoord.y) : gl_FragCoord.xy;\n"
    "    vec2 k = texelFetch(keys, ivec2(q), 0).rg;\n"
    "    vec3 col = layer(background, k.r, q);\n"
    "    col = layer(col, k.g, q);\n"
    "    frag = to_screen ? vec4(col, 1.0) : vec4(col.bgr, 1.0);\n"
    "}\n";

static const char *OVERLAY_VS =
    "#version 330\n"
    "uniform vec4 rect;      // x0, y0, x1, y1 in NDC\n"
    "out vec2 uv;\n"
    "void main() {\n"
    "    vec2 c = vec2(gl_VertexID & 1, (gl_VertexID >> 1) & 1);\n"
    "    uv = vec2(c.x, 1.0 - c.y);\n"
    "    gl_Position = vec4(mix(rect.xy, rect.zw, c), 0.0, 1.0);\n"
    "}\n";

static const char *OVERLAY_FS =
    "#version 330\n"
    "uniform sampler2D tex;  // premultiplied ARGB32 from cairo, uploaded as BGRA\n"
    "in vec2 uv;\n"
    "out vec4 frag;\n"
    "void main() { frag = texture(tex, uv); }\n";

static GLuint compile(GLenum type, const char *src)
{
    GLuint s = glCreateShader(type);
    glShaderSource(s, 1, &src, NULL);
    glCompileShader(s);
    GLint ok;
    glGetShaderiv(s, GL_COMPILE_STATUS, &ok);
    if (!ok) {
        char log[2048];
        glGetShaderInfoLog(s, sizeof log, NULL, log);
        fprintf(stderr, "shader compile error:\n%s\n", log);
        glDeleteShader(s);
        return 0;
    }
    return s;
}

static GLuint program(const char *vs, const char *fs)
{
    GLuint v = compile(GL_VERTEX_SHADER, vs), f = compile(GL_FRAGMENT_SHADER, fs);
    if (!v || !f)
        return 0;
    GLuint p = glCreateProgram();
    glAttachShader(p, v);
    glAttachShader(p, f);
    glLinkProgram(p);
    glDeleteShader(v);
    glDeleteShader(f);
    GLint ok;
    glGetProgramiv(p, GL_LINK_STATUS, &ok);
    if (!ok) {
        char log[2048];
        glGetProgramInfoLog(p, sizeof log, NULL, log);
        fprintf(stderr, "shader link error:\n%s\n", log);
        glDeleteProgram(p);
        return 0;
    }
    return p;
}

int wm_gl_init(WmGl *g)
{
    memset(g, 0, sizeof *g);
    g->geo = program(GEOMETRY_VS, GEOMETRY_FS);
    g->comp = program(COMPOSITE_VS, COMPOSITE_FS);
    g->ovl = program(OVERLAY_VS, OVERLAY_FS);
    if (!g->geo || !g->comp || !g->ovl)
        return -1;
#define U(p, n) glGetUniformLocation(p, n)
    g->ug.size = U(g->geo, "size");
    g->ug.n_lenses = U(g->geo, "n_lenses");
    g->ug.lenses = U(g->geo, "lenses");
    g->ug.half_width = U(g->geo, "half_width");
    g->ug.n_vertical = U(g->geo, "n_vertical");
    g->uc.keys = U(g->comp, "keys");
    g->uc.style = U(g->comp, "style");
    g->uc.background = U(g->comp, "background");
    g->uc.hue = U(g->comp, "hue");
    g->uc.mode = U(g->comp, "mode");
    g->uc.phase = U(g->comp, "phase");
    g->uc.wave_vec = U(g->comp, "wave_vec");
    g->uc.center = U(g->comp, "center");
    g->uc.lam = U(g->comp, "lam");
    g->uc.boost = U(g->comp, "boost");
    g->uc.min_sat = U(g->comp, "min_sat");
    g->uc.stops = U(g->comp, "stops");
    g->uc.to_screen = U(g->comp, "to_screen");
    g->uc.height = U(g->comp, "height");
    g->uo.rect = U(g->ovl, "rect");
    g->uo.tex = U(g->ovl, "tex");
#undef U
    glGenVertexArrays(1, &g->empty_vao);
    return 0;
}

void wm_gl_destroy(WmGl *g)
{
    glDeleteProgram(g->geo);
    glDeleteProgram(g->comp);
    glDeleteProgram(g->ovl);
    glDeleteVertexArrays(1, &g->empty_vao);
    if (g->geo_vao)
        glDeleteVertexArrays(1, &g->geo_vao);
    if (g->seg_buf)
        glDeleteBuffers(1, &g->seg_buf);
    if (g->id_buf)
        glDeleteBuffers(1, &g->id_buf);
    if (g->key_fbo)
        glDeleteFramebuffers(1, &g->key_fbo);
    if (g->key_tex)
        glDeleteTextures(1, &g->key_tex);
    memset(g, 0, sizeof *g);
}

static int targets(WmGl *g, int w, int h)
{
    if (g->key_tex && g->tex_w == w && g->tex_h == h)
        return 0;
    if (g->key_fbo)
        glDeleteFramebuffers(1, &g->key_fbo);
    if (g->key_tex)
        glDeleteTextures(1, &g->key_tex);
    glGenTextures(1, &g->key_tex);
    glBindTexture(GL_TEXTURE_2D, g->key_tex);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RG32F, w, h, 0, GL_RG, GL_FLOAT, NULL);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glGenFramebuffers(1, &g->key_fbo);
    glBindFramebuffer(GL_FRAMEBUFFER, g->key_fbo);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, g->key_tex, 0);
    GLenum st = glCheckFramebufferStatus(GL_FRAMEBUFFER);
    glBindFramebuffer(GL_FRAMEBUFFER, 0);
    if (st != GL_FRAMEBUFFER_COMPLETE) {
        fprintf(stderr, "RG32F framebuffer incomplete (0x%x)\n", st);
        return -1;
    }
    g->tex_w = w;
    g->tex_h = h;
    return 0;
}

static void grid(WmGl *g, const WmScene *s)
{
    if (g->geo_vao && g->grid_w == s->width && g->grid_h == s->height &&
        g->grid_spacing == s->spacing && g->grid_offset[0] == s->offset[0] &&
        g->grid_offset[1] == s->offset[1])
        return;
    size_t n = wm_grid_segments(s, WM_GL_STEP, NULL, NULL);
    float *segs = malloc(n * 4 * sizeof(float)), *ids = malloc(n * sizeof(float));
    wm_grid_segments(s, WM_GL_STEP, segs, ids);
    if (!g->geo_vao) {
        glGenVertexArrays(1, &g->geo_vao);
        glGenBuffers(1, &g->seg_buf);
        glGenBuffers(1, &g->id_buf);
    }
    glBindVertexArray(g->geo_vao);
    GLint seg_loc = glGetAttribLocation(g->geo, "seg"), id_loc = glGetAttribLocation(g->geo, "line_id");
    glBindBuffer(GL_ARRAY_BUFFER, g->seg_buf);
    glBufferData(GL_ARRAY_BUFFER, n * 4 * sizeof(float), segs, GL_STATIC_DRAW);
    glEnableVertexAttribArray(seg_loc);
    glVertexAttribPointer(seg_loc, 4, GL_FLOAT, GL_FALSE, 0, 0);
    glVertexAttribDivisor(seg_loc, 1);
    glBindBuffer(GL_ARRAY_BUFFER, g->id_buf);
    glBufferData(GL_ARRAY_BUFFER, n * sizeof(float), ids, GL_STATIC_DRAW);
    glEnableVertexAttribArray(id_loc);
    glVertexAttribPointer(id_loc, 1, GL_FLOAT, GL_FALSE, 0, 0);
    glVertexAttribDivisor(id_loc, 1);
    glBindBuffer(GL_ARRAY_BUFFER, 0);
    glBindVertexArray(0);
    free(segs);
    free(ids);
    g->n_segs = n;
    g->grid_w = s->width;
    g->grid_h = s->height;
    g->grid_spacing = s->spacing;
    g->grid_offset[0] = s->offset[0];
    g->grid_offset[1] = s->offset[1];
}

int wm_gl_render(WmGl *g, const WmScene *s, const WmRainbow *rb)
{
    static WmLineStyle styles[WM_GL_MAX_LINES];
    int w = s->width, h = s->height;
    int n = wm_line_styles(s, WM_GL_BEND_STEP, styles, WM_GL_MAX_LINES);
    if (n < 0) {
        fprintf(stderr, "too many grid lines for the GL backend\n");
        return -1;
    }
    if (targets(g, w, h))
        return -1;
    grid(g, s);

    /* pass 1: nearest-line distances, MIN-blended */
    glBindFramebuffer(GL_FRAMEBUFFER, g->key_fbo);
    glViewport(0, 0, w, h);
    glClearColor(EMPTY, EMPTY, 0, 0);
    glClear(GL_COLOR_BUFFER_BIT);
    glEnable(GL_BLEND);
    glBlendEquation(GL_MIN);
    glUseProgram(g->geo);
    glUniform2f(g->ug.size, (float)w, (float)h);
    int n_vertical = 0;
    float half[WM_GL_MAX_LINES] = {0};
    float style[WM_GL_MAX_LINES][3] = {{0}};
    for (int i = 0; i < n; i++) {
        n_vertical += styles[i].vertical;
        half[i] = (float)(styles[i].width / 2);
        style[i][0] = (float)styles[i].width;
        style[i][1] = (float)styles[i].value;
        style[i][2] = (float)styles[i].sat;
    }
    glUniform1f(g->ug.n_vertical, (float)n_vertical);
    float lenses[WM_MAX_LENSES][4] = {{0}};
    for (int i = 0; i < s->n_lenses; i++) {
        lenses[i][0] = (float)s->lenses[i].cx;
        lenses[i][1] = (float)s->lenses[i].cy;
        lenses[i][2] = (float)s->lenses[i].radius;
        lenses[i][3] = (float)s->lenses[i].strength;
    }
    glUniform1i(g->ug.n_lenses, s->n_lenses);
    glUniform4fv(g->ug.lenses, WM_MAX_LENSES, &lenses[0][0]);
    glUniform1fv(g->ug.half_width, WM_GL_MAX_LINES, half);
    glBindVertexArray(g->geo_vao);
    glDrawArraysInstanced(GL_TRIANGLE_STRIP, 0, 4, (GLsizei)g->n_segs);
    glDisable(GL_BLEND);
    glBlendEquation(GL_FUNC_ADD);

    /* pass 2: composite onto the window */
    glUseProgram(g->comp);
    glUniform3fv(g->uc.style, WM_GL_MAX_LINES, &style[0][0]);
    glUniform3f(g->uc.background, (float)WM_BACKGROUND[0], (float)WM_BACKGROUND[1],
                (float)WM_BACKGROUND[2]);
    glUniform1f(g->uc.hue, (float)py_fmod(s->hue / 360, 1.0));
    int mode = rb ? (int)rb->mode : 0;
    glUniform1i(g->uc.mode, mode);
    if (mode) {
        glUniform1f(g->uc.phase, (float)rb->phase);
        glUniform1f(g->uc.boost, (float)WM_RAINBOW_BOOST);
        glUniform1f(g->uc.min_sat, (float)WM_RAINBOW_SAT);
        glUniform1i(g->uc.stops, WM_RAINBOW_STOPS);
        if (mode == WM_RAINBOW_WAVE)
            glUniform2f(g->uc.wave_vec, (float)rb->wave_vec[0], (float)rb->wave_vec[1]);
        else if (mode == WM_RAINBOW_RIPPLE) {
            glUniform2f(g->uc.center, (float)rb->center[0], (float)rb->center[1]);
            glUniform1f(g->uc.lam, (float)rb->lam);
        }
    }
    glActiveTexture(GL_TEXTURE0);
    glBindTexture(GL_TEXTURE_2D, g->key_tex);
    glUniform1i(g->uc.keys, 0);
    glUniform1i(g->uc.to_screen, 1);
    glUniform1f(g->uc.height, (float)h);
    glBindFramebuffer(GL_FRAMEBUFFER, 0);
    glViewport(0, 0, w, h);
    glBindVertexArray(g->empty_vao);
    glDrawArrays(GL_TRIANGLES, 0, 3);
    glBindVertexArray(0);
    return 0;
}

void wm_gl_overlay(WmGl *g, GLuint tex, int tw, int th, int x, int y, int w, int h)
{
    float x0 = (float)x / w * 2 - 1, x1 = (float)(x + tw) / w * 2 - 1;
    float y0 = 1 - (float)(y + th) / h * 2, y1 = 1 - (float)y / h * 2;   /* NDC y runs up */
    glUseProgram(g->ovl);
    glUniform4f(g->uo.rect, x0, y0, x1, y1);
    glActiveTexture(GL_TEXTURE0);
    glBindTexture(GL_TEXTURE_2D, tex);
    glUniform1i(g->uo.tex, 0);
    glEnable(GL_BLEND);
    glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA);
    glBindVertexArray(g->empty_vao);
    glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
    glBindVertexArray(0);
    glDisable(GL_BLEND);
}

void wm_status_strip_draw(WmStatusStrip *ss, WmGl *g, WmStatus *st, double hue, int w, int h)
{
    wm_status_update(st);
    double font = wm_status_font_size(h);
    int sh = (int)(font * 3.5);
    long rhue = lround(hue);
    if (!ss->tex || strcmp(ss->key_text, st->text) || ss->key_hue != rhue || ss->key_w != w ||
        ss->key_h != h) {
        cairo_surface_t *img = cairo_image_surface_create(CAIRO_FORMAT_ARGB32, w, sh);
        cairo_t *cr = cairo_create(img);
        wm_status_draw(st, cr, w, sh, hue, font);
        cairo_destroy(cr);
        cairo_surface_flush(img);
        if (!ss->tex || ss->tw != w || ss->th != sh) {
            if (ss->tex)
                glDeleteTextures(1, &ss->tex);
            glGenTextures(1, &ss->tex);
            glBindTexture(GL_TEXTURE_2D, ss->tex);
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, w, sh, 0, GL_BGRA, GL_UNSIGNED_BYTE, NULL);
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
            ss->tw = w;
            ss->th = sh;
        }
        glBindTexture(GL_TEXTURE_2D, ss->tex);
        glPixelStorei(GL_UNPACK_ROW_LENGTH, cairo_image_surface_get_stride(img) / 4);
        glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, w, sh, GL_BGRA, GL_UNSIGNED_BYTE,
                        cairo_image_surface_get_data(img));
        glPixelStorei(GL_UNPACK_ROW_LENGTH, 0);
        cairo_surface_destroy(img);
        snprintf(ss->key_text, sizeof ss->key_text, "%s", st->text);
        ss->key_hue = rhue;
        ss->key_w = w;
        ss->key_h = h;
    }
    wm_gl_overlay(g, ss->tex, ss->tw, ss->th, 0, h - sh, w, h);
}

void wm_status_strip_destroy(WmStatusStrip *ss)
{
    if (ss->tex)
        glDeleteTextures(1, &ss->tex);
    memset(ss, 0, sizeof *ss);
}
