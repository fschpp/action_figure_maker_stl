"""Contrato de interfaz de la línea (`afm-interfaz-1`): talles de cara y posición lateral por clase de puerto.

Plan `docs/PLAN_compatibilidad_cortes_catalogo.md`, Fase 1 (§4.1). El contrato vive en la clave opcional `interfaz` de
`linea.json`. Sin esa clave todo funciona como antes. Las piezas del catálogo son inmutables (G9): su talle NO se guarda
en ellas, se deriva al vuelo comparando la cara de cada puerto con el contrato.

Qué mide un talle (`metrica` de cada clase): por defecto el radio del círculo de la misma área que la cara
(`PortFace.radius_eq`, «equivalente», como en `docs/MEDIDAS_interfaces.md`); con `metrica: "inscrito"`, el radio del mayor
círculo que entra en la cara, que es el que decide si el pasador entra con pared (plan §1.3, punto 2). Qué métrica usar es una
decisión abierta del plan (G2): el contrato soporta las dos y un contrato sin `metrica` sigue siendo el de siempre. Las caras están a la altura de la figura de origen; el contrato lleva su `altura_mm` y las
medidas de una pieza se reescalan a esa altura (los pasadores son mm absolutos y no escalan).
"""
from __future__ import annotations

import hashlib
import json
import statistics
from dataclasses import dataclass, field

import numpy as np

from .params import Params
from .ports import PIN_OF_CLASS, Port

FORMAT = "afm-interfaz-1"
VERSION = 1

HARD_CLASSES = ("hombro", "cadera", "muneca")      # contrato duro: talles (y posición lateral, salvo la muñeca)
LATERAL_CLASSES = ("hombro", "cadera")              # clases cuya cara tiene una x que fijar; el origen de la mano no es una posición
SOFT_CLASSES = ("cuello", "cintura")                # contrato blando: escala con tope, sin talles
SOFT_SCALE_MAX = 0.10                               # ±10 % de escala (plan G5)

METRICS = ("equivalente", "inscrito")
DEFAULT_METRIC = "equivalente"
DEFAULT_RADIUS_TOL_MM = 0.4                         # tolerancia del radio de un talle (plan §4.1)
DEFAULT_X_TOL_MM = {"hombro": 1.0, "cadera": 0.5, "muneca": 1.0}
DEFAULT_AXIS_TOL_DEG = 1.0
MIN_TOL_MM = 0.05

# eje (de la pieza hacia su vecino, módulo del vector) que el motor asume por clase; la muñeca y el cuello van por el miembro
AXIS_OF_CLASS = {"hombro": (1.0, 0.0, 0.0), "cadera": (1.0, 0.0, 0.0)}


class ContractError(ValueError):
    pass


def _r(x, nd: int = 3) -> float:
    return round(float(x), nd)


# ---------------------------------------------------------------------------------------------------- datos
@dataclass
class Talle:
    id: str
    radio_mm: float
    tol_mm: float = DEFAULT_RADIUS_TOL_MM

    def to_dict(self) -> dict:
        return {"id": self.id, "radio_mm": _r(self.radio_mm), "tol_mm": _r(self.tol_mm)}

    @classmethod
    def from_dict(cls, d: dict) -> "Talle":
        return cls(str(d["id"]), float(d["radio_mm"]), float(d.get("tol_mm", DEFAULT_RADIUS_TOL_MM)))


