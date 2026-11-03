# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
# Ported from Stylized HairMesh Creator 1.0.1 by catarackta (https://catarackta.netlify.app/), used with the author's permission.
"""Helpers that reproduce JavaScript semantics the original relies on (see docs/js-para-python.md).

Every function here exists because the plain Python equivalent silently gives a different result.
"""
import math
from decimal import Decimal, ROUND_HALF_UP, localcontext

INF = math.inf
NAN = math.nan


def is_nan(x):
    return isinstance(x, float) and x != x


def js_max(*xs):
    """Math.max: NaN if any argument is NaN; -Infinity with no arguments."""
    if not xs:
        return -INF
    for x in xs:
        if x != x:                                                  # (is_nan, inlined: these run millions of times)
            return NAN
    return max(xs)


def js_min(*xs):
    """Math.min: NaN if any argument is NaN; Infinity with no arguments."""
    if not xs:
        return INF
    for x in xs:
        if x != x:
            return NAN
    return min(xs)


def _odd_integer(y):
    return math.isfinite(y) and y == math.floor(y) and abs(y) < 2 ** 53 and int(y) % 2 == 1


def js_pow(x, y):
    """Math.pow(x, y) / x ** y as V8 computes them (tests/test_fdlibm.py). V8 calls the C runtime's pow, as Python's
    ** does, except where that pow is not exact: x ** 2 is x * x and Math.pow(x, 0.5) is sqrt(x) (the Windows pow is
    1 ulp off on ~0.05 % of the arguments for both, V8 never). On top, ECMAScript's results where Python raises or C
    differs: NaN for a NaN y or |x| = 1 with an infinite y, NaN for a negative x with a fractional y, +-Infinity for
    0 ** negative and on overflow. A literal JS `x ** 2` in a hot loop is written as x * x instead (same bits)."""
    if y != y:
        return NAN
    if y == 2:
        return x * x
    if y == 0.5 and (x > 0 or (x == 0 and math.copysign(1.0, x) > 0)):
        return math.sqrt(x)                                         # (pow(-0, 0.5) = +0 and pow(-inf, 0.5) = +inf: below)
    if math.isinf(y) and abs(x) == 1:
        return NAN
    try:
        return math.pow(x, y)
    except ValueError:
        if x == 0:                                                  # 0 ** negative
            return -INF if (math.copysign(1.0, x) < 0 and _odd_integer(y)) else INF
        return NAN                                                  # negative ** fractional
    except OverflowError:
        return -INF if (x < 0 and _odd_integer(y)) else INF


def clamp(v, a, b):
    # port of clamp (hair-modeler.clean.html:543): Math.max(a, Math.min(b, v))
    # = js_max(a, js_min(b, v)) written out: min() keeps the first of two equal values, so b wins a tie with v and
    # a wins a tie with the minimum (the same objects come back: an int bound stays an int); NaN anywhere gives NaN.
    if v != v or a != a or b != b:
        return NAN
    w = v if v < b else b
    return w if w > a else a


def js_round(x):
    """Math.round: halves go up (towards +Infinity). Returns an int for finite input.

    Not floor(x + 0.5): 0.49999999999999994 + 0.5 rounds to 1.0 in binary, while Math.round gives 0.
    """
    if is_nan(x) or x in (INF, -INF):
        return x
    f = math.floor(x)
    return f + 1 if x - f >= 0.5 else f


def to_int32(x):
    """ToInt32, as in `x | 0`: truncate towards zero, wrap to 32 bits; NaN and infinities give 0."""
    if is_nan(x) or x in (INF, -INF):
        return 0
    n = int(x) & 0xFFFFFFFF
    return n - (1 << 32) if n >= (1 << 31) else n


def js_sign(x):
    """Math.sign: NaN stays NaN; zero (either sign) is returned as is."""
    if is_nan(x):
        return x
    if x > 0:
        return 1
    if x < 0:
        return -1
    return x


def truthy(x):
    """JavaScript truthiness: None, False, 0, -0, NaN and '' are false; everything else (empty lists too) is true."""
    if x is None or x is False:
        return False
    if isinstance(x, bool):
        return x
    if isinstance(x, (int, float)):
        return x != 0 and not is_nan(x)
    if isinstance(x, str):
        return x != ''
    return True


def js_or(x, default):
    """`x || default` (NaN counts as false, unlike Python's `or`)."""
    return x if truthy(x) else default


