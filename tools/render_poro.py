"""Pré-calcule les images du poro qui tourne, à partir d'un modèle STL.

Le rendu 3D est fait ici une fois pour toutes (numpy + Pillow) : PrankBox n'a
ensuite qu'à afficher les images, sans dépendance chez les potes.

Usage : python tools/render_poro.py chemin/vers/modele.stl [nb_images] [taille_px] [oiia]
Sortie : images/poro/poro_XX.png
         avec "oiia" : images/poro_oiia/debout.png + toupie_XX.png (le poro tassé qui tourne)
"""

import math
import os
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "..", "images", "poro")
SUPERSAMPLE = 3
FUR = np.array([246, 248, 252], dtype=float)
HORN = np.array([110, 170, 235], dtype=float)
EYE = np.array([25, 22, 40], dtype=float)
LIGHT = np.array([-0.4, 0.7, 0.6])
PITCH = math.radians(18)


def load_stl(path):
    with open(path, "rb") as f:
        data = f.read()
    count = int.from_bytes(data[80:84], "little")
    if 84 + 50 * count != len(data):
        raise ValueError("seuls les STL binaires sont pris en charge")
    dtype = np.dtype([("normal", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")])
    tris = np.frombuffer(data, dtype=dtype, offset=84, count=count)["v"].astype(float)
    tris = tris[:, :, [0, 2, 1]]  # Blender est en Z vers le haut, on passe en Y vers le haut
    tris[:, :, 2] *= -1
    tris -= (tris.reshape(-1, 3).max(0) + tris.reshape(-1, 3).min(0)) / 2
    return tris / np.abs(tris).max()


def vertex_normals(tris):
    """Normales lissées : moyenne des faces autour de chaque sommet (rendu "doux" de la fourrure)."""
    face_n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    if (face_n * tris.mean(axis=1)).sum() < 0:  # triangles orientés vers l'intérieur
        face_n = -face_n
    keys = np.round(tris.reshape(-1, 3), 4)
    _, idx = np.unique(keys, axis=0, return_inverse=True)
    acc = np.zeros((idx.max() + 1, 3))
    np.add.at(acc, idx, np.repeat(face_n, 3, axis=0))
    acc /= np.linalg.norm(acc, axis=1, keepdims=True) + 1e-12
    return acc[idx].reshape(-1, 3, 3)


def paint_eyes(pos, col, shade):
    """Le STL n'a pas de couleurs : on peint les yeux au pixel près dans le fond des deux orbites."""
    x, y, z = pos[..., 0], pos[..., 1], pos[..., 2]
    socket = (z > 0.5) & (z < 0.8)
    eye = socket & (np.hypot(np.abs(x) - 0.36, (y - 0.15) * 1.15) < 0.13)
    glint = socket & (np.hypot(np.abs(x) - 0.36 + 0.04 * np.sign(x), y - 0.2) < 0.035)
    col[eye] = EYE * shade[eye, None]
    col[glint] = 255
    return col


def colors_for(tris):
    """Fourrure blanche, cornes bleues : tout ce qui dépasse en haut du modèle."""
    height = tris[:, :, 1]
    top = np.clip((height - 0.55) / 0.25, 0, 1)[..., None]
    return FUR * (1 - top) + HORN * top


def rotation(yaw, pitch):
    cy, sy, cp, sp = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch)
    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    rx = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]])
    return rx @ ry


def squashed(tris, normals, k):
    """Poro tassé (comme le chat qui se ramasse pour tourner) : plus bas, plus large."""
    stretch = np.array([1 / math.sqrt(k), k, 1 / math.sqrt(k)])
    n = normals / stretch
    return tris * stretch, n / np.linalg.norm(n, axis=-1, keepdims=True)


def fit_scale(tris, pitch=PITCH):
    """Échelle qui garde le modèle entier dans l'image sous tous les angles."""
    pts = tris.reshape(-1, 3)
    extent = max(np.abs(pts @ rotation(2 * math.pi * k / 72, pitch)[:2].T).max() for k in range(72))
    return 0.48 / extent


