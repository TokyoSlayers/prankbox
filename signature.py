"""Signatures Ed25519 (RFC 8032) en Python pur, sans dépendance.

Sert à vérifier que les mises à jour viennent bien du propriétaire de la clé
privée. Pas en temps constant : la vérification ne manipule aucun secret, et la
signature ne se fait que sur le PC de celui qui publie.
"""

import hashlib

P = 2 ** 255 - 19
L = 2 ** 252 + 27742317777372353535851937790883648493
D = -121665 * pow(121666, P - 2, P) % P
SQRT_M1 = pow(2, (P - 1) // 4, P)


def _hash_int(data):
    return int.from_bytes(hashlib.sha512(data).digest(), "little")


def _add(p, q):
    """Addition de points en coordonnées étendues (X, Y, Z, T)."""
    a = (p[1] - p[0]) * (q[1] - q[0]) % P
    b = (p[1] + p[0]) * (q[1] + q[0]) % P
    c = 2 * p[3] * q[3] * D % P
    d = 2 * p[2] * q[2] % P
    e, f, g, h = b - a, d - c, d + c, b + a
    return e * f % P, g * h % P, f * g % P, e * h % P


def _mul(s, p):
    q = (0, 1, 1, 0)
    while s:
        if s & 1:
            q = _add(q, p)
        p = _add(p, p)
        s >>= 1
    return q


def _same(p, q):
    return (p[0] * q[2] - q[0] * p[2]) % P == 0 and (p[1] * q[2] - q[1] * p[2]) % P == 0


def _recover_x(y, sign):
    if y >= P:
        return None
    x2 = (y * y - 1) * pow(D * y * y + 1, P - 2, P) % P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (P + 3) // 8, P)
    if (x * x - x2) % P:
        x = x * SQRT_M1 % P
    if (x * x - x2) % P:
        return None
    return P - x if (x & 1) != sign else x


_GY = 4 * pow(5, P - 2, P) % P
_GX = _recover_x(_GY, 0)
G = (_GX, _GY, 1, _GX * _GY % P)


def _compress(p):
    zinv = pow(p[2], P - 2, P)
    x, y = p[0] * zinv % P, p[1] * zinv % P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _decompress(data):
    y = int.from_bytes(data, "little")
    sign, y = y >> 255, y & ((1 << 255) - 1)
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % P)


def _expand(secret):
    h = hashlib.sha512(secret).digest()
    a = int.from_bytes(h[:32], "little") & ((1 << 254) - 8) | (1 << 254)
    return a, h[32:]


def public_key(secret):
    """Clé publique (32 octets) d'une clé privée de 32 octets."""
    return _compress(_mul(_expand(secret)[0], G))


def sign(secret, message):
    a, prefix = _expand(secret)
    public = _compress(_mul(a, G))
    r = _hash_int(prefix + message) % L
    big_r = _compress(_mul(r, G))
    s = (r + _hash_int(big_r + public + message) % L * a) % L
    return big_r + s.to_bytes(32, "little")


def verify(public, message, signature):
    if len(public) != 32 or len(signature) != 64:
        return False
    a, r = _decompress(public), _decompress(signature[:32])
    s = int.from_bytes(signature[32:], "little")
    if a is None or r is None or s >= L:
        return False
    h = _hash_int(signature[:32] + public + message) % L
    return _same(_mul(s, G), _add(r, _mul(h, a)))
