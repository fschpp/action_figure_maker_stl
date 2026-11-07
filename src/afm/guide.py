"""Guía de cortes: cuánto del catálogo acepta un corte (plan de compatibilidad, `docs/PLAN_compatibilidad_cortes_catalogo.md`, Fases 2 y 3).

Dos cosas, las dos sin construir la figura entera (`build`) y sin GTK:

* **Cobertura** (`coverage_of_port`, `piece_coverage`): para un puerto —el de una pieza ya catalogada o el que saldría de
  un corte— cuenta cuántas piezas del catálogo con el puerto opuesto lo aceptan, según la compatibilidad barata
  (`ports.port_compat`: clase, IoU de las caras, escalón, margen del pasador, altura). Solo mira metadatos.
* **Medición del corte** (`measure_cuts`): la cara real donde cada miembro (y el cuello) se separa del cuerpo, antes de
  catalogar nada: radio equivalente y del círculo inscrito, posición lateral, margen del pasador y la cobertura de esa
  cara contra el catálogo, tanto como enchufe (la pieza que saldría de esta figura) como toma (el torso de esta figura
  recibiendo piezas de otras).

Además de la cara, la cobertura anticipa **el choque brazo–pierna en la pose sentada** (`SeatedClash`): lo que `validate`
reporta como «brazo_izq y pierna_izq se interpenetran» y la comparación de caras no ve. Se calcula con las mallas del
catálogo y las tomas del torso, sin armar la figura: acertó las 24 combinaciones de `docs/CALIBRACION_compat.md`.

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

from . import interface as I
from . import solids as S
from .catalog import Catalog
from .hand import STEP_WARN_MM
from .head import MIN_BASE_IOU
from .ports import COMPLEMENT, PIN_OF_CLASS, Compat, Port, PortFace, PortFrame, coupling_transform, pin_diagnosis, port_compat
from .params import Params

CALIB_FORMAT = "afm-calibracion-1"
CALIB_FILE = "calibracion.json"
LEVEL_RANK = {"ok": 0, "aviso": 1, "no": 2}
# Avisos de `port_compat` que son solo de aspecto (las caras no coinciden: se ve una costura). La calibración (`docs/CALIBRACION_compat.md`)
# mostró que ni el IoU ni el escalón predicen una falla de `validate`: 17 combinaciones con IoU 0,25–0,89 pasaron. El resto de los avisos
# (pared del pasador, alturas…) tocan la función. Si cambian esos textos en `ports.py`, `tests/test_guide.py` lo avisa.
COSMETIC_PREFIXES = ("las caras coinciden solo", "escalón de")


# ---------------------------------------------------------------------------------------------------- choque en pose sentada
SIT_CLASH_TOL = 1e-6        # mm³: el mismo umbral con que `validate` dice «se interpenetran»


@dataclass
class Limb:
    """Un miembro (brazo o pierna) con su malla en el marco de su figura y su puerto de enchufe."""

    mesh: S.Manifold
    port: Port
    kind: str                   # «arm» | «leg»
    figura: str = ""


class SeatedClash:
    """Choque entre el brazo y la pierna de una figura armada en la pose sentada, sin armarla.

    `validate` compara las piezas ya exportadas (la pierna girada `sit_angle` grados alrededor de su pasador) y marca los
    pares que se tocan. Acá se hace lo mismo con las piezas crudas del catálogo: el brazo y la pierna se acoplan a las tomas
    `hombro_izq` y `cadera_izq` del torso anfitrión (`coupling_transform`), la pierna se gira y se mide el volumen común. Lo
    que choca es un miembro prestado contra el OTRO miembro de la figura del torso (el que el torso trae de fábrica)."""

    def __init__(self, cat: Catalog, params: Params | None = None):
        self.cat = cat
        self.p = params or cat.params
        self._limbs: dict = {}

    def limb_of(self, meta) -> Limb | None:
        spec = {"brazo": ("arm", "hombro"), "pierna": ("leg", "cadera")}.get(meta.tipo)
        if spec is None or spec[1] not in meta.puertos:
            return None
        if meta.ref not in self._limbs:
            _meta, mesh = self.cat.load(meta.ref)
            self._limbs[meta.ref] = Limb(S.to_manifold(mesh), meta.puertos[spec[1]], spec[0], meta.origen.get("figura") or "")
        return self._limbs[meta.ref]

    def partner(self, figura: str, kind: str) -> Limb | None:
        """El miembro de la figura `figura` que acompaña a un `kind` (la pierna de un brazo y al revés), o None."""
        if not figura:
            return None
        tipo = "pierna" if kind == "arm" else "brazo"
        cands = [m for m in self.cat.list(tipo=tipo) if (m.origen.get("figura") or "") == figura]
        return self.limb_of(cands[-1]) if cands else None

    def volume(self, arm: Limb, leg: Limb, shoulder: Port, hip: Port) -> float:
        ta = coupling_transform(arm.port.frame, shoulder.frame)
        tl = coupling_transform(leg.port.frame, hip.frame)
        a = arm.mesh.transform(ta[:3, :])
        l = leg.mesh.transform(tl[:3, :])
        axis = -np.asarray(hip.frame.axis, float)
        axis = axis if axis[0] >= 0 else -axis                      # como `pipeline._rotation_axis`: + = hacia adelante
        l = S.rotate_about_axis(l, hip.frame.center, axis, self.p.sit_angle)
        return float(S.intersection(a, l).volume())

    def limb_on_torso(self, limb: Limb, torso, partner: Limb | None = None) -> float | None:
        """`limb` puesto en el torso de catálogo `torso`, contra el otro miembro de la figura de ese torso (`partner`, si no
        se da, el del catálogo). None si el torso no trae las dos tomas en una sola pieza o no hay otro miembro."""
        sh, hip = torso.puertos.get("hombro_izq"), torso.puertos.get("cadera_izq")
        mate = partner or self.partner(torso.origen.get("figura") or "", limb.kind)
        if sh is None or hip is None or mate is None or mate.kind == limb.kind:
            return None
        arm, leg = (limb, mate) if limb.kind == "arm" else (mate, limb)
        return self.volume(arm, leg, sh, hip)

    def torso_with_limb(self, shoulder: Port, hip: Port, figura: str, other, own: dict | None = None) -> float | None:
        """El torso (tomas `shoulder`/`hip`) de la figura `figura` con su otro miembro, recibiendo el miembro de catálogo
        `other`. `own`: `{kind: Limb}` de esa figura cuando todavía no está en el catálogo (una figura recién cortada)."""
        cand = self.limb_of(other)
        if cand is None:
            return None
        mate = (own or {}).get("leg" if cand.kind == "arm" else "arm") or self.partner(figura, cand.kind)
        if mate is None:
            return None
        arm, leg = (cand, mate) if cand.kind == "arm" else (mate, cand)
        return self.volume(arm, leg, shoulder, hip)


def _clash_fit(fit: PieceFit, volume: float | None, sit_angle: float) -> PieceFit:
    """Anota el choque en la pieza y, si hay, la vuelve «no» (con el motivo primero)."""
    fit.choque_mm3 = volume
    if volume is not None and volume > SIT_CLASH_TOL:
        why = f"en la pose sentada ({sit_angle:g}°) choca con el otro miembro de la figura anfitriona ({volume:.1f} mm³)"
        fit.compat = dataclasses.replace(fit.compat, nivel="no", motivos=[why] + list(fit.compat.motivos))
    return fit


# ---------------------------------------------------------------------------------------------------- cobertura
@dataclass
class PieceFit:
    """Cómo se acopla el puerto medido con UNA pieza del catálogo (el peor de sus puertos de esa clase)."""

    ref: str
    tipo: str
    puerto: str                 # nombre del puerto de esa pieza que dio el peor resultado
    compat: Compat
    choque_mm3: float | None = None   # volumen que choca con el otro miembro en la pose sentada (None: no se pudo medir)

    @property
    def nivel(self) -> str:
        return self.compat.nivel

    def motivo(self) -> str:
        c = self.compat
        return (c.motivos or c.avisos or [""])[0]

    @property
    def estetico(self) -> bool:
        """«aviso» solo por el aspecto de la unión (IoU de las caras, escalón): funcionalmente no hay reparos medidos."""
        c = self.compat
        return c.nivel == "aviso" and bool(c.avisos) and all(a.startswith(COSMETIC_PREFIXES) for a in c.avisos)

    @property
    def caras_parecidas(self) -> bool | None:
        """Las caras coinciden (IoU ≥ `MIN_BASE_IOU`, escalón ≤ `STEP_WARN_MM`). None si no se compararon caras."""
        c = self.compat
        return None if c.iou is None else (c.iou >= MIN_BASE_IOU and (c.escalon_mm or 0.0) <= STEP_WARN_MM)


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
    def sin_capacidad(self) -> int:
        """Piezas cuya toma no trae la capacidad del anfitrión medida (catalogadas antes de la Fase 3): esa parte no se juzgó."""
        return sum(1 for f in self.fits if f.compat.sin_capacidad)

    @property
    def aviso_estetico(self) -> int:
        return sum(1 for f in self.fits if f.estetico)

    @property
    def aviso_funcional(self) -> int:
        return self.aviso - self.aviso_estetico

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
        if self.aviso:
            txt += (f" · avisos: {self.aviso_estetico} solo de aspecto" if not self.aviso_funcional else
                    f" · avisos: {self.aviso_estetico} solo de aspecto, {self.aviso_funcional} que tocan la función")
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


def contract_of(cat: Catalog):
    """El contrato de la línea, o None si no hay o `linea.json` trae uno ilegible (la guía sigue sin él; `afm catalogo verificar`
    es quien avisa del contrato roto)."""
    try:
        return cat.contract()
    except (I.ContractError, KeyError, ValueError, TypeError):
        return None


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
                     calib: dict | None = None, port_name: str = "", clash=None) -> Coverage:
    """Cobertura de `port` (de una pieza o de un corte) contra las piezas del catálogo con el puerto opuesto.

    El universo son las últimas versiones de cada id, sin `exclude_ids` ni las piezas cuya figura de origen es
    `exclude_figure` (una figura siempre encaja con sus propias piezas). De cada pieza se toma el peor de sus puertos
    de la misma clase y rol complementario. Mismo criterio de espejo que `Catalog.compatible_with` (así los números
    coinciden en todas partes): si el puerto medido es de una pieza espejable (`mirrorable`) se saltean los `_der` de las
    otras piezas, y si `port_name` es un `_der` se saltean las otras piezas espejables (ya se miden por el lado izquierdo).

    `clash`: función `pieza del catálogo -> mm³ | None` con el choque en la pose sentada (ver `SeatedClash`); si da volumen,
    la pieza pasa a «no». Es de un miembro con un torso (o al revés), así que lo arma quien llama.
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
            if clash is not None:
                worst = _clash_fit(worst, clash(other), params.sit_angle)
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
    sc = SeatedClash(cat)
    mine = sc.limb_of(me)
    out = {}
    for name, port in me.puertos.items():
        if name.endswith("_der") and me.espejable:
            continue
        if mine is not None and port.clase in ("hombro", "cadera"):           # un miembro contra los torsos de otras figuras
            clash = lambda other, mine=mine: sc.limb_on_torso(mine, other) if other.tipo == "torso" else None   # noqa: E731
        elif me.tipo == "torso" and name in ("hombro_izq", "cadera_izq"):     # un torso contra los miembros de otras figuras
            sh, hip = me.puertos.get("hombro_izq"), me.puertos.get("cadera_izq")
            clash = (lambda other, sh=sh, hip=hip: sc.torso_with_limb(sh, hip, fig or "", other)) if sh and hip else None  # noqa: E731
        else:
            clash = None
        cov = coverage_of_port(port, me.altura_figura_mm, cat, cat.params, exclude_ids=(me.id,), exclude_figure=fig,
                               mirrorable=me.espejable, calib=calib, port_name=name, clash=clash)
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
    etiqueta: object = field(default=None, repr=False)   # `interface.PortLabel`: dónde cae frente al contrato de la línea (None sin contrato)
    limb: object = field(default=None, repr=False)   # el miembro cortado (Manifold en el marco de la figura), para el choque sentado
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


