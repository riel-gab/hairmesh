# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Portions: fdlibm 5.3, Copyright (C) 1993 by Sun Microsystems, Inc. All rights reserved. Developed at SunSoft, a
# Sun Microsystems, Inc. business. Permission to use, copy, modify, and distribute this software is freely granted,
# provided that this notice is preserved.
"""asin, acos, sin, cos, tan, atan, atan2, exp and cbrt as V8 computes them: ports of fdlibm 5.3 (Sun Microsystems,
1993: e_asin.c, e_acos.c, s_sin.c, s_cos.c, s_tan.c, k_sin.c, k_cos.c, k_tan.c, e_rem_pio2.c) and, for atan / atan2 /
exp / cbrt, of the FreeBSD msun versions of s_atan.c, e_atan2.c, e_exp.c and s_cbrt.c that V8's base::ieee754 carries
(its exp also returns E itself for exp(1)). Validated bit for bit against V8 by tests/test_fdlibm.py.

Not pow: V8 calls the C runtime's pow (std::pow) instead of fdlibm's. Measured on Node 24 for Windows (14/10/2026):
a port of fdlibm's e_pow.c differed from Math.pow on 1 999 of 24 256 arguments, the platform pow that Python's
math.pow and ** call on 12 (5 ECMAScript special cases the geometry never reaches, 7 at 1 ulp: Node's static C
runtime is not the system's ucrtbase.dll). So the port keeps ** / math.pow.

CPython's math functions call the platform libm (UCRT on Windows) and differ from V8 by 1 ulp on a few inputs
(asin: 450 of 227k calls in the example's straightenLoops; sin: 28 of 2121 in the synthetic scene's walls). The
algorithms below use only +, -, *, /, sqrt, truncation and word manipulation, all of which IEEE 754 defines
exactly, so the results are bit-identical to V8's. Arguments beyond 2^19 * pi/2 (never produced by the geometry)
fall back to the libm with a warning: fdlibm's large-argument reduction (__kernel_rem_pio2) is not ported.
"""
import math
import struct
import warnings

_one = 1.0
_huge = 1.0e300
_pi = 3.14159265358979311600e+00
_pio2_hi = 1.57079632679489655800e+00
_pio2_lo = 6.12323399573676603587e-17
_pio4_hi = 7.85398163397448278999e-01
# coefficients for R(x^2)
_pS0 = 1.66666666666666657415e-01
_pS1 = -3.25565818622400915405e-01
_pS2 = 2.01212532134862925881e-01
_pS3 = -4.00555345006794114027e-02
_pS4 = 7.91534994289814532176e-04
_pS5 = 3.47933107596021167570e-05
_qS1 = -2.40339491173441421878e+00
_qS2 = 2.02094576023350569471e+00
_qS3 = -6.88283971605453293030e-01
_qS4 = 7.70381505559019352791e-02


def _hi_lo(x):
    """the high and low 32-bit words of a double (__HI as a SIGNED int32, as in C; __LO unsigned)"""
    bits = struct.unpack('<Q', struct.pack('<d', x))[0]
    hx = bits >> 32
    if hx >= 0x80000000:
        hx -= 0x100000000
    return hx, bits & 0xFFFFFFFF


def _clear_lo(x):
    """__LO(x) = 0"""
    bits = struct.unpack('<Q', struct.pack('<d', x))[0] & 0xFFFFFFFF00000000
    return struct.unpack('<d', struct.pack('<Q', bits))[0]


def _hi_unsigned(x):
    return struct.unpack('<Q', struct.pack('<d', x))[0] >> 32


def _from_hi(hi):
    """a double from its high word, low word 0 (__HI(q) = hi; __LO(q) = 0)"""
    return struct.unpack('<d', struct.pack('<Q', (hi & 0xFFFFFFFF) << 32))[0]


