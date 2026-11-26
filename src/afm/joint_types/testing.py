"""Kit de conformidad de un tipo de articulación (plan de plugins §3.8).

`check_type(t)` arma el tipo sobre un corte sintético (un bloque hijo pegado a un bloque padre) y devuelve la lista de verificaciones
con su resultado. Lo corre un test sobre CADA tipo de `REGISTRY` (también los internos): quien escribe un tipo nuevo lo prueba igual.

Hecho en la Fase 0: 1 determinismo · 2 piezas y cortadores válidos (manifold), volumen > 0, sin NaN · 3 giro libre en todas las poses del recorrido ·
5 pared del padre alrededor de la cavidad · 6 espejo (`S.mirror_x`) · 7 `requires()` coherente · 8 solo importa la superficie pública
permitida. Falta (necesita `Motion` general, Fase 2): 4 retención por desplazamiento ±1 mm.
"""
from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass

import numpy as np

from .. import solids as S
from ..limbs import ContactFace
from ..params import Params
from .base import JointSite

ALLOWED_IMPORTS = {"afm", "numpy", "shapely", "dataclasses", "typing", "__future__", "math"}
FREE_TURN_TOL_MM3 = 1e-4
TURN_STEP_DEG = 5.0


@dataclass(frozen=True)
class Check:
    n: int
    name: str
    ok: bool
    detail: str = ""


def synthetic_site(t, slot: str | None = None, params: Params | None = None, flex: tuple = (0.0, 20.0),
                   side: str = "izq") -> tuple[JointSite, S.Manifold]:
    """Un corte sintético: la hija es un bloque en x ≥ 0 y el padre otro en x ≤ 0; la cara de contacto es el plano x = 0."""
    p = params or Params()
    slot = slot or sorted(t.slots)[0]
    child = S.Manifold.cube((14.0, 16.0, 30.0)).translate((0.0, -8.0, -30.0))
    parent = S.Manifold.cube((20.0, 40.0, 60.0)).translate((-20.0, -20.0, -45.0))
    face = ContactFace(center=np.array([0.0, 0.0, -8.0]), outward=np.array([-1.0, 0.0, 0.0]), area=16.0 * 30.0)
    site = JointSite(slot=slot, side=side, child=child, face=face, params=p, jp=t.default_params(p, slot), parent=parent,
                     flex=flex)
    return site, parent


def _volume(mf: S.Manifold) -> float:
    return float(mf.volume())


def _sound(mf: S.Manifold) -> tuple[bool, str]:
    """Pieza sana: manifold válido (`status()` sin error), sin NaN y con volumen > 0. El soldado de aristas para el laminador lo
    resuelve el motor al exportar (`S.to_printable_trimesh`, pymeshfix), no el tipo: una pieza recién salida de las booleanas puede
    dejar aristas de más de 2 caras que ahí se limpian."""
    if str(mf.status()) != "Error.NoError":
        return False, f"manifold inválido: {mf.status()}"
    if not np.isfinite(S.to_trimesh(mf).vertices).all():
        return False, "vértices con NaN o infinito"
    if _volume(mf) <= 0:
        return False, f"volumen {_volume(mf):.4g} ≤ 0"
    return True, ""


def _signature(r) -> dict:
    return {"pieces": {k: round(_volume(v), 9) for k, v in sorted(r.pieces.items())},
            "cutters": {k: [round(_volume(c), 9) for c in v] for k, v in sorted(r.cutters.items())}}