def _measure_port(lugar: str, clase: str, port: Port, params: Params) -> CutMeasure:
    """Métricas de la cara de un puerto ya armado: radios, posición lateral, margen del pasador."""
    m = CutMeasure(lugar, clase, port=port)
    pf = port.face
    m.r_eq, m.area_mm2, m.x_mm = pf.radius_eq, pf.area, abs(float(port.frame.center[0]))
    attr = PIN_OF_CLASS.get(clase)
    pin = getattr(params, attr) if attr else None
    g = pin_diagnosis(pf, pin.shaft_d if pin is not None else 0.0)
    m.r_inscrito = g["circulo_d"] / 2
    if pin is not None:
        m.margen_pasador = g["margen"]
        if g["margen"] < 0:
            m.avisos.append(f"el vástago de Ø{pin.shaft_d:g} mm sobresale {-g['margen']:.2f} mm de la cara")
            if g["circulo_d"] < pin.shaft_d:
                m.cara_chica = (g["circulo_d"], pin.shaft_d + 2 * params.min_wall)
        elif g["margen"] < params.min_wall:
            m.avisos.append(f"pared de {g['margen']:.2f} mm alrededor del vástago (mínimo {params.min_wall:g} mm)")
    return m


def _measure(lugar: str, clase: str, face, center, ref_prefer, params: Params) -> CutMeasure:
    """Como `_measure_port`, a partir de la cara de contacto (triángulos) y el centro del puerto."""
    try:
        if face.triangles is None or len(face.triangles) == 0:
            raise ValueError("la cara de corte no trae sus triángulos")
        port = _face_port(face, clase, center, ref_prefer)
    except ValueError as exc:
        m = CutMeasure(lugar, clase)
        m.error = str(exc)
        return m
    return _measure_port(lugar, clase, port, params)


