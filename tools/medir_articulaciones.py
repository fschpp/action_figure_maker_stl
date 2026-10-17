#!/usr/bin/env python3
"""Mide las articulaciones de la figura de referencia del cliente (Link, ≈128 mm) a partir de sus STL.

Reproduce `docs/MEDIDAS_figura_referencia.md` (plan de articulaciones, Fase 0). Uso:

    python tools/medir_articulaciones.py DIRECTORIO_CON_STL [--out medidas.json]

Convenciones: 1 unidad del STL = 100 mm (se escala a mm); la figura mira hacia −Y; las piezas están
despiezadas, así que las holguras se miden dentro de cada pieza (agujero vs. pasador, ranura vs. lengüeta).
Método: cortes planos perpendiculares a X para hallar círculos (agujeros / pasadores) y ajustar el eje
(recta), y rayos a lo largo de ese eje (o de X, o de Z) para espesores, ranuras y paredes.
Necesita trimesh, numpy, shapely y rtree.
"""
from __future__ import annotations

import argparse
import json
import os

import numpy as np
import trimesh
from shapely.geometry import Polygon

PIEZAS = ["torso_adelante_v4", "torso_atras_v4", "cadera_adelante_v1", "cadera_atras_v1", "cabeza_v2",
          "muslo_0002", "bota_volume_0005", "antebrazo_003", "brazomano"]
# Eje de cada articulación (y, z en mm; semilla para buscar los círculos) y piezas que la forman.
SEMILLAS = {"rodilla": (-6.2, 37.2), "codo": (-2.2, 77.3), "cadera": (-3.81, 60.7)}
_cache: dict = {}


def cargar(directorio: str, nombre: str) -> trimesh.Trimesh:
    if nombre not in _cache:
        m = trimesh.load(os.path.join(directorio, nombre + ".stl"), process=False)
        m.merge_vertices()
        m.apply_scale(100.0)
        _cache[nombre] = m
    return _cache[nombre]


def tramos(mesh, origen, d, span=(-40, 40)):
    """Tramos (t0, t1) de la recta origen + t·d dentro del sólido (t en mm, relativo al origen)."""
    d = np.asarray(d, float) / np.linalg.norm(d)
    o = np.asarray(origen, float) + d * span[0]
    loc, _, _ = mesh.ray.intersects_location([o], [d], multiple_hits=True)
    if len(loc) == 0:
        return []
    t = np.sort(((loc - o) @ d) + span[0])
    t = [x for i, x in enumerate(t) if i == 0 or abs(x - t[i - 1]) > 1e-3]
    if len(t) % 2:
        return []
    return [(round(float(a), 3), round(float(b), 3)) for a, b in zip(t[0::2], t[1::2])]


def _circulo(yz):
    A = np.c_[2 * yz, np.ones(len(yz))]
    sol, *_ = np.linalg.lstsq(A, (yz ** 2).sum(1), rcond=None)
    c = sol[:2]
    r = float(np.sqrt(sol[2] + c @ c))
    return c, r, float(np.abs(np.linalg.norm(yz - c, axis=1) - r).max())


def _es_agujero(yz, todos) -> bool:
    pg = Polygon(yz).buffer(0)
    n = sum(1 for o in todos if o is not yz and Polygon(o).buffer(0).area > pg.area
            and Polygon(o).buffer(0).contains(pg.representative_point()))
    return n % 2 == 1


def circulos(m, semilla, tol, rmin=0.8, rmax=7.0, paso=0.25):
    """Círculos (corte x = const) cerca de `semilla` (y, z). Devuelve dicts con x, centro, Ø en Y y si es agujero."""
    out = []
    for x in np.arange(m.bounds[0][0] + paso, m.bounds[1][0], paso):
        s = m.section(plane_origin=[x, 0, 0], plane_normal=[1, 0, 0])
        if s is None:
            continue
        L = [p[:, 1:3] for p in s.discrete if len(p) >= 8]
        for yz in L:
            c, r, res = _circulo(yz)
            if rmin < r < rmax and res < 0.15 * r and np.hypot(c[0] - semilla[0], c[1] - semilla[1]) < tol:
                out.append({"x": float(x), "cy": float(c[0]), "cz": float(c[1]), "r": r,
                            "d_y": float(np.ptp(yz[:, 0])), "agujero": bool(_es_agujero(yz, L))})
    return out


