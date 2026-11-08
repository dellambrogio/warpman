"""warpman GL backend - renders a warped grid on the GPU (headless EGL via moderngl).

The look matches warpman.draw (the cairo backend): the same 4 px polylines, warped
by the same lens formula, with widths, brightness and saturation from
warpman.line_styles, so both backends agree on every line, including where
overlapping lenses fold the grid.

The undisplaced segments only change with the scene's size and spacing, so they are
uploaded once; the vertex shader warps them every frame from the lens uniforms. The
CPU just picks each line's style, from a coarser sample (BEND_STEP) of the warp.

Two passes per frame:
  1. Geometry: every polyline segment is drawn as an instanced quad. Its fragment
     shader writes "distance to the segment, plus line id" into a float texture
     with MIN blending, so each pixel ends up holding its distance to the nearest
     vertical line (red channel) and nearest horizontal line (green channel).
     The minimum over a line's segments is exactly cairo's round-joined stroke.
  2. Composite: a full-screen pass turns those distances into antialiased coverage
     and composites like cairo: background, vertical lines, horizontal lines on top.
     Line colour (plain tint or rainbow) is computed per pixel here.

Offscreen (default): frames are read back as BGRX bytes, the same layout as a cairo
FORMAT_RGB24 surface, so callers can wrap them in a cairo surface or stream them.
On screen (warpman_layer.py): pass an existing context and a target framebuffer;
the composite then writes RGB with the image flipped to GL's bottom-up rows, and a
small cairo-drawn overlay (the status text) can be blended on top.
"""

import moderngl
import numpy as np

import warpman as wm

MAX_LINES = 256     # vertical + horizontal; 8K at the smallest spacing needs ~120
QUANT = 64.0        # distance resolution: 1/64 px
MAX_DIST = 8.0      # distances beyond this (px) count as "no line here"
EMPTY = 1e9         # cleared value; decodes to a distance well past MAX_DIST
MAX_LENSES = 16
STEP = 4.0          # polyline sample spacing in px, as in warpman.draw
BEND_STEP = 16.0    # coarser sampling for the per-line width/brightness on the CPU

GEOMETRY_VS = f"""
#version 330
uniform vec2 size;
uniform int n_lenses;
uniform vec4 lenses[{MAX_LENSES}];    // cx, cy, radius, strength
uniform float half_width[{MAX_LINES}];
uniform float n_vertical;
in vec4 seg;          // undisplaced x0, y0, x1, y1 (image pixels, y down)
in float line_id;
out vec2 v_a;
out vec2 v_b;
flat out float v_id;
flat out float v_vertical;

// warpman.warp_array: s*R*t*(1-t^2)^2 along d/r, which simplifies to d * s*(1 - r^2/R^2)^2
vec2 warp(vec2 p) {{
    vec2 o = vec2(0.0);
    for (int i = 0; i < n_lenses; i++) {{
        vec4 l = lenses[i];
        vec2 d = p - l.xy;
        float r2 = dot(d, d);
        float R2 = l.z * l.z;
        if (r2 < R2) {{
            float u = 1.0 - r2 / R2;
            o += d * (l.w * u * u);
        }}
    }}
    return p + o;
}}

void main() {{
    vec2 a = warp(seg.xy), b = warp(seg.zw);
    vec2 d = b - a;
    float len = length(d);
    vec2 t = len > 0.0 ? d / len : vec2(1.0, 0.0);
    vec2 n = vec2(-t.y, t.x);
    float e = half_width[int(line_id)] + 1.5;   // half width + room for antialiasing
    // corners 0..3 of a quad around the segment, extended past both ends for round caps
    float along = (gl_VertexID & 1) == 0 ? -e : len + e;
    float side = (gl_VertexID & 2) == 0 ? -e : e;
    vec2 p = a + t * along + n * side;
    v_a = a;
    v_b = b;
    v_id = line_id;
    v_vertical = line_id < n_vertical ? 1.0 : 0.0;
    // image y runs down and the readback starts at GL row 0, so no y flip is needed
    gl_Position = vec4(p / size * 2.0 - 1.0, 0.0, 1.0);
}}
"""