# ---------------------------------------------------------------------------------------------------------------
# sin / cos / tan: k_sin.c, k_cos.c, k_tan.c, e_rem_pio2.c (medium arguments), s_sin.c, s_cos.c, s_tan.c
# ---------------------------------------------------------------------------------------------------------------
_S1 = -1.66666666666666324348e-01
_S2 = 8.33333333332248946124e-03
_S3 = -1.98412698298579493134e-04
_S4 = 2.75573137070700676789e-06
_S5 = -2.50507602534068634195e-08
_S6 = 1.58969099521155010221e-10
_C1 = 4.16666666666666019037e-02
_C2 = -1.38888888888741095749e-03
_C3 = 2.48015872894767294178e-05
_C4 = -2.75573143513906633035e-07
_C5 = 2.08757232129817482790e-09
_C6 = -1.13596475577881948265e-11
_T = [3.33333333333334091986e-01, 1.33333333333201242699e-01, 5.39682539762260521377e-02, 2.18694882948595424599e-02,
      8.86323982359930005737e-03, 3.59207910759131235356e-03, 1.45620945432529025516e-03, 5.88041240820264096874e-04,
      2.46463134818469906812e-04, 7.81794442939557092300e-05, 7.14072491382608190305e-05, -1.85586374855275456654e-05,
      2.59073051863633712884e-05]
_pio4 = 7.85398163397448278999e-01
_pio4lo = 3.06161699786838301793e-17
_invpio2 = 6.36619772367581382433e-01
_pio2_1 = 1.57079632673412561417e+00
_pio2_1t = 6.07710050650619224932e-11
_pio2_2 = 6.07710050630396597660e-11
_pio2_2t = 2.02226624879595063154e-21
_pio2_3 = 2.02226624871116645580e-21
_pio2_3t = 8.47842766036889956997e-32
_npio2_hw = [0x3FF921FB, 0x400921FB, 0x4012D97C, 0x401921FB, 0x401F6A7A, 0x4022D97C, 0x4025FDBB, 0x402921FB,
             0x402C463A, 0x402F6A7A, 0x4031475C, 0x4032D97C, 0x40346B9C, 0x4035FDBB, 0x40378FDB, 0x403921FB,
             0x403AB41B, 0x403C463A, 0x403DD85A, 0x403F6A7A, 0x40407E4C, 0x4041475C, 0x4042106C, 0x4042D97C,
             0x4043A28C, 0x40446B9C, 0x404534AC, 0x4045FDBB, 0x4046C6CB, 0x40478FDB, 0x404858EB, 0x404921FB]


def _kernel_sin(x, y, iy):
    # k_sin.c: sin on [-pi/4, pi/4], x + y the argument, iy = 0 when y is 0
    ix = _hi_unsigned(x) & 0x7fffffff
    if ix < 0x3e400000:                                             # |x| < 2**-27
        if int(x) == 0:
            return x
    z = x * x
    v = z * x
    r = _S2 + z * (_S3 + z * (_S4 + z * (_S5 + z * _S6)))
    if iy == 0:
        return x + v * (_S1 + z * r)
    return x - ((z * (0.5 * y - v * r) - y) - v * _S1)


def _kernel_cos(x, y):
    # k_cos.c: cos on [-pi/4, pi/4]
    ix = _hi_unsigned(x) & 0x7fffffff
    if ix < 0x3e400000:                                             # |x| < 2**-27
        if int(x) == 0:
            return 1.0
    z = x * x
    r = z * (_C1 + z * (_C2 + z * (_C3 + z * (_C4 + z * (_C5 + z * _C6)))))
    if ix < 0x3FD33333:                                             # |x| < 0.3
        return 1.0 - (0.5 * z - (z * r - x * y))
    if ix > 0x3fe90000:                                             # x > 0.78125
        qx = 0.28125
    else:
        qx = _from_hi(ix - 0x00200000)                              # x/4
    hz = 0.5 * z - qx
    a = 1.0 - qx
    return a - (hz - (z * r - x * y))


