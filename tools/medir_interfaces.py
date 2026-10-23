"""Mide las interfaces (puertos) de cada figura y las compara entre figuras (plan de piezas intercambiables, Fase 0).

Para cada figura de `examples/` (y el muñeco sintético) calcula, sin construir la figura entera (solo carga, corte y cara
de contacto de cada miembro y del cuello): centro, eje, radio equivalente y área de cada puerto, margen del pasador en la cara
y la compatibilidad barata (`ports.port_compat`) de cada par de figuras. Con eso se deciden D3 (cortes estándar) y los
umbrales de IoU y escalón.

Uso: python tools/medir_interfaces.py [--figuras muneco guemes …] [--out docs]   → docs/MEDIDAS_interfaces.{md,json}
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from golden import FIGURES, _load_example  # noqa: E402

from afm import meshio, pipeline as PL, solids as S  # noqa: E402
from afm.limbs import contact_preview  # noqa: E402
from afm.ports import Port, PortFace, PortFrame, port_compat  # noqa: E402
from afm.ports import _r  # noqa: E402  (redondeo de listas)

PIN_OF = {"arm": ("hombro", "shoulder_pin"), "leg": ("cadera", "hip_pin")}


def _port_of_face(face, shape, plane, clase: str) -> Port:
    """Puerto (enchufe) a partir de la cara de contacto de un miembro: eje = `outward` (sale del miembro hacia el torso),
    referencia de giro = +Z proyectado en el plano (hombro, cadera) o +Y (cuello), centro = centroide de la cara."""
    axis = np.asarray(face.outward, float) / np.linalg.norm(face.outward)
    hint = np.array([0.0, 1.0, 0.0]) if clase == "cuello" else np.array([0.0, 0.0, 1.0])
    ref = hint - (hint @ axis) * axis
    ref = ref / np.linalg.norm(ref) if np.linalg.norm(ref) > 1e-6 else np.array([1.0, 0.0, 0.0])
    frame = PortFrame(np.asarray(face.center, float), axis, ref)
    pts = [frame.to_uv(t) for t in face.triangles]
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    shp = unary_union([q for q in (Polygon(p).buffer(0) for p in pts) if q.area > 1e-12])
    return Port(clase, "enchufe", frame, PortFace.from_shape(shp))


def toma_of(port: Port) -> Port:
    """La toma que recibe a `port`: misma cara, eje opuesto, misma referencia (la cara se ve espejada en v)."""
    from shapely import affinity
    f = port.frame
    face = PortFace.from_shape(affinity.scale(port.face.shape(), 1.0, -1.0, origin=(0, 0)))
    return Port(port.clase, "toma", PortFrame(f.center, -f.axis, f.ref), face, port.sweep_deg)


def measure(name: str) -> dict:
    """Puertos de una figura: {nombre: Port} y su altura."""
    if name == "muneco":
        from afm.project import demo_project
        from afm.testfigure import make_doll
        project, mesh = demo_project(), make_doll()
        rep_height = project.params.ref_height
    else:
        script, stl = FIGURES[name]
        project = _load_example(script)
        mesh, _rep = meshio.load_figure(os.path.join(ROOT, "examples", stl), project.params.ref_height, project.crown_z)
        rep_height = project.params.ref_height
    if project.front == "-Y":
        mesh.apply_transform([[-1, 0, 0, 0], [0, -1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])
    body = S.to_manifold(PL.align_to_symmetry(mesh, project.symmetry))
    half = body.trim_by_plane((1.0, 0.0, 0.0), 0.0)
    ports: dict[str, Port] = {}
    for spec in project.limbs:
        clase, _attr = PIN_OF[spec.kind]
        prev = contact_preview(half, spec, project.params.burr)
        ports[clase] = _port_of_face(prev.face, prev.shape, prev.plane, clase)
        ports[clase].sweep_deg = (0.0, project.params.sit_angle + project.params.hip_margin) if clase == "cadera" else None
    if project.head is not None:
        from afm.head import split_head
        sp = split_head(body, project.head, project.params)
        ports["cuello"] = _port_of_face(sp.face, None, sp.plane, "cuello")
    return {"altura_mm": rep_height, "puertos": ports, "params": project.params}


def _pin_margin(port: Port, params) -> float | None:
    from afm.limbs import pin_fit
    from afm.ports import PIN_OF_CLASS
    attr = PIN_OF_CLASS.get(port.clase)
    return None if attr is None else pin_fit(port.face.shape(), (0.0, 0.0), getattr(params, attr).shaft_d)[1]


def run(names: list[str]) -> dict:
    data = {n: measure(n) for n in names}
    out = {"figuras": {}, "pares": []}
    for n, d in data.items():
        out["figuras"][n] = {"altura_mm": d["altura_mm"], "puertos": {
            k: {"centro": _r(p.frame.center, 3), "eje": _r(p.frame.axis, 4), "radio_eq_mm": round(p.face.radius_eq, 3),
                "area_mm2": round(p.face.area, 2), "margen_pasador_mm": None if _pin_margin(p, d["params"]) is None
                else round(_pin_margin(p, d["params"]), 3)} for k, p in d["puertos"].items()}}
    for a, b in itertools.permutations(names, 2):         # a = quien aporta el enchufe, b = la toma de la otra figura
        for clase, pa in data[a]["puertos"].items():
            pb = data[b]["puertos"].get(clase)
            if pb is None:
                continue
            c = port_compat(pa, toma_of(pb), data[a]["altura_mm"], data[b]["altura_mm"], data[b]["params"])
            out["pares"].append({"enchufe_de": a, "toma_de": b, "puerto": clase, **c.to_dict()})
    return out


def to_markdown(res: dict) -> str:
    L = ["# Medidas de interfaces entre figuras", "",
         "_Generado por `tools/medir_interfaces.py` (plan de piezas intercambiables, Fase 0). Mm de la figura a su altura de "
         "referencia; el puerto sale de la cara de contacto de cada miembro (`contact_preview`) y del corte del cuello. "
         "El margen del pasador usa el pasador por defecto centrado en la cara (no el `pin_center` del proyecto)._", "",
         "## Puertos por figura", "", "| Figura | Puerto | Radio eq. (mm) | Área (mm²) | Margen pasador (mm) | Centro | Eje |",
         "|---|---|---|---|---|---|---|"]
    for n, f in res["figuras"].items():
        for k, p in f["puertos"].items():
            L.append(f"| {n} ({f['altura_mm']:g} mm) | {k} | {p['radio_eq_mm']} | {p['area_mm2']} | {p['margen_pasador_mm']} | "
                     f"{p['centro']} | {p['eje']} |")
    L += ["", "## Compatibilidad barata entre figuras (enchufe de A en la toma de B)", "",
          "| Enchufe de | Toma de | Puerto | Nivel | IoU | Escalón (mm) | Avisos / motivos |", "|---|---|---|---|---|---|---|"]
    for r in res["pares"]:
        L.append(f"| {r['enchufe_de']} | {r['toma_de']} | {r['puerto']} | {r['nivel']} | {r['iou']} | {r['escalon_mm']} | "
                 f"{'; '.join(r['motivos'] + r['avisos']) or '—'} |")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--figuras", nargs="+", default=list(FIGURES), choices=list(FIGURES))
    ap.add_argument("--out", default=os.path.join(ROOT, "docs"))
    args = ap.parse_args(argv)
    res = run(args.figuras)
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "MEDIDAS_interfaces.json"), "w") as f:
        json.dump(res, f, indent=2, ensure_ascii=False)
    with open(os.path.join(args.out, "MEDIDAS_interfaces.md"), "w") as f:
        f.write(to_markdown(res))
    print(to_markdown(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