def render(tris, normals, colors, yaw, size, scale, pitch=PITCH, eyes_on=None):
    """eyes_on : positions d'origine du modèle, pour placer les yeux quand il est déformé."""
    eyes_on = tris if eyes_on is None else eyes_on
    s = size * SUPERSAMPLE
    rot = rotation(yaw, pitch)
    v = tris @ rot.T
    n = normals @ rot.T
    light = LIGHT / np.linalg.norm(LIGHT)
    shade = 0.62 + 0.45 * np.clip(n @ light, 0, 1)
    vcol = np.clip(colors * shade[..., None], 0, 255)

    px = (v[:, :, 0] * scale + 0.5) * s
    py = (0.5 - v[:, :, 1] * scale) * s
    depth = v[:, :, 2]

    zbuf = np.full((s, s), -np.inf)
    img = np.zeros((s, s, 4))
    for t in range(len(tris)):
        x0, x1, x2 = px[t]
        y0, y1, y2 = py[t]
        area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
        if abs(area) < 1e-9:
            continue
        xmin, xmax = int(max(min(x0, x1, x2), 0)), int(min(max(x0, x1, x2) + 1, s))
        ymin, ymax = int(max(min(y0, y1, y2), 0)), int(min(max(y0, y1, y2) + 1, s))
        if xmin >= xmax or ymin >= ymax:
            continue
        gx, gy = np.meshgrid(np.arange(xmin, xmax) + 0.5, np.arange(ymin, ymax) + 0.5)
        w0 = ((x1 - gx) * (y2 - gy) - (x2 - gx) * (y1 - gy)) / area
        w1 = ((x2 - gx) * (y0 - gy) - (x0 - gx) * (y2 - gy)) / area
        w2 = 1 - w0 - w1
        inside = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
        if not inside.any():
            continue
        z = w0 * depth[t, 0] + w1 * depth[t, 1] + w2 * depth[t, 2]
        region = zbuf[ymin:ymax, xmin:xmax]
        closer = inside & (z > region)
        region[closer] = z[closer]
        w = np.stack([w0, w1, w2], axis=-1)
        col = paint_eyes(w @ eyes_on[t], w @ vcol[t], w @ shade[t])
        img[ymin:ymax, xmin:xmax, :3][closer] = col[closer]
        img[ymin:ymax, xmin:xmax, 3][closer] = 255

    big = Image.fromarray(img.astype(np.uint8), "RGBA")
    small = big.resize((size, size), Image.LANCZOS)
    alpha = small.getchannel("A").point(lambda a: 255 if a > 128 else 0)  # pas de demi-transparence sous Tk
    small.putalpha(alpha)
    return small


def render_turn(tris, normals, colors, frames, size, scale, folder, prefix, eyes_on=None):
    os.makedirs(folder, exist_ok=True)
    for k in range(frames):
        render(tris, normals, colors, 2 * math.pi * k / frames, size, scale, eyes_on=eyes_on).save(
            os.path.join(folder, f"{prefix}_{k:02d}.png"))
        print(f"\r{prefix} {k + 1}/{frames}", end="", flush=True)
    print()


def main():
    path = sys.argv[1]
    frames = int(sys.argv[2]) if len(sys.argv) > 2 else 36
    size = int(sys.argv[3]) if len(sys.argv) > 3 else 300
    tris = load_stl(path)
    normals, colors = vertex_normals(tris), colors_for(tris)
    scale = fit_scale(tris)
    if "oiia" not in sys.argv[4:]:
        return render_turn(tris, normals, colors, frames, size, scale, OUT_DIR, "poro")
    # images/poro_oiia/ : debout de face, puis tassé pour la toupie (même échelle, même sol)
    folder = os.path.join(HERE, "..", "images", "poro_oiia")
    os.makedirs(folder, exist_ok=True)
    low_tris, low_normals = squashed(tris, normals, 0.8)
    low_tris[..., 1] += tris[..., 1].min() - low_tris[..., 1].min()
    scale = min(scale, fit_scale(low_tris))
    render(tris, normals, colors, 0, size, scale).save(os.path.join(folder, "debout.png"))
    render_turn(low_tris, low_normals, colors, frames, size, scale, folder, "toupie", eyes_on=tris)


if __name__ == "__main__":
    main()