def measure_cuts(project, body: S.Manifold, cat: Catalog | None = None, params: Params | None = None,
                 exclude_figure: str | None = None, calib: dict | None = None) -> list:
    """Mide los cortes de una figura (miembros y cuello) sin generar nada: `[CutMeasure]`.

    `body`: el cuerpo normalizado (`extract.load_body`). Con `cat`, cada corte lleva además su cobertura contra el catálogo
    como enchufe (`as_plug`) y como toma (`as_socket`). Hace lo mismo que `extract.extract_all` hasta la cara de cada corte:
    mismo recorte de la mitad de trabajo, misma rebaba, mismo centro del pasador."""
    from .extract import _clean, _pick_limbs, _port_face, reference_for
    from .head import neck_plane, split_head
    from .limbs import separate_limb
    from .planes import Plane
    from .trims import apply_trims

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
        try:               # los mismos pasos que `extract.extract_all`: corte, recortes, limpieza y cara real del puerto
            limb, face = separate_limb(half, spec, p.burr)
            limb = apply_trims(limb, project.trims, spec.name)
            frame = PortFrame(face.center.copy(), face.outward / np.linalg.norm(face.outward), reference_for(face.outward))
            frame, pface = _port_face(_clean(limb, place, warnings), frame, spec.name, warnings)
        except ValueError as exc:
            m = CutMeasure(place, clase)
            m.error = str(exc)
            out.append(m)
            continue
        m = _measure_port(place, clase, Port(clase, "enchufe", frame, pface), p)
        m.limb = limb
        out.append(m)

    if cat is not None:
        contract = contract_of(cat)
        for m in out:                                           # talle y posición frente al contrato de la línea, si lo hay
            if contract is not None and m.port is not None:
                m.etiqueta = I.label_port(contract, m.port, p.ref_height)
        sc = SeatedClash(cat, cat.params)
        own = {"arm": None, "leg": None}                       # los miembros de ESTA figura, para el choque con los de catálogo
        by_kind = {"brazo": "arm", "pierna": "leg"}
        for m in out:
            if m.port is not None and m.limb is not None and m.lugar in by_kind:
                own[by_kind[m.lugar]] = Limb(m.limb, m.port, by_kind[m.lugar], exclude_figure or "")
        sh = hip = None
        for m in out:
            if m.port is None:
                continue
            socket = m.socket_port()
            if m.lugar == "brazo":
                sh = socket
            elif m.lugar == "pierna":
                hip = socket
        for m in out:
            if m.port is None:
                continue
            kw = dict(cat=cat, params=cat.params, exclude_figure=exclude_figure, calib=calib)
            plug_clash = socket_clash = None
            if m.limb is not None:                              # el miembro de esta figura en torsos de catálogo
                mine = own[by_kind[m.lugar]]
                plug_clash = lambda other, mine=mine: (sc.limb_on_torso(mine, other)         # noqa: E731
                                                       if other.tipo == "torso" else None)
                if sh is not None and hip is not None:          # el torso de esta figura con miembros de catálogo
                    socket_clash = lambda other: sc.torso_with_limb(sh, hip, "", other, own)   # noqa: E731
            m.as_plug = coverage_of_port(m.port, p.ref_height, clash=plug_clash, **kw)
            m.as_socket = coverage_of_port(m.socket_port(), p.ref_height, clash=socket_clash, **kw)
    return out