GEOMETRY_FS = f"""
#version 330
in vec2 v_a;
in vec2 v_b;
flat in float v_id;
flat in float v_vertical;
out vec2 key;
void main() {{
    vec2 p = gl_FragCoord.xy;
    vec2 ab = v_b - v_a;
    float h = clamp(dot(p - v_a, ab) / max(dot(ab, ab), 1e-12), 0.0, 1.0);
    float dist = length(p - v_a - ab * h);
    // pack quantized distance and line id into one float so MIN keeps both together
    float k = floor(min(dist, {MAX_DIST}) * {QUANT}) * {MAX_LINES}.0 + v_id;
    key = v_vertical > 0.5 ? vec2(k, {EMPTY}) : vec2({EMPTY}, k);
}}
"""

COMPOSITE_VS = """
#version 330
void main() {
    // one triangle that covers the whole viewport
    vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
    gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}
"""

COMPOSITE_FS = f"""
#version 330
#define MAX_LINES {MAX_LINES}
uniform sampler2D keys;
uniform vec3 style[MAX_LINES];       // width, value, sat
uniform vec3 background;
uniform float hue;                   // 0-1, plain tint
uniform int mode;                    // 0 tint, 1 wave, 2 ripple, 3 cycle
uniform float phase;
uniform vec2 wave_vec;               // wave: gradient end point (start is 0,0)
uniform vec2 center;                 // ripple centre
uniform float lam;                   // ripple wavelength
uniform float boost;
uniform float min_sat;
uniform int stops;
uniform bool to_screen;              // window framebuffer: flip rows, RGB order
uniform float height;
out vec4 frag;

vec3 hsv(float h, float s, float v) {{
    vec3 k = clamp(abs(mod(h * 6.0 + vec3(0.0, 4.0, 2.0), 6.0) - 3.0) - 1.0, 0.0, 1.0);
    return v * mix(vec3(1.0), k, s);
}}

// cairo-style gradient: `stops` hue samples per period, linearly mixed in RGB
vec3 rainbow(float param, float s, float v) {{
    float k = fract(param) * float(stops);
    float k0 = floor(k);
    vec3 c0 = hsv(fract(k0 / float(stops) - phase), s, v);
    vec3 c1 = hsv(fract((k0 + 1.0) / float(stops) - phase), s, v);
    return mix(c0, c1, k - k0);
}}

vec3 line_color(vec3 st, vec2 q) {{
    if (mode == 0)
        return hsv(hue, st.z, min(st.y, 1.0));
    float s = max(st.z, min_sat);
    float v = min(st.y * boost, 1.0);
    if (mode == 1)
        return rainbow(dot(q, wave_vec) / dot(wave_vec, wave_vec), s, v);
    if (mode == 2)
        return rainbow(length(q - center) / lam, s, v);
    return hsv(phase, s, v);
}}

// coverage of a line `w` px wide whose centre is `d` px from this pixel's centre
float coverage(float d, float w) {{
    return clamp(min(0.5, d + 0.5 * w) - max(-0.5, d - 0.5 * w), 0.0, 1.0);
}}

vec3 layer(vec3 col, float k, vec2 q) {{
    if (k >= {EMPTY * 0.5}) return col;
    float id = mod(k, {MAX_LINES}.0);
    float d = floor(k / {MAX_LINES}.0) / {QUANT};
    vec3 st = style[int(id + 0.5)];
    return mix(col, line_color(st, q), coverage(d, st.x));
}}

void main() {{
    // q is in image pixels (y down). Offscreen, GL row y is image row y (the readback
    // starts at row 0); on screen, row 0 is the bottom, so flip.
    vec2 q = to_screen ? vec2(gl_FragCoord.x, height - gl_FragCoord.y) : gl_FragCoord.xy;
    vec2 k = texelFetch(keys, ivec2(q), 0).rg;
    vec3 col = layer(background, k.r, q);   // vertical lines first,
    col = layer(col, k.g, q);               // horizontal lines on top, as in cairo
    // offscreen: BGRX byte order, like cairo FORMAT_RGB24
    frag = to_screen ? vec4(col, 1.0) : vec4(col.bgr, 1.0);
}}
"""