def check_type(t, site: JointSite | None = None, parent: S.Manifold | None = None) -> list[Check]:
    """Corre las verificaciones sobre `t` y devuelve una `Check` por cada una (no lanza: un fallo es `ok=False`)."""
    if site is None:
        site, parent = t.test_site() if hasattr(t, "test_site") else synthetic_site(t)
    out: list[Check] = []

    try:
        r1, r2 = t.build(site), t.build(site)
    except Exception as exc:        # un tipo que no arma no pasa ninguna otra
        return [Check(1, "determinismo", False, f"build lanzó {type(exc).__name__}: {exc}")]
    s1, s2 = _signature(r1), _signature(r2)
    out.append(Check(1, "determinismo", s1 == s2, "" if s1 == s2 else "dos corridas dieron volúmenes distintos"))

    items = list(r1.pieces.items()) + [(f"cortador {k}[{i}]", c) for k, cs in r1.cutters.items() for i, c in enumerate(cs)]
    bad = [f"{k}: {why}" for k, v in items for ok, why in [_sound(v)] if not ok]
    out.append(Check(2, "piezas y cortadores válidos (manifold), volumen > 0, sin NaN", not bad, "; ".join(bad)))

    # Contra qué gira la hija: la pieza proximal (si el tipo parte el miembro: bisagra) o el padre ya tallado por sus cortadores
    target = r1.pieces.get("prox")
    if target is None and parent is not None:
        target = parent
        for c in r1.cutters.get("parent", []):
            target = S.difference(target, c)
    worst, free = 0.0, True
    if target is not None:
        motion = r1.motion.revolute()
        for T in (motion.poses(TURN_STEP_DEG) if motion is not None else []):
            moved = r1.motion.limb.transform(T[:3, :])
            worst = max(worst, _volume(S.intersection(moved, target)))
        free = worst < FREE_TURN_TOL_MM3
    out.append(Check(3, "giro libre en todo el recorrido", free, f"interferencia máxima {worst:.4g} mm³" if not free else ""))

    wall_ok, wall_msg = True, ""
    if parent is not None:
        cav = S.union(*r1.cutters.get("parent", [S.Manifold()]))
        left = S.difference(parent, cav)
        # el padre debe conservar material: la cavidad resta algo y no se lleva todo
        wall_ok = _volume(left) > 0 and _volume(left) < _volume(parent)
        wall_msg = "" if wall_ok else "la cavidad no resta nada o se lleva todo el padre"
    out.append(Check(5, "el padre conserva material alrededor de la cavidad", wall_ok, wall_msg))

    mirrored = {k: S.mirror_x(v) for k, v in r1.pieces.items()}
    mir_ok = all(abs(_volume(mirrored[k]) - _volume(v)) < 1e-6 for k, v in r1.pieces.items())
    out.append(Check(6, "espejo: el lado der es mirror_x del izq", mir_ok, "" if mir_ok else "mirror_x cambió un volumen"))

    try:
        req = t.requires(site.jp, site.params, site.slot)
        d = req.to_dict() if req is not None else None      # None: el tipo no le pide nada al anfitrión
        req_ok = d is None or (all(np.isfinite(v) and v >= 0 for v in d.values()) and d["profundidad_mm"] > 0)
        out.append(Check(7, "requires() coherente", bool(req_ok), "" if req_ok else f"valores no válidos: {d}"))
    except Exception as exc:
        out.append(Check(7, "requires() coherente", False, f"lanzó {type(exc).__name__}: {exc}"))

    out.append(_check_imports(t))
    return out


def _check_imports(t) -> Check:
    """Revisión estática: el módulo del tipo solo importa la superficie pública permitida (nunca `manifold3d` directo)."""
    try:
        src = inspect.getsource(inspect.getmodule(type(t)))
    except (OSError, TypeError):
        return Check(8, "solo importa la superficie permitida", True, "sin fuente disponible (no se revisó)")
    bad = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            bad += [a.name for a in node.names if a.name.split(".")[0] not in ALLOWED_IMPORTS]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module.split(".")[0] not in ALLOWED_IMPORTS:
                bad.append(node.module)
    return Check(8, "solo importa la superficie permitida", not bad, ", ".join(sorted(set(bad))))


def failures(checks: list[Check]) -> list[Check]:
    return [c for c in checks if not c.ok]