def _kernel_tan(x, y, iy):
    # k_tan.c: tan on [-pi/4, pi/4]; iy = 1 returns tan, -1 returns -1/tan
    hx, lx = _hi_lo(x)
    ix = hx & 0x7fffffff
    if ix < 0x3e300000:                                             # x < 2**-28
        if int(x) == 0:
            if ((ix | lx) | (iy + 1)) == 0:
                return 1.0 / abs(x)
            if iy == 1:
                return x
            w = x + y; z = _clear_lo(w); v = y - (z - x); a = -1.0 / w; t = _clear_lo(a); s = 1.0 + t * z
            return t + a * (s + t * v)
    if ix >= 0x3FE59428:                                            # |x| >= 0.6744
        if hx < 0:
            x = -x; y = -y
        z = _pio4 - x; w = _pio4lo - y; x = z + w; y = 0.0
    z = x * x; w = z * z
    r = _T[1] + w * (_T[3] + w * (_T[5] + w * (_T[7] + w * (_T[9] + w * _T[11]))))
    v = z * (_T[2] + w * (_T[4] + w * (_T[6] + w * (_T[8] + w * (_T[10] + w * _T[12])))))
    s = z * x
    r = y + z * (s * (r + v) + y)
    r += _T[0] * s
    w = x + r
    if ix >= 0x3FE59428:
        v = float(iy)
        return float(1 - ((hx >> 30) & 2)) * (v - 2.0 * (x - (w * w / (w + v) - r)))
    if iy == 1:
        return w
    z = _clear_lo(w)
    v = r - (z - x)
    a = -1.0 / w; t = _clear_lo(a)
    s = 1.0 + t * z
    return t + a * (s + t * v)


def _rem_pio2(x):
    # e_rem_pio2.c for |x| <= 2^19 * pi/2: returns (n, y0, y1) with x = n * pi/2 + (y0 + y1)
    hx, lx = _hi_lo(x)
    ix = hx & 0x7fffffff
    if ix <= 0x3fe921fb:                                            # |x| ~<= pi/4, no need for reduction
        return 0, x, 0.0
    if ix < 0x4002d97c:                                             # |x| < 3pi/4, special case with n = +-1
        if hx > 0:
            z = x - _pio2_1
            if ix != 0x3ff921fb:                                    # 33+53 bit pi is good enough
                y0 = z - _pio2_1t; y1 = (z - y0) - _pio2_1t
            else:                                                   # near pi/2, use 33+33+53 bit pi
                z -= _pio2_2; y0 = z - _pio2_2t; y1 = (z - y0) - _pio2_2t
            return 1, y0, y1
        z = x + _pio2_1
        if ix != 0x3ff921fb:
            y0 = z + _pio2_1t; y1 = (z - y0) + _pio2_1t
        else:
            z += _pio2_2; y0 = z + _pio2_2t; y1 = (z - y0) + _pio2_2t
        return -1, y0, y1
    if ix <= 0x413921fb:                                            # |x| ~<= 2^19 * (pi/2), medium size
        t = abs(x)
        n = int(t * _invpio2 + 0.5)
        fn = float(n)
        r = t - fn * _pio2_1
        w = fn * _pio2_1t                                           # 1st round good to 85 bits
        if n < 32 and ix != _npio2_hw[n - 1]:
            y0 = r - w                                              # quick check no cancellation
        else:
            j = ix >> 20
            y0 = r - w
            i = j - ((_hi_unsigned(y0) >> 20) & 0x7ff)
            if i > 16:                                              # 2nd iteration needed, good to 118 bits
                t = r
                w = fn * _pio2_2
                r = t - w
                w = fn * _pio2_2t - ((t - r) - w)
                y0 = r - w
                i = j - ((_hi_unsigned(y0) >> 20) & 0x7ff)
                if i > 49:                                          # 3rd iteration needed, 151 bits
                    t = r
                    w = fn * _pio2_3
                    r = t - w
                    w = fn * _pio2_3t - ((t - r) - w)
                    y0 = r - w
        y1 = (r - y0) - w
        if hx < 0:
            return -n, -y0, -y1
        return n, y0, y1
    raise OverflowError('argument too large for the ported reduction')


def _large(name, x):
    warnings.warn(f'fdlibm.{name}({x!r}): argument beyond 2^19*pi/2, using the platform libm (may differ from V8 by 1 ulp)')
    return getattr(math, name)(x)