@dataclass
class ClassContract:
    """Contrato de una clase de puerto. Duro (con talles) o blando (solo escala)."""

    clase: str
    talles: list = field(default_factory=list)       # lista de Talle (vacía en el contrato blando)
    x_mm: float | None = None                         # posición lateral de la cara: |x| del centro del puerto
    x_tol_mm: float = 1.0
    tol_eje_deg: float = DEFAULT_AXIS_TOL_DEG
    pared_min_mm: float | None = None                 # pared mínima alrededor del vástago (por defecto la de la línea)
    escala_max: float | None = None                   # solo contrato blando
    metrica: str = DEFAULT_METRIC                     # qué radio de la cara compara con los talles: «equivalente» o «inscrito»

    @property
    def soft(self) -> bool:
        return self.clase in SOFT_CLASSES

    def to_dict(self) -> dict:
        d: dict = {}
        if self.soft:
            d["modo"] = "escala"
            d["escala_max"] = _r(self.escala_max if self.escala_max is not None else SOFT_SCALE_MAX)
            return d
        if self.x_mm is not None:
            d["x_cara_mm"] = {"valor": _r(self.x_mm), "tol": _r(self.x_tol_mm)}
        d["forma"] = "disco"
        if self.metrica != DEFAULT_METRIC:      # solo si no es la de siempre: la huella de los contratos ya guardados no cambia
            d["metrica"] = self.metrica
        d["tol_eje_deg"] = _r(self.tol_eje_deg)
        d["talles"] = [t.to_dict() for t in sorted(self.talles, key=lambda t: t.radio_mm)]
        if self.pared_min_mm is not None:
            d["pared_min_mm"] = _r(self.pared_min_mm)
        pin = PIN_OF_CLASS.get(self.clase)
        if pin:
            d["pasador"] = pin
        return d

    @classmethod
    def from_dict(cls, clase: str, d: dict) -> "ClassContract":
        if clase in SOFT_CLASSES or d.get("modo") == "escala":
            return cls(clase, escala_max=float(d.get("escala_max", SOFT_SCALE_MAX)))
        x = d.get("x_cara_mm") or {}
        return cls(clase,
                   talles=[Talle.from_dict(t) for t in d.get("talles", [])],
                   x_mm=float(x["valor"]) if "valor" in x else None,
                   x_tol_mm=float(x.get("tol", DEFAULT_X_TOL_MM.get(clase, 1.0))),
                   tol_eje_deg=float(d.get("tol_eje_deg", DEFAULT_AXIS_TOL_DEG)),
                   pared_min_mm=float(d["pared_min_mm"]) if "pared_min_mm" in d else None,
                   metrica=str(d.get("metrica", DEFAULT_METRIC)))

    def problems(self, params: Params | None = None) -> list[str]:
        """Incoherencias del contrato (no mira ninguna pieza)."""
        out = []
        if self.soft:
            if self.escala_max is not None and not (0 < self.escala_max <= 0.5):
                out.append(f"{self.clase}: escala_max {self.escala_max:g} fuera de (0, 0.5]")
            return out
        if self.metrica not in METRICS:
            out.append(f"{self.clase}: métrica «{self.metrica}» desconocida (hay {', '.join(METRICS)})")
        ids = [t.id for t in self.talles]
        if len(set(ids)) != len(ids):
            out.append(f"{self.clase}: talles con id repetido {ids}")
        for t in self.talles:
            if t.radio_mm <= 0 or t.tol_mm <= 0:
                out.append(f"{self.clase}: talle {t.id}: radio y tolerancia tienen que ser mayores que 0")
        ts = sorted(self.talles, key=lambda t: t.radio_mm)
        for a, b in zip(ts, ts[1:]):
            if a.radio_mm + a.tol_mm >= b.radio_mm - b.tol_mm:
                out.append(f"{self.clase}: los talles {a.id} y {b.id} se solapan "
                           f"({a.radio_mm:g} ± {a.tol_mm:g} y {b.radio_mm:g} ± {b.tol_mm:g} mm)")
        if self.x_mm is not None and self.x_tol_mm <= 0:
            out.append(f"{self.clase}: la tolerancia de x tiene que ser mayor que 0")
        floor = floor_radius(self.clase, params, self.pared_min_mm)
        if floor is not None:
            for t in self.talles:
                if t.radio_mm - t.tol_mm < floor - 1e-9:
                    out.append(f"{self.clase}: el talle {t.id} ({t.radio_mm:g} - {t.tol_mm:g} mm) baja del piso físico "
                               f"de {floor:.2f} mm (vástago/2 + pared)")
        return out


@dataclass
class Contract:
    altura_mm: float
    clases: dict = field(default_factory=dict)       # clase -> ClassContract
    version: int = VERSION

    def to_dict(self) -> dict:
        d: dict = {"version": self.version, "altura_mm": _r(self.altura_mm)}
        for clase in sorted(self.clases):
            d[clase] = self.clases[clase].to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Contract":
        if not isinstance(d, dict) or "altura_mm" not in d:
            raise ContractError("el contrato necesita «altura_mm»")
        ver = int(d.get("version", VERSION))
        if ver != VERSION:
            raise ContractError(f"contrato de versión {ver}; este motor entiende la {VERSION}")
        clases = {}
        for k, v in d.items():
            if k in ("version", "altura_mm") or not isinstance(v, dict):
                continue
            clases[k] = ClassContract.from_dict(k, v)
        return cls(float(d["altura_mm"]), clases, ver)

    def hash(self) -> str:
        """Huella corta del contrato (entra en la clave de `compat.json` y en las certificaciones)."""
        blob = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(blob).hexdigest()[:12]

    def problems(self, params: Params | None = None) -> list[str]:
        out = []
        if self.altura_mm <= 0:
            out.append("altura_mm tiene que ser mayor que 0")
        for c in self.clases.values():
            out += c.problems(params)
        return out


