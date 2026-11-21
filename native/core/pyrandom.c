#include "pyrandom.h"

#include <math.h>
#include <stdlib.h>
#include <sys/random.h>
#include <time.h>

#define N 624
#define M 397
#define MATRIX_A 0x9908b0dfU
#define UPPER_MASK 0x80000000U
#define LOWER_MASK 0x7fffffffU

static void init_genrand(PyRandom *r, uint32_t s)
{
    uint32_t *mt = r->mt;
    mt[0] = s;
    for (int i = 1; i < N; i++)
        mt[i] = 1812433253U * (mt[i - 1] ^ (mt[i - 1] >> 30)) + (uint32_t)i;
    r->mti = N;
}

static void init_by_array(PyRandom *r, const uint32_t *key, size_t len)
{
    uint32_t *mt = r->mt;
    init_genrand(r, 19650218U);
    size_t i = 1, j = 0;
    size_t k = N > len ? N : len;
    for (; k; k--) {
        mt[i] = (mt[i] ^ ((mt[i - 1] ^ (mt[i - 1] >> 30)) * 1664525U)) + key[j] + (uint32_t)j;
        i++;
        j++;
        if (i >= N) {
            mt[0] = mt[N - 1];
            i = 1;
        }
        if (j >= len)
            j = 0;
    }
    for (k = N - 1; k; k--) {
        mt[i] = (mt[i] ^ ((mt[i - 1] ^ (mt[i - 1] >> 30)) * 1566083941U)) - (uint32_t)i;
        i++;
        if (i >= N) {
            mt[0] = mt[N - 1];
            i = 1;
        }
    }
    mt[0] = 0x80000000U;
}

void py_seed(PyRandom *r, int64_t n)
{
    uint64_t u = n < 0 ? (uint64_t)0 - (uint64_t)n : (uint64_t)n;     /* abs(n) */
    uint32_t key[2];
    size_t len;
    if (u == 0) {
        key[0] = 0;
        len = 1;
    } else {
        key[0] = (uint32_t)u;
        key[1] = (uint32_t)(u >> 32);
        len = key[1] ? 2 : 1;
    }
    init_by_array(r, key, len);
    r->has_gauss_next = 0;
    r->gauss_next = 0;
}

void py_seed_os(PyRandom *r)
{
    uint32_t key[N];
    if (getrandom(key, sizeof key, 0) != (ssize_t)sizeof key) {
        struct timespec ts;
        clock_gettime(CLOCK_REALTIME, &ts);
        for (int i = 0; i < N; i++)
            key[i] = (uint32_t)(ts.tv_nsec * 2654435761u + (uint32_t)i * 40503u + (uint32_t)ts.tv_sec);
    }
    init_by_array(r, key, N);
    r->has_gauss_next = 0;
}

uint32_t py_genrand_uint32(PyRandom *r)
{
    static const uint32_t mag01[2] = {0, MATRIX_A};
    uint32_t *mt = r->mt, y;
    if (r->mti >= N) {
        int kk;
        for (kk = 0; kk < N - M; kk++) {
            y = (mt[kk] & UPPER_MASK) | (mt[kk + 1] & LOWER_MASK);
            mt[kk] = mt[kk + M] ^ (y >> 1) ^ mag01[y & 1];
        }
        for (; kk < N - 1; kk++) {
            y = (mt[kk] & UPPER_MASK) | (mt[kk + 1] & LOWER_MASK);
            mt[kk] = mt[kk + (M - N)] ^ (y >> 1) ^ mag01[y & 1];
        }
        y = (mt[N - 1] & UPPER_MASK) | (mt[0] & LOWER_MASK);
        mt[N - 1] = mt[M - 1] ^ (y >> 1) ^ mag01[y & 1];
        r->mti = 0;
    }
    y = mt[r->mti++];
    y ^= (y >> 11);
    y ^= (y << 7) & 0x9d2c5680U;
    y ^= (y << 15) & 0xefc60000U;
    y ^= (y >> 18);
    return y;
}

double py_random(PyRandom *r)
{
    uint32_t a = py_genrand_uint32(r) >> 5, b = py_genrand_uint32(r) >> 6;
    return (a * 67108864.0 + b) * (1.0 / 9007199254740992.0);
}

uint32_t py_getrandbits(PyRandom *r, int k)
{
    return py_genrand_uint32(r) >> (32 - k);
}

uint64_t py_randbelow(PyRandom *r, uint64_t n)
{
    if (n == 0)
        return 0;
    int k = 0;          /* n == 1 still draws bits, as in CPython */
    for (uint64_t v = n; v; v >>= 1)
        k++;            /* n.bit_length() */
    uint64_t x = py_getrandbits(r, k);
    while (x >= n)
        x = py_getrandbits(r, k);
    return x;
}

double py_uniform(PyRandom *r, double a, double b)
{
    return a + (b - a) * py_random(r);
}

double py_gauss(PyRandom *r, double mu, double sigma)
{
    double z;
    if (r->has_gauss_next) {
        z = r->gauss_next;
        r->has_gauss_next = 0;
    } else {
        double x2pi = py_random(r) * (2 * M_PI);
        double g2rad = sqrt(-2.0 * log(1.0 - py_random(r)));
        z = cos(x2pi) * g2rad;
        r->gauss_next = sin(x2pi) * g2rad;
        r->has_gauss_next = 1;
    }
    return mu + z * sigma;
}

double py_fmod(double a, double b)
{
    double m = fmod(a, b);
    if (m != 0) {
        if ((b < 0) != (m < 0))
            m += b;
    } else {
        m = copysign(0.0, b);
    }
    return m;
}