# ---------------------------------------------------------------------------------------------------- ajuste del corte (Fase 4)
@dataclass
class FitStep:
    """Un desplazamiento probado del plano de corte de un miembro y lo que da contra el catálogo."""

    delta_mm: float
    offset_mm: float
    x_mm: float | None = None
    r_eq: float | None = None
    r_inscrito: float | None = None
    margen_pasador: float | None = None
    compat: int = 0               # piezas del catálogo que no dan «no», sumando la pieza de esta figura (enchufe) y su torso (toma)
    universo: int = 0
    parecidas: int = 0            # de esas, las de caras parecidas (IoU y escalón)
    aviso_funcional: int = 0
    error: str | None = None

    def score(self) -> tuple:
        """Mayor es mejor: sin error, más piezas compatibles, más caras parecidas, menos avisos de función, menos desplazamiento."""
        return (self.error is None, self.compat, self.parecidas, -self.aviso_funcional, -abs(self.delta_mm))

    def quality(self) -> tuple:
        return self.score()[:4]


@dataclass
class FitScan:
    lugar: str                     # «brazo» | «pierna»
    aplicable: bool
    motivo: str = ""               # por qué no se puede ajustar, si no
    base_offset: float = 0.0
    steps: list = field(default_factory=list)

    @property
    def actual(self) -> FitStep | None:
        return next((st for st in self.steps if abs(st.delta_mm) < 1e-9), None)

    @property
    def mejor(self) -> FitStep | None:
        return max(self.steps, key=lambda st: st.score()) if self.steps else None

    @property
    def mejora(self) -> bool:
        """El mejor desplazamiento da algo estrictamente mejor que el corte actual."""
        a, b = self.actual, self.mejor
        return bool(a and b and b.quality() > a.quality())


