"""Guía de cortes: cuánto del catálogo acepta un corte (plan de compatibilidad, `docs/PLAN_compatibilidad_cortes_catalogo.md`, Fases 2 y 3).

Dos cosas, las dos sin construir la figura entera (`build`) y sin GTK:

* **Cobertura** (`coverage_of_port`, `piece_coverage`): para un puerto —el de una pieza ya catalogada o el que saldría de
  un corte— cuenta cuántas piezas del catálogo con el puerto opuesto lo aceptan, según la compatibilidad barata
  (`ports.port_compat`: clase, IoU de las caras, escalón, margen del pasador, altura). Solo mira metadatos.
* **Medición del corte** (`measure_cuts`): la cara real donde cada miembro (y el cuello) se separa del cuerpo, antes de
  catalogar nada: radio equivalente y del círculo inscrito, posición lateral, margen del pasador y la cobertura de esa
  cara contra el catálogo, tanto como enchufe (la pieza que saldría de esta figura) como toma (el torso de esta figura
  recibiendo piezas de otras).

La cobertura es un nivel N0 del plan (informado, no garantizado). Con una calibración (`calibracion.json`, que escribirá
`tools/calibrar_compat.py`, Fase 0) suma la probabilidad *esperada* de que la compatibilidad completa (`build` +
`validate`) también pase: límite inferior de Wilson al 95 % de lo observado por clase y nivel.
"""
from __future__ import annotations

import dataclasses
import json
import math
import os
from dataclasses import dataclass, field

import numpy as np

from . import solids as S
from .catalog import Catalog
from .ports import COMPLEMENT, PIN_OF_CLASS, Compat, Port, PortFace, PortFrame, pin_diagnosis, port_compat
from .params import Params

CALIB_FORMAT = "afm-calibracion-1"
CALIB_FILE = "calibracion.json"
LEVEL_RANK = {"ok": 0, "aviso": 1, "no": 2}


# ---------------------------------------------------------------------------------------------------- cobertura
@dataclass
class PieceFit:
    """Cómo se acopla el puerto medido con UNA pieza del catálogo (el peor de sus puertos de esa clase)."""

    ref: str
    tipo: str
    puerto: str                 # nombre del puerto de esa pieza que dio el peor resultado
    compat: Compat

    @property
    def nivel(self) -> str:
        return self.compat.nivel

    def motivo(self) -> str:
        c = self.compat
        return (c.motivos or c.avisos or [""])[0]


@dataclass
class Coverage:
    """Resultado de medir un puerto contra el catálogo."""

    clase: str
    rol: str
    fits: list = field(default_factory=list)       # list[PieceFit], ordenadas: ok, aviso, no
    esperada: float | None = None                  # probabilidad esperada de que la completa también pase (con calibración)
    n_calibracion: int | None = None               # combinaciones completas de esta clase que respaldan `esperada`

    @property
    def universo(self) -> int:
        return len(self.fits)

    def count(self, nivel: str) -> int:
        return sum(1 for f in self.fits if f.nivel == nivel)

    @property
    def ok(self) -> int:
        return self.count("ok")

    @property
    def aviso(self) -> int:
        return self.count("aviso")

    @property
    def no(self) -> int:
        return self.count("no")

    @property
    def cobertura(self) -> float:
        """Fracción de piezas que no dan «no» (ok o aviso). 0 si no hay piezas con el puerto opuesto."""
        return (self.ok + self.aviso) / self.universo if self.universo else 0.0

    @property
    def cobertura_plena(self) -> float:
        return self.ok / self.universo if self.universo else 0.0

    def summary(self) -> str:
        """Una línea: «compatible con 3 de 4 piezas (75 %) · ok pleno 1 · esperada ≈ 62 % (n = 12)»."""
        if not self.universo:
            return f"sin piezas con puerto «{self.clase}» de rol {COMPLEMENT[self.rol]} en el catálogo"
        n_ok = self.ok + self.aviso
        txt = (f"compatible con {n_ok} de {self.universo} pieza{'s' if self.universo != 1 else ''} "
               f"({100 * self.cobertura:.0f} %) · ok pleno {self.ok}")
        if self.esperada is not None:
            txt += f" · esperada ≈ {100 * self.esperada:.0f} % (n = {self.n_calibracion})"
        return txt


