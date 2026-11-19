"""Compara el color de la fuente (FBX) con el del GLB ensamblado que deja `afm build --color-desde`: 3 vistas (frente, lado, espalda),
la fuente arriba y el GLB abajo, en un PNG (plan T16). Rasterizador por software (z-buffer ortográfico): no necesita GL ni GTK.

Uso:
    python tools/render_color_compare.py --fbx figura.fbx --stl figura.stl --glb out/figura_ensamblada.glb --out comparar.png [--front -Y]

`--stl` fija la escala de la fuente (se alinea con la similitud del modo B, sin ICP salvo que haga falta). `--front -Y` gira la fuente 180°
sobre Z, como `Project.front`. El GLB (en Y arriba, como OBJ/glTF) es el que escribe `export`: las piezas están en la POSE DE IMPRESIÓN (las piernas giradas), no de pie;
las piezas de brazos y torso sí coinciden con la fuente. No toca `src/afm/`.
"""
from __future__ import annotations

import argparse
import sys

import numpy as np

VIEWS = {"frente": (0.0, 1.0, 0.0), "lado": (1.0, 0.0, 0.0), "espalda": (0.0, -1.0, 0.0)}   # hacia dónde está el observador


def render(tris: np.ndarray, uv: np.ndarray | None, tex: np.ndarray | None, view: str, *, px_per_mm: float = 4.0,
           size: tuple[int, int] = (260, 520), z0: float = 0.0, cx: float = 0.0, bg=(238, 238, 238)) -> np.ndarray:
    """Imagen uint8 (alto, ancho, 3) de `tris` (F,3,3) vista desde `view`. `uv` (F,3,2) y `tex` (H,W,3) dan el color (sin ellos, gris).
    Observador a distancia infinita; derecha de pantalla = adelante × arriba; `z0` = cota que queda abajo; `cx` = coordenada
    horizontal (según la vista) que queda en el centro."""
    v = np.asarray(VIEWS[view], float)
    right = np.cross(-v, [0.0, 0.0, 1.0])
    w, h = size
    img = np.empty((h, w, 3), np.uint8)
    img[:] = bg
    zbuf = np.full((h, w), -np.inf)
    T = np.asarray(tris, float)
    sx = (T @ right - cx) * px_per_mm + w / 2.0
    sy = (h - 8) - (T[:, :, 2] - z0) * px_per_mm
    depth = T @ v
    n = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
    ln = np.linalg.norm(n, axis=1)
    lam = np.where(ln > 0, np.abs(n @ v) / np.where(ln > 0, ln, 1.0), 0.0)          # luz desde el observador, doble cara
    shade = 0.45 + 0.55 * lam
    for k in range(len(T)):
        x, y = sx[k], sy[k]
        x0, x1 = max(int(np.floor(x.min())), 0), min(int(np.ceil(x.max())), w - 1)
        y0, y1 = max(int(np.floor(y.min())), 0), min(int(np.ceil(y.max())), h - 1)
        if x1 < x0 or y1 < y0:
            continue
        d = (y[1] - y[2]) * (x[0] - x[2]) + (x[2] - x[1]) * (y[0] - y[2])
        if abs(d) < 1e-9:
            continue
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        a = ((y[1] - y[2]) * (gx - x[2]) + (x[2] - x[1]) * (gy - y[2])) / d
        b = ((y[2] - y[0]) * (gx - x[2]) + (x[0] - x[2]) * (gy - y[2])) / d
        c = 1.0 - a - b
        inside = (a >= -1e-6) & (b >= -1e-6) & (c >= -1e-6)
        if not inside.any():
            continue
        z = a * depth[k, 0] + b * depth[k, 1] + c * depth[k, 2]
        sub = zbuf[y0:y1 + 1, x0:x1 + 1]
        win = inside & (z > sub)
        if not win.any():
            continue
        if uv is not None and tex is not None:
            u = a[win] * uv[k, 0, 0] + b[win] * uv[k, 1, 0] + c[win] * uv[k, 2, 0]
            vv = a[win] * uv[k, 0, 1] + b[win] * uv[k, 1, 1] + c[win] * uv[k, 2, 1]
            th, tw = tex.shape[:2]
            col = tex[np.clip(((1.0 - vv) * th).astype(int), 0, th - 1), np.clip((u * tw).astype(int), 0, tw - 1)].astype(float)
        else:
            col = np.full((int(win.sum()), 3), 170.0)
        sub[win] = z[win]
        img[y0:y1 + 1, x0:x1 + 1][win] = np.clip(col * shade[k], 0, 255).astype(np.uint8)
    return img