def sin(x):
    # s_sin.c
    ix = _hi_unsigned(x) & 0x7fffffff
    if ix <= 0x3fe921fb:                                            # |x| ~< pi/4
        return _kernel_sin(x, 0.0, 0)
    if ix >= 0x7ff00000:
        return math.nan                                             # sin(Inf or NaN) is NaN
    try:
        n, y0, y1 = _rem_pio2(x)
    except OverflowError:
        return _large('sin', x)
    k = n & 3
    if k == 0:
        return _kernel_sin(y0, y1, 1)
    if k == 1:
        return _kernel_cos(y0, y1)
    if k == 2:
        return -_kernel_sin(y0, y1, 1)
    return -_kernel_cos(y0, y1)


def cos(x):
    # s_cos.c
    ix = _hi_unsigned(x) & 0x7fffffff
    if ix <= 0x3fe921fb:
        return _kernel_cos(x, 0.0)
    if ix >= 0x7ff00000:
        return math.nan
    try:
        n, y0, y1 = _rem_pio2(x)
    except OverflowError:
        return _large('cos', x)
    k = n & 3
    if k == 0:
        return _kernel_cos(y0, y1)
    if k == 1:
        return -_kernel_sin(y0, y1, 1)
    if k == 2:
        return -_kernel_cos(y0, y1)
    return _kernel_sin(y0, y1, 1)


def tan(x):
    # s_tan.c
    ix = _hi_unsigned(x) & 0x7fffffff
    if ix <= 0x3fe921fb:
        return _kernel_tan(x, 0.0, 1)
    if ix >= 0x7ff00000:
        return math.nan
    try:
        n, y0, y1 = _rem_pio2(x)
    except OverflowError:
        return _large('tan', x)
    return _kernel_tan(y0, y1, 1 - ((n & 1) << 1))                  # 1 -> n even, -1 -> n odd


def _R(t):
    p = t * (_pS0 + t * (_pS1 + t * (_pS2 + t * (_pS3 + t * (_pS4 + t * _pS5)))))
    q = _one + t * (_qS1 + t * (_qS2 + t * (_qS3 + t * _qS4)))
    return p, q


def asin(x):
    # port of __ieee754_asin (fdlibm e_asin.c)
    hx, lx = _hi_lo(x)
    ix = hx & 0x7fffffff
    if ix >= 0x3ff00000:                                            # |x| >= 1
        if ((ix - 0x3ff00000) | lx) == 0:
            return x * _pio2_hi + x * _pio2_lo                      # asin(1) = +-pi/2 with inexact
        return math.nan                                             # asin(|x| > 1) is NaN
    elif ix < 0x3fe00000:                                           # |x| < 0.5
        if ix < 0x3e400000:                                         # |x| < 2**-27
            if _huge + x > _one:
                return x                                            # return x with inexact if x != 0
        t = x * x
        p, q = _R(t)
        w = p / q
        return x + x * w
    # 1 > |x| >= 0.5
    w = _one - abs(x)
    t = w * 0.5
    p, q = _R(t)
    s = math.sqrt(t)
    if ix >= 0x3FEF3333:                                            # |x| > 0.975
        w = p / q
        t = _pio2_hi - (2.0 * (s + s * w) - _pio2_lo)
    else:
        w = _clear_lo(s)
        c = (t - w * w) / (s + w)
        r = p / q
        p = 2.0 * s * r - (_pio2_lo - 2.0 * c)
        q = _pio4_hi - 2.0 * w
        t = _pio4_hi - (p - q)
    return t if hx > 0 else -t