def floor_radius(clase: str, params: Params | None, pared_min: float | None = None) -> float | None:
    """Piso físico del radio de la cara: para que el vástago entre con pared, radio ≥ Ø vástago / 2 + pared mínima
    (3,25 mm en el hombro y 3,70 mm en la cadera con los pasadores de la figura de referencia)."""
    attr = PIN_OF_CLASS.get(clase)
    if attr is None or params is None:
        return None
    pin = getattr(params, attr)
    wall = params.min_wall if pared_min is None else pared_min
    return float(pin.shaft_d) / 2 + float(wall)


# ---------------------------------------------------------------------------------------------------- radios de una cara
def inscribed_radius(face) -> float:
    """Radio del mayor círculo que entra en la cara (mm): el que cuenta para que el pasador entre con pared."""
    from shapely.ops import polylabel

    shape = face.shape()
    geoms = list(shape.geoms) if hasattr(shape, "geoms") else [shape]
    big = max(geoms, key=lambda g: g.area)
    pole = polylabel(big, tolerance=0.01)
    return float(big.boundary.distance(pole))


def face_radius(face, metrica: str = DEFAULT_METRIC) -> float:
    """Radio de la cara según la métrica del contrato («equivalente» = el de la misma área; «inscrito»)."""
    if metrica == "inscrito":
        return inscribed_radius(face)
    return float(face.radius_eq)


# ---------------------------------------------------------------------------------------------------- etiquetar un puerto
@dataclass
class PortLabel:
    """Dónde cae la cara de un puerto respecto del contrato de su clase."""

    clase: str
    talle: str | None                   # id del talle, o None si está «fuera de contrato»
    r_eq: float | None                  # radio equivalente de la cara, ya a la altura del contrato (mm)
    x_mm: float | None                  # |x| del centro del puerto, ya a la altura del contrato (mm)
    x_ok: bool | None                   # None si el contrato no fija x
    eje_ok: bool | None                 # None si el contrato no fija el eje de esta clase
    eje_deg: float | None = None
    mas_cercano: str | None = None      # talle más cercano cuando está fuera de contrato
    delta_mm: float | None = None       # distancia (mm) al talle más cercano (con signo: cara − talle)
    avisos: list = field(default_factory=list)
    r_ins: float | None = None          # radio inscrito, a la altura del contrato (mm)
    metrica: str = DEFAULT_METRIC       # con cuál de los dos radios se eligió el talle
    r: float | None = None              # el radio comparado con los talles (según `metrica`)

    @property
    def en_contrato(self) -> bool:
        return self.talle is not None and self.x_ok is not False and self.eje_ok is not False

    def resumen(self) -> str:
        if self.r_eq is None:
            return "sin cara"
        base = f"talle {self.talle}" if self.talle else "fuera de contrato"
        if not self.talle and self.mas_cercano:
            base += f" (más cercano {self.mas_cercano}, {self.delta_mm:+.2f} mm)"
        r = self.r if self.r is not None else self.r_eq
        txt = f"{base}, r {'inscrito ' if self.metrica == 'inscrito' else ''}{r:.2f} mm"
        if self.x_ok is not None and self.x_mm is not None:
            txt += f", x {self.x_mm:.2f} mm" + ("" if self.x_ok else " ✘")
        if self.eje_ok is False:
            txt += f", eje {self.eje_deg:.1f}° ✘"
        return txt


def _axis_tilt_deg(axis, ref_axis) -> float:
    a = np.asarray(axis, float)
    b = np.asarray(ref_axis, float)
    c = abs(float(a @ b)) / (np.linalg.norm(a) * np.linalg.norm(b))      # el eje sale ±X según el lado: no importa el signo
    return float(np.degrees(np.arccos(min(1.0, c))))