def _limb_spec(project, lugar: str):
    kind = {"brazo": "arm", "pierna": "leg"}[lugar]
    return next((l for l in project.limbs if l.kind == kind), None)


def scan_cut_offset(project, body: S.Manifold, cat: Catalog, lugar: str, rango: float = 2.0, paso: float = 0.25,
                    exclude_figure: str | None = None, calib: dict | None = None) -> FitScan:
    """Prueba desplazar el plano de corte del miembro `lugar` entre −`rango` y +`rango` mm y mide cada vez la cobertura contra el catálogo.

    Solo cuenta con la extrusión «outward» (el plano de la silueta marca dónde se corta el miembro del torso): con «symmetric»
    la cara de contacto sale del propio STL, ya segmentado, y mover el plano no la cambia (se midió en las 4 figuras reales).
    Cada paso vuelve a medir el corte entero (`measure_cuts`, con choque en pose sentada) y suma el miembro de esta figura contra
    los torsos del catálogo (enchufe) y su torso contra los miembros del catálogo (toma)."""
    from .limbs import _leg_cross_spans_body, effective_cut_offset

    spec = _limb_spec(project, lugar)
    if spec is None:
        return FitScan(lugar, False, f"el proyecto no define el miembro «{lugar}»")
    if spec.extrude != "outward" or _leg_cross_spans_body(spec):
        return FitScan(lugar, False, "el desplazamiento del corte solo cuenta con extrusión «outward»; este miembro usa «symmetric» "
                                     "y su cara de contacto sale del propio STL, así que mover el plano no la cambia")
    try:
        base = effective_cut_offset(spec)
    except ValueError as exc:
        return FitScan(lugar, False, str(exc))
    scan = FitScan(lugar, True, base_offset=base)
    n = int(round(rango / paso))
    for k in range(-n, n + 1):
        delta = k * paso
        trial = dataclasses.replace(project, limbs=[dataclasses.replace(l, cut_offset=base + delta) if l is spec else l
                                                    for l in project.limbs])
        step = FitStep(delta, base + delta)
        try:
            cuts = {m.lugar: m for m in measure_cuts(trial, body, cat, exclude_figure=exclude_figure, calib=calib)}
        except ValueError as exc:
            step.error = str(exc)
            scan.steps.append(step)
            continue
        m = cuts.get(lugar)
        if m is None or m.error or m.port is None:
            step.error = (m.error if m else None) or "no se pudo medir"
        else:
            step.x_mm, step.r_eq, step.r_inscrito, step.margen_pasador = m.x_mm, m.r_eq, m.r_inscrito, m.margen_pasador
            for cov in (m.as_plug, m.as_socket):
                if cov is not None:
                    step.compat += cov.ok + cov.aviso
                    step.universo += cov.universo
                    step.parecidas += sum(1 for f in cov.fits if f.nivel != "no" and f.caras_parecidas)
                    step.aviso_funcional += cov.aviso_funcional
        scan.steps.append(step)
    return scan