def acos(x):
    # port of __ieee754_acos (fdlibm e_acos.c)
    hx, lx = _hi_lo(x)
    ix = hx & 0x7fffffff
    if ix >= 0x3ff00000:                                            # |x| >= 1
        if ((ix - 0x3ff00000) | lx) == 0:                           # |x| == 1
            return 0.0 if hx > 0 else _pi + 2.0 * _pio2_lo
        return math.nan                                             # acos(|x| > 1) is NaN
    if ix < 0x3fe00000:                                             # |x| < 0.5
        if ix <= 0x3c600000:
            return _pio2_hi + _pio2_lo                              # if |x| < 2**-57
        z = x * x
        p, q = _R(z)
        r = p / q
        return _pio2_hi - (x - (_pio2_lo - x * r))
    elif hx < 0:                                                    # x < -0.5
        z = (_one + x) * 0.5
        p, q = _R(z)
        s = math.sqrt(z)
        r = p / q
        w = r * s - _pio2_lo
        return _pi - 2.0 * (s + w)
    else:                                                           # x > 0.5
        z = (_one - x) * 0.5
        s = math.sqrt(z)
        df = _clear_lo(s)
        c = (z - df * df) / (s + df)
        p, q = _R(z)
        r = p / q
        w = r * s + c
        return 2.0 * (df + w)


# ---------------------------------------------------------------------------------------------------------------
# helper for the routines below: INSERT_WORDS of fdlibm
# ---------------------------------------------------------------------------------------------------------------

def _from_words(hi, lo):
    """INSERT_WORDS(d, hi, lo): the bit pattern of hi (any int, taken modulo 2^32) and lo"""
    return struct.unpack('<d', struct.pack('<Q', ((hi & 0xFFFFFFFF) << 32) | (lo & 0xFFFFFFFF)))[0]


# ---------------------------------------------------------------------------------------------------------------
# atan / atan2: s_atan.c, e_atan2.c (FreeBSD msun, as in V8's base::ieee754)
# ---------------------------------------------------------------------------------------------------------------
_atanhi = [4.63647609000806093515e-01, 7.85398163397448278999e-01, 9.82793723247329054082e-01, 1.57079632679489655800e+00]
_atanlo = [2.26987774529616870924e-17, 3.06161699786838301793e-17, 1.39033110312309984516e-17, 6.12323399573676603587e-17]
_aT = [3.33333333333329318027e-01, -1.99999999998764832476e-01, 1.42857142725034663711e-01, -1.11111104054623557880e-01,
       9.09088713343650656196e-02, -7.69187620504482999495e-02, 6.66107313738753120669e-02, -5.83357013379057348645e-02,
       4.97687799461593236017e-02, -3.65315727442169155270e-02, 1.62858201153657823623e-02]
_tiny = 1.0e-300
_pi_o_4 = 7.8539816339744827900e-01
_pi_o_2 = 1.5707963267948965580e+00
_pi_lo = 1.2246467991473531772e-16


def atan(x):
    # s_atan.c
    hx, lx = _hi_lo(x)
    ix = hx & 0x7fffffff
    if ix >= 0x44100000:                                            # |x| >= 2^66
        if ix > 0x7ff00000 or (ix == 0x7ff00000 and lx != 0):
            return x + x                                            # NaN
        return _atanhi[3] + _atanlo[3] if hx > 0 else -_atanhi[3] - _atanlo[3]
    if ix < 0x3fdc0000:                                             # |x| < 0.4375
        if ix < 0x3e400000:                                         # |x| < 2^-27
            if _huge + x > _one:
                return x                                            # raise inexact
        id_ = -1
    else:
        x = abs(x)
        if ix < 0x3ff30000:                                         # |x| < 1.1875
            if ix < 0x3fe60000:                                     # 7/16 <= |x| < 11/16
                id_ = 0; x = (2.0 * x - _one) / (2.0 + x)
            else:                                                   # 11/16 <= |x| < 19/16
                id_ = 1; x = (x - _one) / (x + _one)
        elif ix < 0x40038000:                                       # |x| < 2.4375
            id_ = 2; x = (x - 1.5) / (_one + 1.5 * x)
        else:                                                       # 2.4375 <= |x| < 2^66
            id_ = 3; x = -1.0 / x
    z = x * x
    w = z * z
    s1 = z * (_aT[0] + w * (_aT[2] + w * (_aT[4] + w * (_aT[6] + w * (_aT[8] + w * _aT[10])))))
    s2 = w * (_aT[1] + w * (_aT[3] + w * (_aT[5] + w * (_aT[7] + w * _aT[9]))))
    if id_ < 0:
        return x - x * (s1 + s2)
    z = _atanhi[id_] - ((x * (s1 + s2) - _atanlo[id_]) - x)
    return -z if hx < 0 else z