def label_port(contract: Contract, port: Port, height_mm: float | None = None) -> PortLabel | None:
    """Etiqueta un puerto con su talle. `height_mm` es la altura de la figura de origen de la pieza; con ella la cara y la
    x se reescalan a la altura del contrato. Devuelve None si el contrato no dice nada de esta clase."""
    cc = contract.clases.get(port.clase)
    if cc is None or cc.soft:
        return None
    if port.face is None:
        return PortLabel(port.clase, None, None, None, None, None, avisos=["el puerto no tiene cara de unión"])
    k = (contract.altura_mm / height_mm) if height_mm and height_mm > 0 else 1.0
    r_eq = float(port.face.radius_eq) * k
    r_ins = inscribed_radius(port.face) * k
    r = r_ins if cc.metrica == "inscrito" else r_eq
    x = abs(float(port.frame.center[0])) * k
    lab = PortLabel(port.clase, None, r_eq, x, None, None, r_ins=r_ins, metrica=cc.metrica, r=r)
    best = None
    for t in cc.talles:
        d = r - t.radio_mm
        if best is None or abs(d) < abs(best[1]):
            best = (t, d)
    if best is not None:
        lab.mas_cercano, lab.delta_mm = best[0].id, best[1]
        if abs(best[1]) <= best[0].tol_mm + 1e-9:
            lab.talle = best[0].id
    if cc.x_mm is not None:
        lab.x_ok = abs(x - cc.x_mm) <= cc.x_tol_mm + 1e-9
        if not lab.x_ok:
            lab.avisos.append(f"x = {x:.2f} mm; el contrato pide {cc.x_mm:.2f} ± {cc.x_tol_mm:.2f} mm")
    ax = AXIS_OF_CLASS.get(port.clase)
    if ax is not None:
        lab.eje_deg = _axis_tilt_deg(port.frame.axis, ax)
        lab.eje_ok = lab.eje_deg <= cc.tol_eje_deg + 1e-9
        if not lab.eje_ok:
            lab.avisos.append(f"el eje está a {lab.eje_deg:.1f}° de ±X (tolerancia {cc.tol_eje_deg:.1f}°)")
    if lab.talle is None and cc.talles:
        lab.avisos.append(f"r {'inscrito ' if cc.metrica == 'inscrito' else ''}= {r:.2f} mm no cae en ningún talle (el más cercano, {lab.mas_cercano}, a {lab.delta_mm:+.2f} mm)")
    return lab


def label_piece(contract: Contract, meta, only_plugs: bool = False) -> dict:
    """Etiqueta cada puerto de una pieza (`parts.PieceMeta`): nombre de puerto -> PortLabel. Los puertos de las clases con
    contrato blando no aparecen. Una pieza espejable ya se mide por el lado izquierdo (el «_der» es su espejo)."""
    out = {}
    for name, p in meta.puertos.items():
        if only_plugs and p.rol != "enchufe":
            continue
        if name.endswith("_der") and meta.espejable:
            continue
        lab = label_port(contract, p, meta.altura_figura_mm)
        if lab is not None:
            out[name] = lab
    return out


# ---------------------------------------------------------------------------------------------------- proponer desde el catálogo
def _talle_ids(n: int) -> list[str]:
    if n == 1:
        return ["M"]
    if n == 2:
        return ["S", "L"]
    if n == 3:
        return ["S", "M", "L"]
    return [f"T{i + 1}" for i in range(n)]


def cluster_radii(values: list[float], gap_mm: float) -> list[list[float]]:
    """Agrupa valores 1-D ordenados: un grupo nuevo cuando la distancia al anterior supera `gap_mm`."""
    vs = sorted(float(v) for v in values)
    groups: list[list[float]] = []
    for v in vs:
        if groups and v - groups[-1][-1] <= gap_mm:
            groups[-1].append(v)
        else:
            groups.append([v])
    return groups


@dataclass
class Proposal:
    contract: Contract
    notas: list = field(default_factory=list)
    muestras: dict = field(default_factory=dict)      # clase -> [(pieza, puerto, r, x)]


