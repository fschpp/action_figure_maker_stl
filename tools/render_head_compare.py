"""Compara el color de una cabeza importada con su FBX (fuente) contra la pieza `cabeza` horneada en el GLB ensamblado (plan T37).

Uso:
    python tools/render_head_compare.py --fbx cabeza.fbx --glb out/figura_ensamblada.glb --out cabeza_comparar.png --escala 0.2944 [--corte-y -28]

Fila de arriba: la fuente (FBX de la cabeza, solo lo que queda sobre el corte de la base). Fila de abajo: la pieza `cabeza` del GLB.
Colocar la fuente: Y arriba -> Z arriba, escala `--escala` (`info["color"]["cabeza"]["escala"]` del informe de `afm build`), y el giro sobre Z
(0/90/180/270) que deje la fuente más cerca de la pieza; se imprime la distancia media. Rasterizador por software (sin GL).
No toca `src/afm/`.
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from render_color_compare import VIEWS, glb_geometry, render      # noqa: E402


def head_piece(glb: str):
    import trimesh

    from afm.frame import Z_UP_TO_Y_UP

    scene = trimesh.load(glb)
    g = scene.geometry.get("cabeza")
    if g is None:
        key = next((k for k in scene.geometry if "cabeza" in k), None)
        if key is None:
            raise ValueError("el GLB no tiene una pieza «cabeza»")
        g = scene.geometry[key]
    tex = g.visual.material.baseColorTexture
    F = np.asarray(g.faces)
    V = np.asarray(g.vertices, float) @ Z_UP_TO_Y_UP
    return V[F], np.asarray(g.visual.uv, float)[F], np.asarray(tex.convert("RGB"))


def source_head(fbx: str, escala: float, corte_y: float | None, ref: np.ndarray):
    """(tris, uv, tex, giro°, distancia media a `ref`): la fuente en Z arriba, escalada, con la base cortada y girada para calzar con `ref`."""
    from scipy.spatial import cKDTree

    from afm.fbxio import load_textured

    tm = load_textured(fbx)
    V = np.asarray(tm.vertices, float)
    F = np.asarray(tm.faces)
    keep = (V[F][:, :, 1].min(1) >= corte_y) if corte_y is not None else np.ones(len(F), bool)
    Vz = np.stack([V[:, 0], -V[:, 2], V[:, 1]], 1) * escala          # Y arriba -> Z arriba (rotación propia)
    uv = np.asarray(tm.corner_uv, float)[keep]
    tree = cKDTree(ref.reshape(-1, 3))
    best = None
    for yaw in (0, 90, 180, 270):
        c, s = np.cos(np.radians(yaw)), np.sin(np.radians(yaw))
        R = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
        W = Vz @ R.T
        W = W - W[F[keep]].reshape(-1, 3).min(0) * [0, 0, 0]
        T = W[F[keep]]
        T = T + (ref.reshape(-1, 3).max(0) - T.reshape(-1, 3).max(0)) * [0, 0, 1] + \
            ((ref.reshape(-1, 3).min(0) + ref.reshape(-1, 3).max(0)) / 2 - (T.reshape(-1, 3).min(0) + T.reshape(-1, 3).max(0)) / 2) * [1, 1, 0]
        d = tree.query(T.reshape(-1, 3)[::7])[0].mean()
        if best is None or d < best[0]:
            best = (d, yaw, T)
    return best[2], uv, tm.image("base_color"), best[1], float(best[0])


def main(argv=None) -> int:
    from PIL import Image

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--fbx", required=True)
    ap.add_argument("--glb", required=True)
    ap.add_argument("--out", default="cabeza_comparar.png")
    ap.add_argument("--escala", type=float, required=True)
    ap.add_argument("--corte-y", type=float, default=None, help="cota (en el FBX, Y arriba) del corte de la base; lo de abajo no se dibuja")
    ap.add_argument("--px-por-mm", type=float, default=12.0)
    a = ap.parse_args(argv)
    ptris, puv, ptex = head_piece(a.glb)
    stris, suv, stex, yaw, dist = source_head(a.fbx, a.escala, a.corte_y, ptris)
    print(f"giro de la fuente sobre Z: {yaw}°; distancia media fuente→pieza: {dist:.2f} mm")
    allv = np.concatenate([stris.reshape(-1, 3), ptris.reshape(-1, 3)])
    z0 = float(allv[:, 2].min())
    w = int(max(np.ptp(allv[:, 0]), np.ptp(allv[:, 1])) * a.px_por_mm) + 40
    h = int(np.ptp(allv[:, 2]) * a.px_por_mm) + 24
    cx = {v: float(np.median(ptris.reshape(-1, 3) @ np.cross(-np.asarray(VIEWS[v]), [0, 0, 1]))) for v in VIEWS}
    rows = []
    for v in VIEWS:
        top = render(stris, suv, stex, v, px_per_mm=a.px_por_mm, size=(w, h), z0=z0, cx=cx[v])
        bot = render(ptris, puv, ptex, v, px_per_mm=a.px_por_mm, size=(w, h), z0=z0, cx=cx[v])
        rows.append(np.concatenate([top, bot], axis=0))
    Image.fromarray(np.concatenate(rows, axis=1)).save(a.out)
    print("PNG:", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