def atan2(y, x):
    # e_atan2.c (FreeBSD: |y/x| > 2^60 keeps only the sign of y, `m &= 1`)
    hx, lx = _hi_lo(x)
    ix = hx & 0x7fffffff
    hy, ly = _hi_lo(y)
    iy = hy & 0x7fffffff
    if x != x or y != y:                                            # x or y is NaN
        return x + y
    if ((hx - 0x3ff00000) | lx) == 0:                               # x = 1.0
        return atan(y)
    m = ((hy >> 31) & 1) | ((hx >> 30) & 2)                         # 2 * sign(x) + sign(y)
    if (iy | ly) == 0:                                              # y = 0
        if m in (0, 1):
            return y                                                # atan(+-0, +anything) = +-0
        return _pi + _tiny if m == 2 else -_pi - _tiny              # atan(+-0, -anything) = +-pi
    if (ix | lx) == 0:                                              # x = 0
        return -_pi_o_2 - _tiny if hy < 0 else _pi_o_2 + _tiny
    if ix == 0x7ff00000:                                            # x is INF
        if iy == 0x7ff00000:
            return (_pi_o_4 + _tiny, -_pi_o_4 - _tiny, 3.0 * _pi_o_4 + _tiny, -3.0 * _pi_o_4 - _tiny)[m]
        return (0.0, -0.0, _pi + _tiny, -_pi - _tiny)[m]
    if iy == 0x7ff00000:                                            # y is INF
        return -_pi_o_2 - _tiny if hy < 0 else _pi_o_2 + _tiny
    k = (iy - ix) >> 20                                             # compute y/x
    if k > 60:                                                      # |y/x| > 2^60
        z = _pi_o_2 + 0.5 * _pi_lo
        m &= 1
    elif hx < 0 and k < -60:                                        # 0 > |y|/x > -2^-60
        z = 0.0
    else:
        z = atan(abs(y / x))                                        # safe to do y/x
    if m == 0:
        return z                                                    # atan(+, +)
    if m == 1:
        return -z                                                   # atan(-, +)
    if m == 2:
        return _pi - (z - _pi_lo)                                   # atan(+, -)
    return (z - _pi_lo) - _pi                                       # atan(-, -)


# ---------------------------------------------------------------------------------------------------------------
# exp: e_exp.c (FreeBSD msun, as in V8's base::ieee754, which also returns E itself for exp(1))
# ---------------------------------------------------------------------------------------------------------------
_halF = (0.5, -0.5)
_o_threshold = 7.09782712893383973096e+02
_u_threshold = -7.45133219101941108420e+02
_ln2HI = (6.93147180369123816490e-01, -6.93147180369123816490e-01)
_ln2LO = (1.90821492927058770002e-10, -1.90821492927058770002e-10)
_invln2 = 1.44269504088896338700e+00
_P1 = 1.66666666666666019037e-01
_P2 = -2.77777777770155933842e-03
_P3 = 6.61375632143793436117e-05
_P4 = -1.65339022054652515390e-06
_P5 = 4.13813679705723846039e-08
_E = 2.718281828459045
_twom1000 = 9.33263618503218878990e-302                             # 2^-1000


