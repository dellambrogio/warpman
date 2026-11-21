/* Parity with the Python warpman: replays the runs in golden.txt (see gen_golden.py)
 * and compares every value. Bit-exact results are expected (same libm, no FMA
 * contraction); anything beyond 1e-9 relative fails the test. */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "drift.h"
#include "pyrandom.h"
#include "scene.h"

static long compared, inexact, failed;
static double worst;
static int line_no;

static void check(const char *what, double got, double want)
{
    compared++;
    if (got == want)
        return;
    inexact++;
    double rel = fabs(got - want) / fmax(fabs(want), 1e-300);
    if (rel > worst)
        worst = rel;
    if (rel > 1e-9) {
        if (failed < 20)
            fprintf(stderr, "line %d: %s: got %.17g want %.17g\n", line_no, what, got, want);
        failed++;
    }
}

/* split a line into whitespace-separated tokens */
static int tokens(char *line, char **tok, int max)
{
    int n = 0;
    for (char *p = strtok(line, " \t\n"); p && n < max; p = strtok(NULL, " \t\n"))
        tok[n++] = p;
    return n;
}

static FILE *golden;
static char buf[4096];
static char *tok[64];

static int next_line(void)
{
    if (!fgets(buf, sizeof buf, golden))
        return -1;
    line_no++;
    return tokens(buf, tok, 64);
}

static int expect(const char *tag)
{
    int n = next_line();
    if (n < 1 || strcmp(tok[0], tag)) {
        fprintf(stderr, "line %d: expected '%s', got '%s'\n", line_no, tag, n > 0 ? tok[0] : "EOF");
        exit(1);
    }
    return n;
}

static double num(int i)
{
    return strtod(tok[i], NULL);
}

static void check_styles(const WmScene *s)
{
    WmLineStyle st[1024];
    int n = expect("styles");
    (void)n;
    int want_n = atoi(tok[2]);
    int got_n = wm_line_styles(s, 16.0, st, 1024);
    check("line count", got_n, want_n);
    for (int i = 0; i < want_n; i++) {
        expect("style");
        if (i >= got_n)
            continue;
        check("vertical", st[i].vertical, num(1));
        check("width", st[i].width, num(2));
        check("value", st[i].value, num(3));
        check("sat", st[i].sat, num(4));
    }
}

static void run_case(void)
{
    /* case name w h lenses hue sat steps resize_at rw rh seed */
    int w = atoi(tok[2]), h = atoi(tok[3]);
    WmSimArgs a = {0};
    a.speed = 60.0;
    a.lenses = atoi(tok[4]);
    if (strcmp(tok[5], "-")) {
        a.has_hue = 1;
        a.hue = num(5);
    }
    if (strcmp(tok[6], "-")) {
        a.has_saturation = 1;
        a.saturation = num(6);
    }
    int steps = atoi(tok[7]), resize_at = atoi(tok[8]), rw = atoi(tok[9]), rh = atoi(tok[10]);
    a.has_seed = 1;
    a.seed = strtoll(tok[11], NULL, 10);
    a.rainbow_speed = 0.1;
    char name[64];
    snprintf(name, sizeof name, "%s", tok[1]);
    long failed_before = failed;

    static WmSim sim;
    wm_sim_init(&sim, &a, w, h);
    WmScene *s = &sim.scene;
    expect("scene");
    check("spacing", s->spacing, num(1));
    check("hue", s->hue, num(2));
    check("saturation", s->saturation, num(3));
    int nl = atoi(tok[4]);
    check("lens count", s->n_lenses, nl);
    for (int i = 0; i < nl; i++) {
        expect("lens");
        check("cx", s->lenses[i].cx, num(1));
        check("cy", s->lenses[i].cy, num(2));
        check("radius", s->lenses[i].radius, num(3));
        check("strength", s->lenses[i].strength, num(4));
    }
    check_styles(s);
    const double dt = 1.0 / 24;
    for (int i = 1; i <= steps; i++) {
        wm_sim_step(&sim, dt);
        if (resize_at && i == resize_at)
            wm_sim_resize(&sim, rw, rh);
        if (i % 100 == 0) {
            expect("state");
            check("t", sim.t, num(2));
            for (int k = 0; k < s->n_lenses; k++) {
                expect("lens");
                check("cx", s->lenses[k].cx, num(1));
                check("cy", s->lenses[k].cy, num(2));
                check("radius", s->lenses[k].radius, num(3));
                check("strength", s->lenses[k].strength, num(4));
                expect("drift");
                check("heading", sim.drifters[k].heading, num(1));
                check("turn", sim.drifters[k].turn, num(2));
            }
        }
        if (i % 600 == 0)
            check_styles(s);
    }
    expect("end");
    printf("case %-8s %s\n", name, failed == failed_before ? "ok" : "FAILED");
}

static void run_rand(void)
{
    PyRandom r;
    long long seed = strtoll(tok[1], NULL, 10);
    py_seed(&r, seed);
    int k = 2;
    for (int i = 0; i < 4; i++)
        check("random", py_random(&r), num(k++));
    for (int i = 0; i < 3; i++)
        check("gauss", py_gauss(&r, 0, 1.2), num(k++));
    for (int i = 0; i < 4; i++)
        check("randbelow", (double)py_randbelow(&r, 5), num(k++));
    for (int i = 0; i < 2; i++)
        check("randrange", (double)py_randbelow(&r, 1u << 30), num(k++));
    check("uniform", py_uniform(&r, 0.85, 1.15), num(k++));
}

int main(int argc, char **argv)
{
    const char *path = argc > 1 ? argv[1] : "golden.txt";
    golden = fopen(path, "r");
    if (!golden) {
        perror(path);
        return 1;
    }
    int n;
    while ((n = next_line()) >= 0) {
        if (n == 0)
            continue;
        if (!strcmp(tok[0], "rand"))
            run_rand();
        else if (!strcmp(tok[0], "case"))
            run_case();
        else {
            fprintf(stderr, "line %d: unexpected '%s'\n", line_no, tok[0]);
            return 1;
        }
    }
    printf("%ld values compared, %ld not bit-exact (worst relative error %.3g), %ld failed\n",
           compared, inexact, worst, failed);
    return failed ? 1 : 0;
}