def wilson_lower(k: int, n: int, z: float = 1.96) -> float:
    """Límite inferior del intervalo de Wilson para una proporción k/n (z = 1,96: 95 %). 0 si n = 0."""
    if n <= 0:
        return 0.0
    p = k / n
    z2 = z * z
    centre = p + z2 / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    return max(0.0, (centre - margin) / (1 + z2 / n))


def load_calibration(root: str) -> dict | None:
    """`<catálogo>/calibracion.json` (`afm-calibracion-1`): `{"clases": {clase: {nivel: [pasaron, total]}}}`. None si no hay
    o no se puede leer (la guía sigue funcionando, solo sin la «esperada»)."""
    try:
        with open(os.path.join(root, CALIB_FILE), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("formato") != CALIB_FORMAT or not isinstance(data.get("clases"), dict):
        return None
    return data["clases"]


def expected_coverage(fits: list, clase: str, calib: dict | None) -> tuple[float | None, int | None]:
    """(esperada, n): promedio, sobre las piezas del universo, del límite de Wilson de que la completa pase dado el nivel
    barato. Un «no» sin dato cuenta 0; si falta el dato de un «ok» o un «aviso» no se puede calcular (None)."""
    if not calib or clase not in calib or not fits:
        return None, None
    table = calib[clase]
    total = 0.0
    for f in fits:
        km = table.get(f.nivel)
        if km is None:
            if f.nivel == "no":
                continue
            return None, None
        total += wilson_lower(int(km[0]), int(km[1]))
    n = sum(int(v[1]) for v in table.values())
    return total / len(fits), n


def coverage_of_port(port: Port, height_mm: float | None, cat: Catalog, params: Params | None = None,
                     exclude_ids=(), exclude_figure: str | None = None, mirrorable: bool = True,
                     calib: dict | None = None, port_name: str = "") -> Coverage:
    """Cobertura de `port` (de una pieza o de un corte) contra las piezas del catálogo con el puerto opuesto.

    El universo son las últimas versiones de cada id, sin `exclude_ids` ni las piezas cuya figura de origen es
    `exclude_figure` (una figura siempre encaja con sus propias piezas). De cada pieza se toma el peor de sus puertos
    de la misma clase y rol complementario. Mismo criterio de espejo que `Catalog.compatible_with` (así los números
    coinciden en todas partes): si el puerto medido es de una pieza espejable (`mirrorable`) se saltean los `_der` de las
    otras piezas, y si `port_name` es un `_der` se saltean las otras piezas espejables (ya se miden por el lado izquierdo).
    """
    params = params or cat.params
    want = COMPLEMENT[port.rol]
    fits: list[PieceFit] = []
    for other in cat.list():
        if other.id in exclude_ids or (exclude_figure and other.origen.get("figura") == exclude_figure):
            continue
        worst: PieceFit | None = None
        for name, q in other.puertos.items():
            if q.clase != port.clase or q.rol != want:
                continue
            if (name.endswith("_der") and mirrorable) or (port_name.endswith("_der") and other.espejable):
                continue
            c = port_compat(port, q, height_mm, other.altura_figura_mm, params)
            cand = PieceFit(other.ref, other.tipo, name, c)
            if worst is None or (LEVEL_RANK[c.nivel], -(c.iou or 0.0)) > (LEVEL_RANK[worst.nivel], -(worst.compat.iou or 0.0)):
                worst = cand
        if worst is not None:
            fits.append(worst)
    fits.sort(key=lambda f: (LEVEL_RANK[f.nivel], -(f.compat.iou or 0.0), f.ref))
    esperada, n = expected_coverage(fits, port.clase, calib)
    return Coverage(port.clase, port.rol, fits, esperada, n)


def piece_coverage(cat: Catalog, ref: str, calib: dict | None = None) -> dict:
    """Cobertura de cada puerto de una pieza del catálogo: `{nombre del puerto: Coverage}`.

    Excluye la pieza misma y las de su propia figura de origen. En una pieza espejable solo se mide el lado izquierdo; el
    `_der` de una pieza que no lo es se omite cuando no queda ninguna pieza contra la que medirlo (las otras son espejables
    y ya se midieron por el `_izq`)."""
    me = cat.meta(ref)
    fig = me.origen.get("figura") or None
    if calib is None:
        calib = load_calibration(cat.root)
    out = {}
    for name, port in me.puertos.items():
        if name.endswith("_der") and me.espejable:
            continue
        cov = coverage_of_port(port, me.altura_figura_mm, cat, cat.params, exclude_ids=(me.id,), exclude_figure=fig,
                               mirrorable=me.espejable, calib=calib, port_name=name)
        if name.endswith("_der") and not cov.universo and name[:-4] + "_izq" in me.puertos:
            continue
        out[name] = cov
    return out


# ---------------------------------------------------------------------------------------------------- medición del corte
@dataclass
class CutMeasure:
    """La cara de un corte medida sobre la figura, antes de catalogar."""

    lugar: str                          # «brazo», «pierna» o «cabeza» (cuello)
    clase: str                          # «hombro», «cadera» o «cuello»
    port: Port | None = None            # enchufe de la pieza que saldría de esta figura (con su cara)
    r_eq: float | None = None           # radio del círculo de la misma área que la cara (mm)
    r_inscrito: float | None = None     # radio del mayor círculo que entra en la cara (mm)
    x_mm: float | None = None           # |x| del centro del puerto (posición lateral de la cara)
    area_mm2: float | None = None
    margen_pasador: float | None = None  # mm entre el vástago y el borde de la cara (negativo: sobresale)
    cara_chica: tuple | None = None     # (Ø del mayor círculo, Ø necesario con pared): el pasador no entra en ninguna posición
    avisos: list = field(default_factory=list)
    error: str | None = None
    as_plug: Coverage | None = None     # la pieza de esta figura contra las tomas del catálogo
    as_socket: Coverage | None = None   # el torso de esta figura contra los enchufes del catálogo

    def socket_port(self) -> Port | None:
        """La toma que esta cara deja en el torso de la figura: mismo centro, eje opuesto, cara con v invertida."""
        if self.port is None:
            return None
        from .extract import complement
        return complement(self.port)


def _face_port(face, clase: str, center, ref_prefer) -> Port:
    """Puerto (enchufe) a partir de la cara de contacto: eje = `outward`, centro dado, cara en (u, v) del puerto."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    from .extract import reference_for

    axis = np.asarray(face.outward, float)
    axis = axis / np.linalg.norm(axis)
    frame = PortFrame(np.asarray(center, float), axis, reference_for(axis, ref_prefer))
    polys = [q for q in (Polygon(frame.to_uv(t)).buffer(0) for t in face.triangles) if q.area > 1e-12]
    if not polys:
        raise ValueError("la cara de corte no tiene área")
    return Port(clase, "enchufe", frame, PortFace.from_shape(unary_union(polys)))


def _measure(lugar: str, clase: str, face, center, ref_prefer, params: Params) -> CutMeasure:
    m = CutMeasure(lugar, clase)
    try:
        if face.triangles is None or len(face.triangles) == 0:
            raise ValueError("la cara de corte no trae sus triángulos")
        m.port = _face_port(face, clase, center, ref_prefer)
    except ValueError as exc:
        m.error = str(exc)
        return m
    pf = m.port.face
    m.r_eq, m.area_mm2, m.x_mm = pf.radius_eq, pf.area, abs(float(m.port.frame.center[0]))
    attr = PIN_OF_CLASS.get(clase)
    pin = getattr(params, attr) if attr else None
    if pin is not None:
        g = pin_diagnosis(pf, pin.shaft_d)
        m.r_inscrito = g["circulo_d"] / 2
        m.margen_pasador = g["margen"]
        if g["margen"] < 0:
            m.avisos.append(f"el vástago de Ø{pin.shaft_d:g} mm sobresale {-g['margen']:.2f} mm de la cara")
            if g["circulo_d"] < pin.shaft_d:
                m.cara_chica = (g["circulo_d"], pin.shaft_d + 2 * params.min_wall)
        elif g["margen"] < params.min_wall:
            m.avisos.append(f"pared de {g['margen']:.2f} mm alrededor del vástago (mínimo {params.min_wall:g} mm)")
    else:
        g = pin_diagnosis(pf, 0.0)
        m.r_inscrito = g["circulo_d"] / 2
    return m


def measure_cuts(project, body: S.Manifold, cat: Catalog | None = None, params: Params | None = None,
                 exclude_figure: str | None = None, calib: dict | None = None) -> list:
    """Mide los cortes de una figura (miembros y cuello) sin generar nada: `[CutMeasure]`.

    `body`: el cuerpo normalizado (`extract.load_body`). Con `cat`, cada corte lleva además su cobertura contra el catálogo
    como enchufe (`as_plug`) y como toma (`as_socket`). Hace lo mismo que `extract.extract_all` hasta la cara de cada corte:
    mismo recorte de la mitad de trabajo, misma rebaba, mismo centro del pasador."""
    from .extract import _pick_limbs
    from .head import neck_plane, split_head
    from .limbs import contact_preview
    from .planes import Plane

    p = params or project.params
    if calib is None and cat is not None:
        calib = load_calibration(cat.root)
    warnings: list = []
    limbs = _pick_limbs(project, warnings)
    half = body.trim_by_plane((1.0, 0.0, 0.0), 0.0)
    out: list[CutMeasure] = []

    neck = None
    if project.head is not None:
        neck = neck_plane(project.head)
        half = S.intersection(half, S.halfspace(Plane.from_normal(neck.origin, -neck.normal)))
        try:
            hs = dataclasses.replace(project.head, replacement_path=None)
            split = split_head(body, hs, p)
            out.append(_measure("cabeza", "cuello", split.face, split.face.center, (0.0, 1.0, 0.0), p))
        except ValueError as exc:
            m = CutMeasure("cabeza", "cuello")
            m.error = str(exc)
            out.append(m)

    for kind, spec in limbs.items():
        place = "brazo" if kind == "arm" else "pierna"
        clase = "hombro" if kind == "arm" else "cadera"
        try:
            prev = contact_preview(half, spec, p.burr)
        except ValueError as exc:
            m = CutMeasure(place, clase)
            m.error = str(exc)
            out.append(m)
            continue
        center = np.asarray(spec.pin_center, float) if spec.pin_center is not None else np.asarray(prev.default_center, float)
        center = center - ((center - prev.face.center) @ prev.face.outward) * prev.face.outward   # sobre el plano de la cara
        out.append(_measure(place, clase, prev.face, center, (0.0, 0.0, 1.0), p))

    if cat is not None:
        for m in out:
            if m.port is None:
                continue
            kw = dict(cat=cat, params=cat.params, exclude_figure=exclude_figure, calib=calib)
            m.as_plug = coverage_of_port(m.port, p.ref_height, **kw)
            m.as_socket = coverage_of_port(m.socket_port(), p.ref_height, **kw)
    return out


# ---------------------------------------------------------------------------------------------------- texto
def _c(x: float, nd: int = 2) -> str:
    return f"{x:.{nd}f}".replace(".", ",")


def format_fit(f: PieceFit) -> str:
    c = f.compat
    det = ""
    if c.iou is not None:
        det = f" (IoU {_c(c.iou)}, escalón {_c(c.escalon_mm)} mm)"
    why = f.motivo()
    return f"{f.ref:<28} {f.puerto:<12} {f.nivel}{det}" + (f" — {why}" if f.nivel != "ok" and why else "")


def format_coverage(cov: Coverage, indent: str = "    ", all_fits: bool = False) -> list:
    """Líneas de texto de una cobertura: el resumen y las piezas que no son «ok» (todas con `all_fits`)."""
    lines = [indent + cov.summary()]
    for f in cov.fits:
        if all_fits or f.nivel != "ok":
            lines.append(f"{indent}  {format_fit(f)}")
    return lines


def format_measure(m: CutMeasure, all_fits: bool = False) -> list:
    """Líneas de texto de un corte medido (para `afm guia`)."""
    head = f"{m.lugar} ({m.clase})"
    if m.error:
        return [f"{head}: no se pudo medir — {m.error}"]
    parts = [f"radio eq {_c(m.r_eq)} mm"]
    if m.r_inscrito is not None:
        parts.append(f"inscrito {_c(m.r_inscrito)} mm")
    parts.append(f"x {_c(m.x_mm)} mm")
    if m.margen_pasador is not None:
        parts.append(f"margen del pasador {_c(m.margen_pasador)} mm")
    lines = [f"{head}: " + " · ".join(parts)]
    for a in m.avisos:
        lines.append(f"  AVISO: {a}")
    if m.cara_chica:
        lines.append(f"  AVISO: la cara admite un círculo de Ø{_c(m.cara_chica[0], 1)} mm y el pasador con pared pide "
                     f"Ø{_c(m.cara_chica[1], 1)} mm: no entra en ninguna posición")
    for label, cov in (("como enchufe (la pieza de esta figura en otras)", m.as_plug),
                       ("como toma (el torso de esta figura con piezas de otras)", m.as_socket)):
        if cov is not None:
            lines.append(f"  {label}:")
            lines += format_coverage(cov, "    ", all_fits)
    return lines