def propose(cat, radius_tol_mm: float = DEFAULT_RADIUS_TOL_MM, metrica: str = DEFAULT_METRIC) -> Proposal:
    """Propone el contrato a partir de los enchufes del catálogo (última versión de cada id, lado izquierdo): agrupa los
    radios en talles (grupos a menos de 2 × tolerancia entre vecinos) y toma la mediana de x. Las medidas se llevan a la
    altura de la línea. `metrica`: con qué radio de la cara se agrupa («equivalente» o «inscrito»). No decide nada: el
    resultado hay que confirmarlo (`afm catalogo contrato --calibrar --guardar`). Cada muestra es
    `(pieza, puerto, radio según la métrica, x, radio equivalente, radio inscrito)`."""
    if metrica not in METRICS:
        raise ContractError(f"métrica «{metrica}» desconocida (hay {', '.join(METRICS)})")
    params = cat.params
    h_line = cat.height_mm
    samples: dict[str, list] = {}
    for m in cat.list():
        for name, p in m.puertos.items():
            if p.rol != "enchufe" or p.face is None or p.clase in SOFT_CLASSES:
                continue
            if name.endswith("_der") and m.espejable:
                continue
            k = (h_line / m.altura_figura_mm) if m.altura_figura_mm else 1.0
            r_eq, r_ins = float(p.face.radius_eq) * k, inscribed_radius(p.face) * k
            samples.setdefault(p.clase, []).append((m.ref, name, r_ins if metrica == "inscrito" else r_eq,
                                                    abs(float(p.frame.center[0])) * k, r_eq, r_ins))
    classes = {}
    notas = []
    for clase in HARD_CLASSES:
        rows = samples.get(clase, [])
        if not rows:
            continue
        groups = cluster_radii([r[2] for r in rows], 2 * radius_tol_mm)
        ids = _talle_ids(len(groups))
        talles = []
        for gid, g in zip(ids, groups):
            med = statistics.median(g)
            tol = max(radius_tol_mm, max(abs(v - med) for v in g) + MIN_TOL_MM)
            talles.append(Talle(gid, med, tol))
        x_med = x_tol = None
        if clase in LATERAL_CLASSES:
            xs = [r[3] for r in rows]
            x_med = statistics.median(xs)
            x_tol = max(DEFAULT_X_TOL_MM[clase], max(abs(v - x_med) for v in xs) + MIN_TOL_MM) if len(xs) > 1 else DEFAULT_X_TOL_MM[clase]
        # si los talles se pisan por la tolerancia ampliada, se ajusta cada uno hasta la mitad de la separación
        talles.sort(key=lambda t: t.radio_mm)
        for a, b in zip(talles, talles[1:]):
            room = (b.radio_mm - a.radio_mm) / 2 - 1e-3
            a.tol_mm = min(a.tol_mm, room)
            b.tol_mm = min(b.tol_mm, room)
        cc = ClassContract(clase, talles=talles, metrica=metrica, x_mm=x_med, x_tol_mm=x_tol if x_tol is not None else 1.0, pared_min_mm=params.min_wall if PIN_OF_CLASS.get(clase) else None)
        classes[clase] = cc
        fl = floor_radius(clase, params, cc.pared_min_mm)
        for t in talles:
            if fl is not None and t.radio_mm < fl:
                notas.append(f"{clase}: el talle {t.id} ({t.radio_mm:.2f} mm) está bajo el piso físico de {fl:.2f} mm: "
                             "el vástago no entra con pared en esas piezas")
        if len(rows) < 4:
            notas.append(f"{clase}: solo {len(rows)} muestra(s); los talles son orientativos")
    for clase in SOFT_CLASSES:
        classes[clase] = ClassContract(clase, escala_max=SOFT_SCALE_MAX)
    if not any(c in classes for c in HARD_CLASSES):
        notas.append("el catálogo no tiene enchufes con cara de hombro, cadera ni muñeca: no hay de dónde sacar talles")
    return Proposal(Contract(h_line, classes), notas, samples)


# ---------------------------------------------------------------------------------------------------- texto
def format_contract(contract: Contract) -> list[str]:
    lines = [f"Contrato afm-interfaz-1 v{contract.version} · altura {contract.altura_mm:g} mm · huella {contract.hash()}"]
    for clase in sorted(contract.clases):
        cc = contract.clases[clase]
        if cc.soft:
            lines.append(f"  {clase:<8} blando: escala hasta ±{(cc.escala_max or SOFT_SCALE_MAX) * 100:.0f} %")
            continue
        ts = ", ".join(f"{t.id} r {t.radio_mm:.2f} ± {t.tol_mm:.2f}" for t in sorted(cc.talles, key=lambda t: t.radio_mm)) or "sin talles"
        x = f" · x {cc.x_mm:.2f} ± {cc.x_tol_mm:.2f} mm" if cc.x_mm is not None else ""
        m = f" · radio {cc.metrica}" if cc.metrica != DEFAULT_METRIC else ""
        lines.append(f"  {clase:<8} {ts}{x}{m}")
    return lines
