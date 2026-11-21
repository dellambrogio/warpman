/* CPython-compatible random.Random (MT19937), so that the same seed gives the same
 * scenes and motion as the Python warpman (warpman/drift.py, warpman/scene.py).
 *
 * Only what warpman uses is ported: seeding from an int, random(), getrandbits() for
 * k <= 32, randbelow() (CPython's _randbelow_with_getrandbits), choice index, uniform()
 * and gauss() with its cached second value.
 */
#pragma once

#include <stdint.h>

typedef struct {
    uint32_t mt[624];
    int mti;
    int has_gauss_next;
    double gauss_next;
} PyRandom;

/* random.Random(n) / .seed(n) for an integer n */
void py_seed(PyRandom *r, int64_t n);
/* seed from the OS (like an unseeded random.Random()) */
void py_seed_os(PyRandom *r);

uint32_t py_genrand_uint32(PyRandom *r);
double py_random(PyRandom *r);                      /* [0, 1), 53 bits */
uint32_t py_getrandbits(PyRandom *r, int k);        /* 1 <= k <= 32 */
uint64_t py_randbelow(PyRandom *r, uint64_t n);     /* [0, n), n < 2^32 */
double py_uniform(PyRandom *r, double a, double b);
double py_gauss(PyRandom *r, double mu, double sigma);

/* Python's float % (result has the sign of the divisor) */
double py_fmod(double a, double b);