OVERLAY_VS = """
#version 330
uniform vec4 rect;      // x0, y0, x1, y1 in NDC
out vec2 uv;
void main() {
    vec2 c = vec2(gl_VertexID & 1, (gl_VertexID >> 1) & 1);
    uv = vec2(c.x, 1.0 - c.y);          // texture row 0 is the top of the cairo image
    gl_Position = vec4(mix(rect.xy, rect.zw, c), 0.0, 1.0);
}
"""

OVERLAY_FS = """
#version 330
uniform sampler2D tex;  // premultiplied ARGB32 from cairo, swizzled to RGBA
in vec2 uv;
out vec4 frag;
void main() { frag = texture(tex, uv); }
"""

MODES = {None: 0, "wave": 1, "ripple": 2, "cycle": 3}


def grid_segments(scene: wm.Scene):
    """Undisplaced segments of every grid line, (x0, y0, x1, y1), and their line ids."""
    segs, ids = [], []
    lines = wm.grid_arrays(scene.width, scene.height, scene.spacing, scene.offset, STEP)
    for i, (_, xs, ys) in enumerate(lines):
        pts = np.column_stack((xs, ys))
        segs.append(np.hstack((pts[:-1], pts[1:])))
        ids.append(np.full(len(pts) - 1, i))
    return np.concatenate(segs).astype("f4"), np.concatenate(ids).astype("f4")