def eje_inclinado(m, semilla, tol):
    """Recta del eje de los agujeros: z = z0 + pendiente·x (y constante). La inclinación es sobre Y."""
    cs = [c for c in circulos(m, semilla, tol) if c["agujero"]]
    if len(cs) < 3:
        return None
    x = np.array([c["x"] for c in cs]); z = np.array([c["cz"] for c in cs]); y = np.array([c["cy"] for c in cs])
    sl, z0 = np.polyfit(x, z, 1)
    xr = float(x.mean())
    segs = sorted({round(c["x"], 1) for c in cs})
    return {"alfa_deg": round(float(np.degrees(np.arctan(sl))), 2), "pendiente": round(float(sl), 4),
            "punto": [round(xr, 2), round(float(y.mean()), 2), round(float(sl * xr + z0), 2)],
            "diametro_agujero_mm": round(float(np.median([c["d_y"] for c in cs])), 2),
            "x_min": round(float(x.min()), 2), "x_max": round(float(x.max()), 2), "n": len(cs), "x_muestras": segs[:1] + segs[-1:]}


def rayos_axiales(m, eje, rho=2.0):
    """Tramos de material a lo largo de rectas paralelas al eje, a `rho` mm de él (8 direcciones)."""
    a = np.radians(eje["alfa_deg"])
    d = np.array([np.cos(a), 0, np.sin(a)]); n1 = np.array([0, 1, 0]); n2 = np.array([-np.sin(a), 0, np.cos(a)])
    P = np.array(eje["punto"], float)
    return {str(ang): tramos(m, P + rho * (np.cos(np.radians(ang)) * n1 + np.sin(np.radians(ang)) * n2), d, (-14, 14))
            for ang in range(0, 360, 45)}


def _mediana(vals):
    return round(float(np.median(vals)), 2) if len(vals) else None


def lengueta(rayos):
    """Espesor de la lengüeta (un solo tramo por rayo): mediana y rango."""
    esp = [r[0][1] - r[0][0] for r in rayos.values() if len(r) == 1]
    return {"espesor_mediana": _mediana(esp), "espesor_min": round(min(esp), 2) if esp else None,
            "espesor_max": round(max(esp), 2) if esp else None, "n": len(esp)}


def ranura(rayos):
    """Ancho de la ranura (hueco entre los dos primeros tramos de cada rayo) y espesor de las paredes."""
    gaps = [r[1][0] - r[0][1] for r in rayos.values() if len(r) >= 2]
    return {"ancho_mediana": _mediana(gaps), "ancho_min": round(min(gaps), 2) if gaps else None,
            "ancho_max": round(max(gaps), 2) if gaps else None, "n": len(gaps)}


def medir_bisagra(dirp, nombre, semilla, tol=6.0):
    m = cargar(dirp, nombre)
    eje = eje_inclinado(m, semilla, tol)
    if eje is None:
        return {"error": "sin agujeros cerca de la semilla"}
    r = rayos_axiales(m, eje)
    return {"eje": eje, "lengueta": lengueta(r), "ranura": ranura(r), "rayos_rho2": r}