def apply_cut_offset(project, scan: FitScan) -> bool:
    """Deja en el proyecto el mejor desplazamiento de `scan` (solo si mejora algo). True si cambió `cut_offset`."""
    if not scan.aplicable or not scan.mejora:
        return False
    spec = _limb_spec(project, scan.lugar)
    spec.cut_offset = float(scan.mejor.offset_mm)
    return True


def format_scan(scan: FitScan) -> list:
    """Texto del barrido: una línea por desplazamiento, el actual y el mejor marcados, y la conclusión."""
    head = f"Ajuste del corte {'del brazo' if scan.lugar == 'brazo' else 'de la pierna'} (desplazamiento del plano, hoy {_c(scan.base_offset)} mm):"
    if not scan.aplicable:
        return [head, f"  no aplica: {scan.motivo}"]
    best, cur = scan.mejor, scan.actual
    lines = [head, "  desplaz.   x      r eq   r insc  pared  compatibles  caras parecidas  avisos de función"]
    for st in scan.steps:
        tag = ("  ← actual" if st is cur else "") + ("  ★ mejor" if st is best and scan.mejora else "")
        if st.error:
            lines.append(f"  {st.delta_mm:+6.2f}   sin cara: {st.error[:70]}{tag}")
            continue
        n = lambda v: "—" if v is None else _c(v)      # noqa: E731
        lines.append(f"  {st.delta_mm:+6.2f}  {n(st.x_mm):>6} {n(st.r_eq):>6} {n(st.r_inscrito):>6} {n(st.margen_pasador):>6}"
                     f"  {st.compat:>3} de {st.universo:<3}  {st.parecidas:>9}  {st.aviso_funcional:>14}{tag}")
    if scan.mejora:
        lines.append(f"  → conviene desplazar {best.delta_mm:+.2f} mm (cut_offset {_c(best.offset_mm)}): compatibles {cur.compat} → {best.compat}, "
                     f"caras parecidas {cur.parecidas} → {best.parecidas}")
    else:
        lines.append("  → el corte actual ya es el mejor dentro del rango probado")
    return lines


# ---------------------------------------------------------------------------------------------------- texto
def _c(x: float, nd: int = 2) -> str:
    return f"{x:.{nd}f}".replace(".", ",")


def format_fit(f: PieceFit) -> str:
    c = f.compat
    det = " (solo aspecto)" if f.estetico else ""
    if c.iou is not None:
        det += f" (IoU {_c(c.iou)}, escalón {_c(c.escalon_mm)} mm)"
    m = c.margenes_mm.get("capacidad_profundidad")
    if m is not None:
        det += f" (profundidad detrás de la cara: {'sobran' if m >= 0 else 'faltan'} {_c(abs(m))} mm)"
    why = f.motivo()
    return f"{f.ref:<28} {f.puerto:<12} {f.nivel}{det}" + (f" — {why}" if f.nivel != "ok" and why else "")


def format_coverage(cov: Coverage, indent: str = "    ", all_fits: bool = False) -> list:
    """Líneas de texto de una cobertura: el resumen y las piezas que no son «ok» (todas con `all_fits`)."""
    lines = [indent + cov.summary()]
    for f in cov.fits:
        if all_fits or f.nivel != "ok":
            lines.append(f"{indent}  {format_fit(f)}")
    if cov.sin_capacidad:
        n = cov.sin_capacidad
        lines.append(f"{indent}  {n} pieza{'s' if n != 1 else ''} sin la capacidad del anfitrión medida: esa parte no se juzgó "
                     "(`afm catalogo capacidad --escribir`)")
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
    if m.etiqueta is not None:
        lab = m.etiqueta
        lines.append(f"  contrato de la línea: {lab.resumen()}" + ("" if lab.en_contrato else "  ← AJUSTAR el corte"))
        lines += [f"    {a}" for a in lab.avisos]
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