class GLRenderer:
    """Offscreen GPU renderer. Raises if no GL 3.3 context can be created."""

    def __init__(self, ctx: "moderngl.Context | None" = None):
        self.ctx = ctx or moderngl.create_standalone_context(backend="egl")
        self.geo = self.ctx.program(vertex_shader=GEOMETRY_VS, fragment_shader=GEOMETRY_FS)
        self.comp = self.ctx.program(vertex_shader=COMPOSITE_VS, fragment_shader=COMPOSITE_FS)
        self.comp_vao = self.ctx.vertex_array(self.comp, [])
        self.ovl = self.ctx.program(vertex_shader=OVERLAY_VS, fragment_shader=OVERLAY_FS)
        self.ovl_vao = self.ctx.vertex_array(self.ovl, [])
        self.seg_buf = self.id_buf = self.geo_vao = None
        self.grid_key = None        # (size, spacing, offset) the uploaded segments belong to
        self.n_segs = 0
        self.size = None

    def _targets(self, w: int, h: int):
        if self.size == (w, h):
            return
        if self.size:
            for o in (self.key_tex, self.key_fbo, self.out_fbo):
                if o:
                    o.release()
        self.key_tex = self.ctx.texture((w, h), 2, dtype="f4")
        self.key_fbo = self.ctx.framebuffer(color_attachments=[self.key_tex])
        self.out_fbo = self.buf = None              # offscreen output, made on first use
        self.size = (w, h)

    def _grid(self, scene: wm.Scene):
        """Upload the undisplaced segments, but only when the grid itself changed."""
        key = (scene.width, scene.height, scene.spacing, tuple(scene.offset))
        if key == self.grid_key:
            return
        segs, ids = grid_segments(scene)
        for o in (self.seg_buf, self.id_buf, self.geo_vao):
            if o:
                o.release()
        self.seg_buf = self.ctx.buffer(segs.tobytes())
        self.id_buf = self.ctx.buffer(ids.tobytes())
        self.geo_vao = self.ctx.vertex_array(self.geo, [
            (self.seg_buf, "4f/i", "seg"), (self.id_buf, "1f/i", "line_id")])
        self.n_segs = len(segs)
        self.grid_key = key

    def _set_array(self, prog, name, rows, width: int, length: int):
        """Fill a float/vecN uniform array from rows, zero-padded to length, in one write."""
        if name in prog:
            a = np.zeros((length, width), "f4")
            if rows:
                a[:len(rows)] = rows
            prog[name].write(a.tobytes())

    def _set(self, prog, name, value):
        if name in prog:                    # the GLSL compiler drops unused uniforms
            prog[name].value = value

    def render(self, scene: wm.Scene, rainbow=None, target=None) -> bytearray | None:
        """Render scene; returns a reused bytearray of BGRX rows, top row first.

        rainbow is a warpman_drift.Rainbow (already updated for this frame) or None.
        With target (a moderngl framebuffer, e.g. the window's), draws there instead
        and returns None; the caller presents it (eglSwapBuffers).
        """
        w, h = scene.width, scene.height
        styles = wm.line_styles(scene, step=BEND_STEP)
        if len(styles) > MAX_LINES:
            raise ValueError(f"too many grid lines for the GL backend ({len(styles)})")
        if len(scene.lenses) > MAX_LENSES:
            raise ValueError(f"the GL backend supports at most {MAX_LENSES} lenses")
        self._targets(w, h)
        self._grid(scene)
        ctx = self.ctx

        # pass 1: nearest-line distances, MIN-blended
        self.key_fbo.use()
        self.key_fbo.clear(EMPTY, EMPTY, 0.0, 0.0)
        ctx.enable(moderngl.BLEND)
        ctx.blend_equation = moderngl.MIN
        self._set(self.geo, "size", (w, h))
        self._set(self.geo, "n_vertical", float(sum(1 for l in styles if l.vertical)))
        lens_data = [(l.cx, l.cy, l.radius, l.strength) for l in scene.lenses]
        self._set(self.geo, "n_lenses", len(lens_data))
        self._set_array(self.geo, "lenses", lens_data, 4, MAX_LENSES)
        self._set_array(self.geo, "half_width", [(l.width / 2,) for l in styles], 1, MAX_LINES)
        self.geo_vao.render(moderngl.TRIANGLE_STRIP, vertices=4, instances=self.n_segs)
        ctx.disable(moderngl.BLEND)
        ctx.blend_equation = moderngl.FUNC_ADD

        # pass 2: composite to BGRX
        c = self.comp
        self._set_array(c, "style", [(l.width, l.value, l.sat) for l in styles], 3, MAX_LINES)
        self._set(c, "background", wm.BACKGROUND)
        self._set(c, "hue", (scene.hue / 360) % 1.0)
        r = rainbow
        self._set(c, "mode", MODES[r.mode] if r else 0)
        if r:
            self._set(c, "phase", r.phase)
            self._set(c, "boost", r.BOOST)
            self._set(c, "min_sat", r.SAT)
            self._set(c, "stops", r.STOPS)
            if r.mode == "wave":
                self._set(c, "wave_vec", r.wave_vec)
            elif r.mode == "ripple":
                self._set(c, "center", r.center)
                self._set(c, "lam", r.lam)
        self.key_tex.use(0)
        self._set(c, "keys", 0)
        self._set(c, "to_screen", target is not None)
        self._set(c, "height", float(h))
        if target is not None:
            target.viewport = (0, 0, w, h)
            target.use()
            self.comp_vao.render(moderngl.TRIANGLES, vertices=3)
            return None
        if self.out_fbo is None:
            self.out_fbo = ctx.simple_framebuffer((w, h), components=4)
            self.buf = bytearray(w * h * 4)
        self.out_fbo.use()
        self.comp_vao.render(moderngl.TRIANGLES, vertices=3)
        self.out_fbo.read_into(self.buf, components=4)
        return self.buf

    def overlay(self, tex: "moderngl.Texture", x: int, y: int, size: tuple[int, int]):
        """Blend a premultiplied texture onto the current target at image pixel (x, y)
        (top-left, y down), for a target of the given size."""
        w, h = size
        tw, th = tex.size
        x0, x1 = x / w * 2 - 1, (x + tw) / w * 2 - 1
        y0, y1 = 1 - (y + th) / h * 2, 1 - y / h * 2        # NDC y runs up
        self._set(self.ovl, "rect", (x0, y0, x1, y1))
        tex.use(0)
        self._set(self.ovl, "tex", 0)
        self.ctx.enable(moderngl.BLEND)
        self.ctx.blend_func = moderngl.ONE, moderngl.ONE_MINUS_SRC_ALPHA
        self.ovl_vao.render(moderngl.TRIANGLE_STRIP, vertices=4)
        self.ctx.disable(moderngl.BLEND)