def medir_cadera_muslo(dirp, y0=-3.81, z0=60.7):
    m = cargar(dirp, "muslo_0002")
    cs = circulos(m, (y0, z0), 3, rmin=1.5, rmax=6, paso=0.2)
    cab = [c for c in cs if not c["agujero"] and c["r"] > 4]
    ax = tramos(m, (0, y0, z0), (1, 0, 0), (-1, 30))
    head = tramos(m, (0, y0 + 3.0, z0), (1, 0, 0), (-1, 30))
    return {"cabeza_d_mm": round(float(np.median([c["d_y"] for c in cab])), 2),
            "cabeza_x": [round(head[0][0], 3), round(head[0][1], 3)] if head else None,
            "centro_yz": [round(float(np.median([c["cy"] for c in cab])), 2), round(float(np.median([c["cz"] for c in cab])), 2)],
            "tramos_sobre_el_eje_x": [(a, b) for a, b in ax],
            "tramos_a_3mm_del_eje": [(a, b) for a, b in head]}


def medir_pelvis(dirp):
    out = {}
    for nombre, y in (("cadera_adelante_v1", -3.40), ("cadera_atras_v1", -3.30)):
        m = cargar(dirp, nombre)
        col = {}
        for x in (0.0, 3.4, 4.2):
            iv = tramos(m, (x, y, 40), (0, 0, 1), (0, 45))
            col[str(x)] = [(round(a + 40, 2), round(b + 40, 2)) for a, b in iv]
        filas = {str(z): [(round(a - 12, 2), round(b - 12, 2)) for a, b in tramos(m, (-12, y, z), (1, 0, 0), (0, 26))]
                 for z in (57.0, 64.5)}
        bolsillo = col["0.0"][0][1], col["0.0"][1][0]
        canal = col["4.2"][0][1], col["4.2"][1][0]
        out[nombre] = {"y_corte": y, "bolsillo_z": bolsillo, "bolsillo_alto_mm": round(bolsillo[1] - bolsillo[0], 2),
                       "canal_z": canal, "canal_alto_mm": round(canal[1] - canal[0], 2),
                       "pared_x": filas["57.0"], "columnas_z": col,
                       "bbox_mm": m.bounds.round(2).tolist()}
        # el bolsillo es un cilindro de eje X cuyo eje cae sobre el plano de unión: radio a partir de la cuerda a otro y
        ys = (y, -4.5, -6.0) if "adelante" in nombre else (y, -2.0, -0.5)
        cu = []
        for yy in ys:
            iv = tramos(m, (0.0, yy, 40), (0, 0, 1), (0, 45))
            cu.append((yy, round(iv[1][0] + 40 - (iv[0][1] + 40), 2)))
        out[nombre]["cuerda_bolsillo_vs_y"] = cu
    return out


def resumen_piezas(dirp):
    out = {}
    for n in PIEZAS:
        m = cargar(dirp, n)
        out[n] = {"triangulos": int(len(m.faces)), "estanca": bool(m.is_watertight),
                  "bbox_min_mm": m.bounds[0].round(2).tolist(), "bbox_max_mm": m.bounds[1].round(2).tolist(),
                  "extension_mm": (m.bounds[1] - m.bounds[0]).round(2).tolist(),
                  "volumen_mm3": round(float(m.volume), 1) if m.is_watertight else None,
                  "componentes": len(m.split(only_watertight=False))}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir")
    ap.add_argument("--out", default="medidas_articulaciones.json")
    a = ap.parse_args(argv)
    res = {"piezas": resumen_piezas(a.dir),
           "rodilla": {"bota (lengüeta)": medir_bisagra(a.dir, "bota_volume_0005", SEMILLAS["rodilla"], 8),
                       "muslo (ranura)": medir_bisagra(a.dir, "muslo_0002", SEMILLAS["rodilla"], 8)},
           "codo": {"brazomano (lengüeta)": medir_bisagra(a.dir, "brazomano", SEMILLAS["codo"]),
                    "antebrazo_003 (ranura)": medir_bisagra(a.dir, "antebrazo_003", SEMILLAS["codo"])},
           "cadera": {"muslo (pasador)": medir_cadera_muslo(a.dir), "pelvis (cavidad)": medir_pelvis(a.dir)}}
    with open(a.out, "w") as f:
        json.dump(res, f, indent=1, default=float)
    print(f"escrito {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