def source_geometry(fbx: str, stl: str, front: str = "+Y"):
    """(triángulos (F,3,3), uv (F,3,2), textura (H,W,3), z0, cx) de la fuente en el marco de la figura, de pie y centrada."""
    import trimesh

    from afm.fbxio import load_textured
    from afm.frame import FigureFrame

    tm = load_textured(fbx)
    if "base_color" not in tm.images:
        raise ValueError("el FBX no trae la textura de color base")
    target = trimesh.load(stl, force="mesh")
    frame = FigureFrame.from_similarity(tm, target).then(FigureFrame.engine_post(front, None))
    V = frame.apply(tm.vertices)
    lo, hi = V.min(0), V.max(0)
    V = V - np.array([(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]])
    return V[tm.faces], np.asarray(tm.corner_uv, float), tm.image("base_color"), 0.0, 0.0


def glb_geometry(glb: str):
    """Los triángulos, UV y textura de cada pieza de un GLB escrito por `Colorizer.write`: lista de (tris, uv, tex)."""
    import trimesh

    from afm.frame import Z_UP_TO_Y_UP

    scene = trimesh.load(glb)
    out = []
    for g in scene.geometry.values():
        tex = getattr(g.visual.material, "baseColorTexture", None)
        uv = np.asarray(g.visual.uv, float)
        F = np.asarray(g.faces)
        V = np.asarray(g.vertices, float) @ Z_UP_TO_Y_UP                  # el GLB va en Y arriba: de vuelta al marco Z arriba del STL
        out.append((V[F], uv[F], None if tex is None else np.asarray(tex.convert("RGB"))))
    return out


def compare(fbx: str, stl: str, glb: str, out: str, front: str = "+Y", px_per_mm: float = 4.0) -> dict:
    from PIL import Image

    tris, uv, tex, z0, cx = source_geometry(fbx, stl, front)
    pieces = glb_geometry(glb)
    allv = np.concatenate([p[0].reshape(-1, 3) for p in pieces])
    zg = float(allv[:, 2].min())
    h = int((tris[:, :, 2].max() - z0) * px_per_mm) + 16
    w = int(max(np.ptp(tris[:, :, 0]), np.ptp(tris[:, :, 1]), np.ptp(allv[:, 0]), np.ptp(allv[:, 1])) * px_per_mm) + 40
    rows = []
    for k, vista in enumerate(VIEWS):
        top = render(tris, uv, tex, vista, px_per_mm=px_per_mm, size=(w, h), z0=z0, cx=cx)
        cxg = float(np.median(allv @ np.cross(-np.asarray(VIEWS[vista]), [0, 0, 1])))          # centrar el GLB en esa vista
        bot = np.full_like(top, 238)
        zb = np.full(top.shape[:2], -np.inf)
        for t, u, tx in pieces:                                     # cada pieza con su textura; z-buffer compartido a mano
            img = render(t, u, tx, vista, px_per_mm=px_per_mm, size=(w, h), z0=zg, cx=cxg)
            mask = (img != 238).any(axis=2) & (zb == -np.inf)
            bot[mask] = img[mask]
            zb[mask] = 0
        rows.append(np.concatenate([top, bot], axis=0))
    sheet = np.concatenate(rows, axis=1)
    Image.fromarray(sheet).save(out)
    return {"png": out, "tamano": list(sheet.shape[1::-1]), "vistas": list(VIEWS), "triangulos_fuente": int(len(tris)),
            "piezas_glb": len(pieces)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--fbx", required=True)
    ap.add_argument("--stl", required=True)
    ap.add_argument("--glb", required=True)
    ap.add_argument("--out", default="comparar_color.png")
    ap.add_argument("--front", default="+Y", choices=("+Y", "-Y"))
    ap.add_argument("--px-por-mm", type=float, default=4.0)
    args = ap.parse_args(argv)
    info = compare(args.fbx, args.stl, args.glb, args.out, args.front, args.px_por_mm)
    print(info)
    return 0


if __name__ == "__main__":
    sys.exit(main())