def exp(x):
    # e_exp.c
    hx = _hi_unsigned(x)
    xsb = (hx >> 31) & 1                                            # sign bit of x
    hx &= 0x7fffffff                                                # high word of |x|
    if hx >= 0x40862E42:                                            # |x| >= 709.78...
        if hx >= 0x7ff00000:
            lx = _hi_lo(x)[1]
            if ((hx & 0xfffff) | lx) != 0:
                return x + x                                        # NaN
            return x if xsb == 0 else 0.0                           # exp(+-inf) = {inf, 0}
        if x > _o_threshold:
            return math.inf                                         # overflow (huge * huge)
        if x < _u_threshold:
            return 0.0                                              # underflow (twom1000 * twom1000)
    k = 0
    hi = lo = 0.0
    if hx > 0x3fd62e42:                                             # |x| > 0.5 ln2
        if hx < 0x3FF0A2B2:                                         # and |x| < 1.5 ln2
            if x == 1.0:
                return _E                                           # (V8: the algorithm would get the last bit wrong)
            hi = x - _ln2HI[xsb]; lo = _ln2LO[xsb]; k = 1 - xsb - xsb
        else:
            k = int(_invln2 * x + _halF[xsb])                       # (int) truncates towards zero
            t = float(k)
            hi = x - t * _ln2HI[0]                                  # t * ln2HI is exact here
            lo = t * _ln2LO[0]
        x = hi - lo
    elif hx < 0x3e300000:                                           # |x| < 2^-28
        if _huge + x > _one:
            return _one + x                                         # trigger inexact
    t = x * x
    twopk = _from_words(0x3ff00000 + (k << 20), 0) if k >= -1021 else _from_words(0x3ff00000 + ((k + 1000) << 20), 0)
    c = x - t * (_P1 + t * (_P2 + t * (_P3 + t * (_P4 + t * _P5))))
    if k == 0:
        return _one - ((x * c) / (c - 2.0) - x)
    y = _one - ((lo - (x * c) / (2.0 - c)) - hi)
    if k >= -1021:
        if k == 1024:
            return y * 2.0 * math.ldexp(1.0, 1023)                  # 0x1p1023
        return y * twopk
    return y * twopk * _twom1000


# ---------------------------------------------------------------------------------------------------------------
# cbrt: s_cbrt.c (FreeBSD msun, Bruce D. Evans' 2005 version, as in V8's base::ieee754). Used by headFit (10414).
# ---------------------------------------------------------------------------------------------------------------
_B1 = 715094163                                                     # (1023 - 1023/3 - 0.03306235651) * 2**20
_B2 = 696219795                                                     # (1023 - 1023/3 - 54/3 - 0.03306235651) * 2**20
_cP0 = 1.87595182427177009643                                       # |1/cbrt(x) - p(x)| < 2**-23.5
_cP1 = -1.88497979543377169875
_cP2 = 1.621429720105354466140
_cP3 = -0.758397934778766047437
_cP4 = 0.145996192886612446982


def cbrt(x):
    # s_cbrt.c
    hx, low = _hi_lo(x)
    sign = hx & 0x80000000                                          # (hx is signed: & gives the sign bit as 0 or 2^31)
    hx = (hx & 0xFFFFFFFF) ^ sign
    if hx >= 0x7ff00000:
        return x + x                                                # cbrt(NaN, INF) is itself
    # rough cbrt to 5 bits
    if hx < 0x00100000:                                             # zero or subnormal
        if (hx | low) == 0:
            return x                                                # cbrt(0) is itself
        t = _from_words(0x43500000, 0)                              # t = 2**54
        t *= x
        high = _hi_unsigned(t)
        t = _from_words(sign | ((high & 0x7fffffff) // 3 + _B2), 0)
    else:
        t = _from_words(sign | (hx // 3 + _B1), 0)
    # new cbrt to 23 bits: cbrt(x) = t * cbrt(x / t**3) ~= t * P(t**3 / x)
    r = (t * t) * (t / x)
    t = t * ((_cP0 + r * (_cP1 + r * _cP2)) + ((r * r) * r) * (_cP3 + r * _cP4))
    # round t away from zero to 23 bits
    bits = struct.unpack('<Q', struct.pack('<d', t))[0]
    bits = (bits + 0x80000000) & 0xffffffffc0000000
    t = struct.unpack('<d', struct.pack('<Q', bits))[0]
    # one step Newton iteration to 53 bits with error < 0.667 ulps
    s = t * t                                                       # t * t is exact
    r = x / s                                                       # error <= 0.5 ulps; |r| < |t|
    w = t + t                                                       # t + t is exact
    r = (r - t) / (w + r)                                           # r - t is exact; w + r ~= 3 * t
    t = t + t * r                                                   # error <= 0.5 + 0.5/3 + epsilon
    return t
