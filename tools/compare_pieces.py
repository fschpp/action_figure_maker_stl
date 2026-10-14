"""Compara una pieza generada con la del cliente (criterio Fase 0: distancia entre mallas).

Uso: python tools/compare_pieces.py mia.stl cliente.stl [--client-y-up]
Alinea por centro de caja + ICP de traslación y reporta mediana / p95 / máximo (mm) y volúmenes.
`--client-y-up`: las piezas del cliente (Onshape) vienen con Y arriba; se pasan a Z arriba.
"""
import argparse

import numpy as np
import trimesh
from scipy.spatial import cKDTree


def compare(mine: str, client: str, client_y_up: bool = False, n: int = 30000) -> dict:
    a = trimesh.load(mine, force="mesh")
    b = trimesh.load(client, force="mesh")
    pa, pb = a.sample(n), b.sample(n)
    if client_y_up:
        pb = np.column_stack([pb[:, 0], pb[:, 2], pb[:, 1]])
    pb = pb - (pb.max(0) + pb.min(0)) / 2 + (pa.max(0) + pa.min(0)) / 2
    t = np.zeros(3)
    ta = cKDTree(pa)
    for _ in range(15):
        _, i = ta.query(pb + t)
        t += (pa[i] - (pb + t)).mean(0)
    d1, _ = ta.query(pb + t)
    d2, _ = cKDTree(pb + t).query(pa)
    d = np.r_[d1, d2]
    return {"mediana": float(np.median(d)), "p95": float(np.percentile(d, 95)), "max": float(d.max()),
            "volumen_mio": abs(a.volume), "volumen_cliente": abs(b.volume), "traslacion": t.round(2).tolist()}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mia")
    ap.add_argument("cliente")
    ap.add_argument("--client-y-up", action="store_true")
    r = compare(**{"mine": ap.parse_args().mia, "client": ap.parse_args().cliente,
                   "client_y_up": ap.parse_args().client_y_up})
    for k, v in r.items():
        print(f"{k}: {v}")