def nullish(x, default):
    """`x ?? default`: only None (null / undefined) is replaced; 0, '' and NaN are kept."""
    return default if x is None else x


def at(seq, i):
    """`seq[i]` with JavaScript bounds: None outside 0 <= i < len (never wraps negative indices)."""
    return seq[i] if 0 <= i < len(seq) else None


def _same(a, b):
    # `===`: value for primitives (NaN never equals), identity for objects
    if a is None or isinstance(a, (bool, int, float, str)):
        return type(a) is type(b) and a == b if not isinstance(a, (int, float)) or isinstance(a, bool) else (isinstance(b, (int, float)) and not isinstance(b, bool) and a == b)
    return a is b


def index_of(seq, x):
    """Array.prototype.indexOf: first index where `seq[i] === x`, else -1."""
    for i, v in enumerate(seq):
        if _same(v, x):
            return i
    return -1


def includes(seq, x):
    """Array.prototype.includes with `===` semantics (NaN excepted; the original never looks NaN up)."""
    return index_of(seq, x) >= 0


def to_fixed(x, digits):
    """Number.prototype.toFixed for |x| < 1e21: rounds the exact binary value half up, like V8 does."""
    if is_nan(x):
        return 'NaN'
    if x in (INF, -INF):
        return 'Infinity' if x > 0 else '-Infinity'
    if x == 0:
        x = 0.0                                                     # (-0).toFixed(n) is "0.00…"
    with localcontext() as ctx:
        ctx.prec = 400
        q = Decimal(x).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
    s = format(q, 'f')
    return s


def to_precision_number(x, p):
    """`+x.toPrecision(p)`: x rounded (half up on the exact binary value) to p significant digits, as a float."""
    if is_nan(x) or x in (INF, -INF) or x == 0:
        return x
    with localcontext() as ctx:
        ctx.prec = 400
        d = Decimal(x)
        q = d.quantize(Decimal(1).scaleb(d.adjusted() - p + 1), rounding=ROUND_HALF_UP)
    return float(q)


def js_hypot(*xs):
    """Math.hypot as V8 implements it (builtins/math.tq): values normalised by the largest, Kahan-summed squares,
    sqrt(sum) * max. Not the C library's hypot, which rounds differently."""
    if not xs:
        return 0.0
    mx = 0.0
    for x in xs:
        ax = abs(x)
        if ax != ax:
            return NAN
        if ax == INF:
            return INF
        if ax > mx:
            mx = ax
    if mx == 0:
        return 0.0
    total = 0.0; compensation = 0.0
    for x in xs:
        n = abs(x) / mx
        summand = n * n - compensation
        preliminary = total + summand
        compensation = (preliminary - total) - summand
        total = preliminary
    return math.sqrt(total) * mx


def js_div(a, b):
    """`a / b` with JavaScript's IEEE semantics: a zero divisor gives +-Infinity (or NaN for 0 / 0 and NaN / 0)
    instead of Python's ZeroDivisionError."""
    try:
        return a / b
    except ZeroDivisionError:
        if a != a or a == 0:
            return NAN
        return math.copysign(INF, a) * math.copysign(1.0, b)


def js_num_str(x):
    """String(number) (Number::toString, ECMA-262 6.1.6.1.20): the shortest digits that read back as x — the digits
    of Python's repr — laid out as JavaScript does: no exponent from 1e-6 up to 1e21, and `1.5e-7`, not `1.5e-07`."""
    if x != x:
        return 'NaN'
    if x in (INF, -INF):
        return 'Infinity' if x > 0 else '-Infinity'
    if x == 0:
        return '0'
    t = Decimal(repr(abs(float(x)))).normalize().as_tuple()
    ds = ''.join(map(str, t.digits)); k = len(ds); n = t.exponent + k     # x = 0.ds * 10^n
    if k <= n <= 21:
        s = ds + '0' * (n - k)
    elif 0 < n <= 21:
        s = ds[:n] + '.' + ds[n:]
    elif -6 < n <= 0:
        s = '0.' + '0' * -n + ds
    else:
        e = n - 1
        s = ds[0] + ('.' + ds[1:] if k > 1 else '') + 'e' + ('+' if e >= 0 else '-') + str(abs(e))
    return ('-' if x < 0 else '') + s


def js_str(x):
    """String(number) for the values used in keys: integers without a trailing `.0`."""
    if isinstance(x, float) and x.is_integer() and abs(x) < 1e21:
        return str(int(x))
    return str(x)
