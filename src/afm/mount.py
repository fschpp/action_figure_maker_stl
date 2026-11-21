"""Monturas: geometría generada que engancha un arma (accesorio) a una parte del cuerpo de la figura (M01–M04 de
`docs/PLAN_enganche_armas_cuerpo.md`).

Este módulo empieza por los **datos**: `MountSpec` (qué arma, dónde, hacia dónde apunta, qué tipo de enganche y con qué juego)
y sus constantes de defecto. M02: escala y pose; M03: secciones del arma; M04: el puente `lazo`. La integración en `pipeline.build` (M05) viene después.

Los defectos de holgura y pared viven acá y **no** en `Params` (plan §6.3): `recipe_key` hashea `Params`, y un campo nuevo
dejaría «vencida» la certificación de cada figura que no usa monturas.

Marcos: `ancla` y `direccion` están en el marco de la pieza huésped (la misma que se ve en `BuildResult.pieces`). `direccion`
es el eje del arma de la empuñadura hacia la punta; el arma, en su marco de accesorio, tiene la empuñadura en +X y la hoja
en x < 0 (ver `weapon.py`).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from . import solids as S

MOUNT_FORMAT = "afm-montura-1"
MOUNT_PLACES = ("pelvis", "torso", "brazo", "pierna", "cabeza")
MOUNT_SIDES = ("izq", "der")                       # izq = +X (lado de trabajo), der = −X (por espejo)
MOUNT_TYPES = ("lazo", "clip", "cuna")
MOUNT_FIELDS = ("arma", "lugar", "lado", "ancla", "direccion", "tipo", "holgura", "pared", "estaciones")

DEFAULT_CLEARANCE = 0.25                           # mm de juego entre la hoja y el hueco del puente
DEFAULT_STATIONS = 1
MAX_CLEARANCE = 2.0                                # por encima ya no retiene nada: se considera un error de tipeo
MAX_STATIONS = 2


class MountError(ValueError):
    """La montura está mal formada o pide algo que no se puede generar."""


def _vec3(value, name: str) -> tuple:
    try:
        v = tuple(float(c) for c in value)
    except (TypeError, ValueError) as exc:
        raise MountError(f"«{name}» tiene que ser una lista de 3 números") from exc
    if len(v) != 3 or not all(math.isfinite(c) for c in v):
        raise MountError(f"«{name}» tiene que ser una lista de 3 números finitos")
    return v


@dataclass(frozen=True)
class MountSpec:
    """Una montura: dónde y cómo se engancha un arma al cuerpo. Inmutable: cambia la receta, no el objeto."""

    arma: str                                      # `id@versión` de un accesorio que figura en `Recipe.accesorios`
    lugar: str                                     # una de MOUNT_PLACES
    ancla: tuple                                   # (x, y, z) sobre la superficie de la pieza huésped
    direccion: tuple                               # eje del arma (empuñadura → punta) en el marco de la pieza
    lado: str = "izq"
    tipo: str = "lazo"
    holgura: float = DEFAULT_CLEARANCE             # mm, juego hoja–hueco
    pared: float | None = None                     # mm; None = `Params.min_wall` (se resuelve con `wall`)
    estaciones: int = DEFAULT_STATIONS             # puentes a lo largo de la hoja

    def __post_init__(self):
        # normaliza a tuplas de floats (también si llegan como listas del JSON) y valida
        object.__setattr__(self, "ancla", _vec3(self.ancla, "ancla"))
        object.__setattr__(self, "direccion", _vec3(self.direccion, "direccion"))
        bad = self.problems()
        if bad:
            raise MountError("; ".join(bad))

    # ------------------------------------------------------------------ validación
    def problems(self) -> list:
        from .catalog import CatalogError, parse_ref

        out = []
        try:
            parse_ref(self.arma)
        except (CatalogError, TypeError, AttributeError) as exc:
            out.append(f"arma «{self.arma}»: {exc}")
        if self.lugar not in MOUNT_PLACES:
            out.append(f"lugar «{self.lugar}» desconocido (hay {', '.join(MOUNT_PLACES)})")
        if self.lado not in MOUNT_SIDES:
            out.append(f"lado «{self.lado}» desconocido (hay {', '.join(MOUNT_SIDES)})")
        if self.tipo not in MOUNT_TYPES:
            out.append(f"tipo «{self.tipo}» desconocido (hay {', '.join(MOUNT_TYPES)})")
        if math.sqrt(sum(c * c for c in self.direccion)) < 1e-9:
            out.append("«direccion» no puede ser el vector nulo")
        if isinstance(self.holgura, bool) or not isinstance(self.holgura, (int, float)) or \
                not (0.0 <= self.holgura <= MAX_CLEARANCE):
            out.append(f"holgura {self.holgura!r}: tiene que ser un número entre 0 y {MAX_CLEARANCE} mm")
        if self.pared is not None and (isinstance(self.pared, bool) or not isinstance(self.pared, (int, float))
                                       or not (self.pared > 0 and math.isfinite(self.pared))):
            out.append(f"pared {self.pared!r}: tiene que ser un número mayor que 0 (o ausente para usar min_wall)")
        if isinstance(self.estaciones, bool) or not isinstance(self.estaciones, int) or \
                not (1 <= self.estaciones <= MAX_STATIONS):
            out.append(f"estaciones {self.estaciones!r}: tiene que ser 1 o {MAX_STATIONS}")
        return out

    # ------------------------------------------------------------------ derivados
    def wall(self, min_wall: float) -> float:
        """Espesor del puente: el pedido o, si no se pidió, la pared mínima de la línea."""
        return float(self.pared) if self.pared is not None else float(min_wall)

    def unit_direction(self) -> tuple:
        n = math.sqrt(sum(c * c for c in self.direccion))
        return tuple(c / n for c in self.direccion)

    # ------------------------------------------------------------------ formato
    def to_dict(self) -> dict:
        """Diccionario JSON. Siempre completo (`pared` nula si usa `min_wall`) para que `recipe_key` sea estable."""
        return {"arma": self.arma, "lugar": self.lugar, "lado": self.lado, "ancla": list(self.ancla),
                "direccion": list(self.direccion), "tipo": self.tipo, "holgura": float(self.holgura),
                "pared": None if self.pared is None else float(self.pared), "estaciones": int(self.estaciones)}

    @classmethod
    def from_dict(cls, d: dict) -> "MountSpec":
        if not isinstance(d, dict):
            raise MountError("una montura tiene que ser un diccionario")
        unknown = sorted(k for k in d if k not in MOUNT_FIELDS)
        if unknown:
            raise MountError(f"campo desconocido en la montura: {', '.join(unknown)} (hay {', '.join(MOUNT_FIELDS)})")
        missing = [k for k in ("arma", "lugar", "ancla", "direccion") if k not in d]
        if missing:
            raise MountError(f"a la montura le falta: {', '.join(missing)}")
        return cls(arma=d["arma"], lugar=d["lugar"], ancla=d["ancla"], direccion=d["direccion"],
                   lado=d.get("lado", "izq"), tipo=d.get("tipo", "lazo"),
                   holgura=d.get("holgura", DEFAULT_CLEARANCE), pared=d.get("pared"),
                   estaciones=d.get("estaciones", DEFAULT_STATIONS))


# ====================================================================== M02: escala y pose de transporte
# Marco del accesorio (ver `weapon.py`): empuñadura en +X, hoja hacia −X, espesor de la hoja a lo largo de Z y ancho a lo largo
# de Y (hoja acostada). La pose de transporte lleva ese marco al de la pieza huésped, con la hoja sobre `direccion`.
def scale_from_real_length(length_units: float, real_mm: float) -> float:
    """Factor (`WeaponSpec.scale`) que deja un arma de `length_units` unidades de STL en `real_mm` milímetros.

    Las mallas del cliente no traen unidades (plan §3): la escala sale de **una medida real** del arma impresa. Para medir el
    largo de una malla ya cargada, `weapon.longest_dimension`; para el atajo por la altura de la figura, `weapon.scale_for_length`.
    """
    for name, v in (("largo en unidades del STL", length_units), ("largo real", real_mm)):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not (v > 0 and math.isfinite(v)):
            raise MountError(f"{name} {v!r}: tiene que ser un número mayor que 0")
    return float(real_mm) / float(length_units)


def construction_spec(spec: MountSpec) -> MountSpec:
    """La montura vista del lado de trabajo (+X). Una montura `der` se construye en +X y se espeja al final (plan §6.1):
    `ancla` y `direccion` de una `der` vienen en el marco real de la pieza (x < 0), así que se espejan en X para construirla.
    Una `izq` se devuelve tal cual."""
    if spec.lado == "izq":
        return spec
    ax, ay, az = spec.ancla
    dx, dy, dz = spec.direccion
    return MountSpec(arma=spec.arma, lugar=spec.lugar, ancla=(-ax, ay, az), direccion=(-dx, dy, dz), lado="izq",
                     tipo=spec.tipo, holgura=spec.holgura, pared=spec.pared, estaciones=spec.estaciones)


def _surface_up(direction: tuple, normal) -> tuple:
    """Normal de referencia (hacia afuera de la superficie) quitada la parte paralela al eje del arma, unitaria."""
    import numpy as np

    d = np.asarray(direction, float)
    n = np.asarray(normal, float)
    n = n - (n @ d) * d
    L = float(np.linalg.norm(n))
    if L < 1e-6:
        raise MountError("la normal de la superficie es paralela al eje del arma: no se puede orientar la hoja")
    return tuple(n / L)


def transport_matrix(spec: MountSpec, station: float = 0.0, normal=None, lift: float = 0.0):
    """4×4 que lleva el marco del accesorio al marco de la pieza huésped (en el lado de construcción: `lado` se resuelve antes
    con `construction_spec`).

    - El eje de la hoja (accesorio −X, de la empuñadura a la punta) queda sobre `spec.direccion`.
    - El punto de la hoja a `station` mm de la empuñadura cae sobre `spec.ancla` (más `lift` mm hacia afuera de la superficie).
    - El espesor de la hoja (accesorio +Z) queda a lo largo de `normal` (la normal saliente de la superficie en el ancla, sin su
      parte paralela al eje): la hoja queda tendida sobre la superficie. Sin `normal`, se usa la dirección saliente del flanco
      (+X en el lado de construcción) y, si el arma apunta casi en esa dirección, +Z.
    """
    import numpy as np

    if spec.lado != "izq":
        raise MountError("`transport_matrix` trabaja en el lado de construcción (+X): pasar la montura por `construction_spec`")
    for name, v in (("station", station), ("lift", lift)):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise MountError(f"{name} {v!r}: tiene que ser un número finito")
    d = np.asarray(spec.unit_direction(), float)
    if normal is None:
        try:
            n = np.asarray(_surface_up(tuple(d), (1.0, 0.0, 0.0)), float)
        except MountError:
            n = np.asarray(_surface_up(tuple(d), (0.0, 0.0, 1.0)), float)
    else:
        n = np.asarray(_surface_up(tuple(d), _vec3(normal, "normal")), float)
    x_acc = -d                                       # empuñadura (+X del accesorio) apunta contra la hoja
    z_acc = n
    y_acc = np.cross(z_acc, x_acc)                   # derecha: X × Y = Z
    m = np.eye(4)
    m[:3, 0], m[:3, 1], m[:3, 2] = x_acc, y_acc, z_acc
    m[:3, 3] = np.asarray(spec.ancla, float) + lift * n - float(station) * d
    return m


def place_accessory(prop, matrix):
    """Coloca el accesorio (`solids.Manifold`, marco del accesorio) en el marco de la pieza con `transport_matrix`."""
    import numpy as np

    return prop.transform(np.asarray(matrix, float)[:3, :].astype(np.float64))


# ====================================================================== M03: secciones de la hoja y de la guarda
# Todo se mide en el marco del accesorio. La «estación» `s` es la distancia (mm) desde el origen del accesorio (cara de la
# guarda que mira a la mano) a lo largo de la hoja: el plano de la sección es x = −s.
SECTION_STEP = 0.25                                # mm entre secciones al recorrer el arma
GUARD_RATIO = 1.3                                  # una sección es «guarda» si es 1,3 × más ancha o más gruesa que la hoja


@dataclass(frozen=True)
class BladeProfile:
    """Lo que el enganche necesita saber del arma: largo, medidas de la hoja y de la guarda."""

    length: float                                  # mm, del origen a la punta
    blade_width: float                             # mm (eje Y del accesorio), mediana del tramo central
    blade_thickness: float                         # mm (eje Z), mediana del tramo central
    has_guard: bool
    guard_end: float                               # estación donde termina la guarda (0 si no hay)
    guard_width: float
    guard_thickness: float


def _to_section_frame():
    """Accesorio (x, y, z) → (y, z, −x): el eje de la hoja queda en +Z y `slice(s)` da la sección en x = −s."""
    import numpy as np

    return np.array([[0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [-1.0, 0.0, 0.0, 0.0]])[:3, :]


def section_at(prop, s: float):
    """Caja (ymin, zmin, ymax, zmax) de la sección del arma en la estación `s`, o `None` si el plano no la corta."""
    cs = prop.transform(_to_section_frame()).slice(float(s))
    if cs.is_empty():
        return None
    x0, y0, x1, y1 = cs.bounds()
    return (float(x0), float(y0), float(x1), float(y1))


def slab_extent(prop, s0: float, s1: float):
    """Caja (ymin, zmin, ymax, zmax) del arma entre las estaciones `s0` y `s1`: la medida conservadora de un puente que cubre ese tramo."""
    if not s1 > s0:
        raise MountError(f"tramo vacío: {s0:g} … {s1:g} mm")
    cube = S.Manifold.cube((s1 - s0, 1e4, 1e4), center=True).translate((-(s0 + s1) / 2, 0.0, 0.0))
    part = S.intersection(prop, cube)
    if part.is_empty():
        raise MountError(f"el arma no pasa por el tramo {s0:g} … {s1:g} mm")
    b = part.bounding_box()
    return (float(b[1]), float(b[2]), float(b[4]), float(b[5]))


def analyze_blade(prop, step: float = SECTION_STEP) -> BladeProfile:
    """Recorre el arma (marco del accesorio, hoja hacia −X) y devuelve largo, sección de la hoja y de la guarda.

    La hoja es la mediana del tramo 30–70 % del largo (no la afecta la punta afinada). La guarda es el tramo que empieza en el
    origen y es más ancho o más grueso que la hoja (`GUARD_RATIO`); si la primera sección ya es como la hoja, no hay guarda.
    La empuñadura (x > 0) no se mide.
    """
    import numpy as np

    b = prop.bounding_box()
    length = -float(b[0])
    if not length > 1.0:
        raise MountError("el arma no tiene hoja: tiene que extenderse hacia −X del origen del accesorio")
    rows = []
    s = 0.05
    while s < length - 0.05:
        box = section_at(prop, s)
        if box is not None:
            rows.append((s,) + box)
        s += step
    if len(rows) < 8:
        raise MountError("el arma es demasiado corta para medirle la sección")
    arr = np.asarray(rows, float)
    w, t = arr[:, 3] - arr[:, 1], arr[:, 4] - arr[:, 2]
    mid = (arr[:, 0] >= 0.3 * length) & (arr[:, 0] <= 0.7 * length)
    if not mid.any():
        mid = np.ones(len(arr), bool)
    bw, bt = float(np.median(w[mid])), float(np.median(t[mid]))
    big = (w > GUARD_RATIO * bw) | (t > GUARD_RATIO * bt)
    guard_end, gw, gt = 0.0, bw, bt
    if big[0]:
        n = int(np.argmin(big)) if not big.all() else len(big)
        lo, hi = float(arr[n - 1, 0]), float(arr[n, 0]) if n < len(arr) else length
        for _ in range(14):                        # el borde real de la guarda cae entre dos secciones: bisección
            mid_s = (lo + hi) / 2
            bx = section_at(prop, mid_s)
            is_big = bx is not None and ((bx[2] - bx[0]) > GUARD_RATIO * bw or (bx[3] - bx[1]) > GUARD_RATIO * bt)
            lo, hi = (mid_s, hi) if is_big else (lo, mid_s)
        guard_end = float(hi)
        gw, gt = float(w[:n].max()), float(t[:n].max())
    return BladeProfile(length=length, blade_width=bw, blade_thickness=bt, has_guard=bool(big[0]),
                        guard_end=guard_end, guard_width=gw, guard_thickness=gt)


def surface_normal(host, point, radius: float = 2.0):
    """Normal saliente de la superficie de `host` (trimesh o Manifold) cerca de `point`: promedio, ponderado por el área, de las
    caras a menos de `radius` mm de la cara más cercana. Solo numpy: sirve también en el navegador."""
    import numpy as np

    tm = host if hasattr(host, "face_normals") else S.to_trimesh(host)
    p = np.asarray(_vec3(point, "point"), float)
    cen = np.asarray(tm.triangles_center, float)
    d = np.linalg.norm(cen - p, axis=1)
    near = d <= d.min() + float(radius)
    w = np.asarray(tm.area_faces, float)[near]
    n = (np.asarray(tm.face_normals, float)[near] * w[:, None]).sum(0)
    L = float(np.linalg.norm(n))
    if L < 1e-9:
        raise MountError("no se pudo calcular la normal de la superficie en el ancla")
    return tuple(n / L)


# ====================================================================== M04: el puente «lazo»
BRIDGE_LENGTH = 3.0                                # mm del puente a lo largo de la hoja
FOOT_DEPTH = 1.0                                   # mm que el pie se hunde en la pieza (la superficie no es plana)
REST_GAP = 0.2                                     # mm entre la guarda y la cara del puente cuando la espada está enfundada
STATION_SPACING = 8.0                              # mm entre centros de dos puentes
TUNNEL_OVERRUN = 0.5                               # mm que el hueco sobresale del puente por cada lado (para cortar limpio)
TIP_MARGIN = 2.0                                   # mm mínimos de hoja que tienen que quedar después del último puente
RETENTION_OVERLAP = 0.8                            # mm que la guarda tiene que superar a la abertura para apoyar en el puente


@dataclass
class Lazo:
    """Puente(s) generado(s) para una montura, en el marco de la pieza (lado de construcción, +X)."""

    matrix: object                                 # 4×4 accesorio → pieza (`transport_matrix`)
    lift: float                                    # mm que la hoja queda separada de la superficie
    stations: tuple                                # centros de los puentes, mm desde el origen del accesorio
    hole_w: float                                  # abertura (ancho × alto), con la holgura incluida
    hole_t: float
    outer: object                                  # sólido de los puentes con su pie (se une a la pieza)
    tunnel: object                                 # hueco (se resta de pieza + puentes)
    weapon: object                                 # el arma ya enfundada, en el marco de la pieza
    info: dict


def build_lazo(spec: MountSpec, prop, min_wall: float, normal=None, station: float | None = None) -> Lazo:
    """Genera el/los puente(s) de un `lazo` para `spec` (M04).

    `prop` es el accesorio en su marco (hoja hacia −X). La hoja queda tendida sobre la superficie, separada `holgura` mm; el hueco
    es la caja de la hoja + `holgura` por cada lado; el techo y las paredes miden `spec.wall(min_wall)`. La guarda apoya sobre la cara
    del puente con `REST_GAP` de juego (`station` por defecto), así la espada «cuelga» enfundada. `normal` es la normal saliente de la
    superficie en el ancla (`surface_normal`); sin ella, ver `transport_matrix`.

    Devuelve sólidos en el marco de la pieza del **lado de construcción**: para una montura `der`, `mirrored_for_side`.
    No toca la pieza: `attach_lazo` la une y la ahueca.
    """
    import numpy as np

    if spec.tipo != "lazo":
        raise MountError(f"`build_lazo` solo genera el tipo «lazo» (la montura pide «{spec.tipo}»)")
    cs = construction_spec(spec)
    wall, c, L = cs.wall(min_wall), float(cs.holgura), BRIDGE_LENGTH
    if not (isinstance(min_wall, (int, float)) and not isinstance(min_wall, bool) and wall > 0):
        raise MountError("la pared del puente tiene que ser mayor que 0")
    prof = analyze_blade(prop)
    s1 = float(station) if station is not None else prof.guard_end + REST_GAP + L / 2
    n = cs.estaciones
    stations = tuple(s1 + i * STATION_SPACING for i in range(n))
    if stations[0] - L / 2 < 0 or stations[-1] + L / 2 > prof.length - TIP_MARGIN:
        raise MountError(f"{n} puente(s) de {L:g} mm no entran en la hoja ({prof.length:.1f} mm; estaciones {stations[0]:.1f}…{stations[-1]:.1f})")
    y0, z0, y1, z1 = slab_extent(prop, stations[0] - L / 2, stations[-1] + L / 2)
    hole_w, hole_t = (y1 - y0) + 2 * c, (z1 - z0) + 2 * c
    yc = (y0 + y1) / 2
    lift = c - z0                                  # la hoja (cara de abajo) queda a `c` mm de la superficie
    z_floor = z0 - c                               # la superficie, en el marco del accesorio
    z_roof = z1 + c + wall

    def box(xa, xb, ya, yb, za, zb):
        return S.Manifold.cube((xb - xa, yb - ya, zb - za)).translate((xa, ya, za))

    outers, tunnels = [], []
    for s in stations:
        xa, xb = -s - L / 2, -s + L / 2
        outers.append(box(xa, xb, yc - hole_w / 2 - wall, yc + hole_w / 2 + wall, z_floor - FOOT_DEPTH, z_roof))
        tunnels.append(box(xa - TUNNEL_OVERRUN, xb + TUNNEL_OVERRUN, yc - hole_w / 2, yc + hole_w / 2, z_floor, z_floor + hole_t))
    m = transport_matrix(cs, station=stations[0], normal=normal, lift=lift)
    to_part = lambda mf: place_accessory(mf, m)    # noqa: E731
    avisos = []
    gw_over = prof.guard_width >= hole_w + RETENTION_OVERLAP
    gt_over = prof.guard_thickness >= hole_t + RETENTION_OVERLAP
    retains = prof.has_guard and (gw_over or gt_over)
    if not prof.has_guard:
        avisos.append("el arma no tiene guarda: nada la frena en el puente y se desliza")
    elif not retains:
        avisos.append(f"la guarda ({prof.guard_width:.1f} × {prof.guard_thickness:.1f} mm) no supera la abertura "
                      f"({hole_w:.1f} × {hole_t:.1f} mm) en {RETENTION_OVERLAP:g} mm: la espada se desliza")
    info = {"estaciones_mm": [round(s, 3) for s in stations], "abertura_mm": [round(hole_w, 3), round(hole_t, 3)],
            "pared_mm": round(wall, 3), "largo_puente_mm": L, "separacion_hoja_mm": round(c, 3),
            "alto_sobre_superficie_mm": round(hole_t + wall, 3), "ancho_total_mm": round(hole_w + 2 * wall, 3),
            "retiene": bool(retains), "avisos": avisos,
            "largo_arma_mm": round(float(prof.length), 3), "guarda_fin_mm": round(float(prof.guard_end), 3)}
    return Lazo(matrix=m, lift=float(lift), stations=stations, hole_w=float(hole_w), hole_t=float(hole_t),
                outer=to_part(S.union(*outers)), tunnel=to_part(S.union(*tunnels)), weapon=to_part(prop), info=info)


def attach_lazo(host, lazo: Lazo):
    """Une los puentes a `host` (`solids.Manifold`, mismo marco) y abre el hueco: el hueco queda exacto aunque el pie penetre la
    superficie. Falla si la montura no toca la pieza (queda flotando) o si parte la pieza en más trozos."""
    before = len(host.decompose())
    out = S.difference(S.union(host, lazo.outer), lazo.tunnel)
    if out.is_empty():
        raise MountError("la montura dejó la pieza vacía")
    if len(out.decompose()) > before:
        raise MountError("el puente no toca la pieza (queda flotando) o la parte en trozos: revisá `ancla`, `direccion` y la normal")
    return out


def mirrored_for_side(lazo: Lazo, spec: MountSpec) -> Lazo:
    """El `Lazo` en el marco real de la pieza: una montura `der` se construyó en +X y se espeja en X (plan §6.1)."""
    if spec.lado == "izq":
        return lazo
    import numpy as np

    flip = np.diag([-1.0, 1.0, 1.0, 1.0])
    return Lazo(matrix=flip @ np.asarray(lazo.matrix, float), lift=lazo.lift, stations=lazo.stations, hole_w=lazo.hole_w,
                hole_t=lazo.hole_t, outer=S.mirror_x(lazo.outer), tunnel=S.mirror_x(lazo.tunnel),
                weapon=S.mirror_x(lazo.weapon), info=lazo.info)


# ====================================================================== M05: integración en `pipeline.build`
@dataclass
class MountBuild:
    """Resultado de una montura ya unida a su pieza huésped (marco real de la pieza)."""

    spec: MountSpec
    host: str                                      # nombre de la pieza en `BuildResult.pieces`
    lazo: Lazo                                     # ya en el marco real (espejado si `lado == der`)
    info: dict


def resolve_host(spec: MountSpec, pieces: dict) -> str:
    """Nombre de la pieza que lleva la montura. `pelvis`: la pelvis si hay cintura, si no el torso; `torso`: el torso."""
    from .waist import PELVIS_NAME

    if spec.lugar == "pelvis":
        name = PELVIS_NAME if PELVIS_NAME in pieces else "torso"
    elif spec.lugar == "torso":
        name = "torso"
    else:
        raise MountError(f"lugar «{spec.lugar}»: por ahora las monturas van en la pelvis o el torso")
    if name not in pieces:
        raise MountError(f"lugar «{spec.lugar}»: la figura no tiene la pieza «{name}»")
    return name


def apply_mounts(pieces: dict, mounts: list, min_wall: float) -> tuple:
    """Suma las monturas a `pieces` (después de la cintura y de todo el tallado; plan §6.2).

    `mounts` es una lista de `(MountSpec, arma)` con el arma (`solids.Manifold`) ya escalada, en su marco de accesorio. Devuelve
    `(pieces_nuevo, {nombre: MountBuild})`; no modifica `pieces`. Cualquier problema levanta `MountError` con el nombre de la montura.
    """
    out = dict(pieces)
    built: dict = {}
    for i, (spec, prop) in enumerate(mounts, start=1):
        name = f"montura_{i}_{spec.lugar}_{spec.lado}"
        try:
            if spec.tipo != "lazo":
                raise MountError(f"tipo «{spec.tipo}»: por ahora solo «lazo»")
            host = resolve_host(spec, out)
            normal = surface_normal(out[host], spec.ancla)
            if spec.lado == "der":                  # la montura se construye en +X: la normal también
                normal = (-normal[0], normal[1], normal[2])
            lazo = mirrored_for_side(build_lazo(spec, prop, min_wall, normal=normal), spec)
            out[host] = attach_lazo(out[host], lazo)
        except MountError as exc:
            raise MountError(f"{name}: {exc}") from exc
        built[name] = MountBuild(spec=spec, host=host, lazo=lazo, info=dict(lazo.info, host=host))
    return out, built


# ====================================================================== M07: verificaciones de la montura (motor puro)
# La orquestación (poses de los miembros, `problems`/`avisos`) vive en `pipeline._validate_mount`; acá solo geometría.
EXTRACT_STEP = 0.5                                 # mm entre posiciones al sacar el arma
EXTRACT_MARGIN = 0.5                               # mm de recorrido de más, después de que la punta libra el puente
CONTACT_TOL_MM3 = 1e-3                             # choque que se ignora (ruido de la booleana)
CLEARANCE_FACTOR = 0.9                             # criterio de holgura de la cintura (`_validate_waist`): gap < 0,9 · clearance es problema
MAX_PRINT_TILT_DEG = 45.0                          # inclinación máxima del eje del hueco respecto de la vertical de impresión (sin voladizo)
GRAVITY_MIN_COS = 0.17                             # cos 80°: la punta tiene que mirar al menos así de «hacia abajo» para que la gravedad apriete la guarda


def handle_direction(lazo: Lazo):
    """Vector unitario (marco de la pieza) que apunta del arma hacia la empuñadura: el sentido en que se saca."""
    import numpy as np

    e = np.asarray(lazo.matrix, float)[:3, 0]
    return e / np.linalg.norm(e)


def extraction_distance(lazo: Lazo, margin: float = EXTRACT_MARGIN) -> float:
    """Recorrido (mm) que hay que tirar del arma, hacia la empuñadura, para que la punta libre el puente más cercano a la guarda."""
    first = min(lazo.stations) - float(lazo.info["largo_puente_mm"]) / 2
    return max(0.0, float(lazo.info["largo_arma_mm"]) - first) + margin


def _boxes_overlap(a, b, tol: float = 1e-6) -> bool:
    return all(a[i] < b[i + 3] + tol and b[i] < a[i + 3] + tol for i in range(3))


def extraction_scan(lazo: Lazo, obstacles: dict, step: float = EXTRACT_STEP) -> dict:
    """Traslada el arma entera a lo largo de su eje, de a `step` mm, hasta librar el puente, y mide en cada paso cuánto se mete en
    cada obstáculo (`{nombre: Manifold}`). Sin `swept_offset`: una traslación en pasos no necesita `scipy` ni `skimage` (plan §6.5).

    Devuelve `{distancia_mm, pasos, direccion, choques: {nombre: {desde_mm, max_mm3}}}`; `desde_mm` es el primer recorrido con choque."""
    import numpy as np

    if not step > 0:
        raise MountError("el paso de extracción tiene que ser mayor que 0")
    dist, e = extraction_distance(lazo), handle_direction(lazo)
    n = int(np.ceil(dist / step - 1e-9))
    boxes = {k: ob.bounding_box() for k, ob in obstacles.items()}
    hits: dict = {}
    for i in range(n + 1):
        d = min(i * step, dist)
        moved = lazo.weapon.translate(tuple(float(x) for x in e * d))
        mb = moved.bounding_box()
        for k, ob in obstacles.items():
            if not _boxes_overlap(mb, boxes[k]):
                continue
            v = float(S.intersection(moved, ob).volume())
            if v > CONTACT_TOL_MM3:
                h = hits.setdefault(k, {"desde_mm": round(d, 3), "max_mm3": 0.0})
                h["max_mm3"] = round(max(h["max_mm3"], v), 4)
    return {"distancia_mm": round(dist, 3), "pasos": n + 1, "direccion": [round(float(x), 4) for x in e], "choques": hits}


def mount_solid(lazo: Lazo):
    """El puente sin el pie hundido en la pieza: lo que realmente sobresale (puente con su hueco abierto)."""
    return S.difference(lazo.outer, lazo.tunnel)


def tilt_from_vertical_deg(direction) -> float:
    """Ángulo (0–90°) entre el eje de la dirección dada y la vertical (+Z): la inclinación del hueco en la pose impresa."""
    import numpy as np

    d = np.asarray(direction, float)
    c = abs(float(d[2])) / float(np.linalg.norm(d))
    return float(np.degrees(np.arccos(min(1.0, c))))


def gravity_seat_cos(lazo: Lazo) -> float:
    """Coseno entre «hacia la punta» y «hacia abajo» (−Z): > 0 si la gravedad aprieta la guarda contra el puente; ≤ 0 si la saca."""
    return float(-(-handle_direction(lazo))[2])


# ====================================================================== M12: informe
def report_by_mount(val: dict) -> dict:
    """`{nombre: {"problemas": [...], "avisos": [...]}}` con lo que `pipeline.validate` dijo de cada montura (los textos llegan
    como «montura_N_…: mensaje»; acá se quita el prefijo). Vacío si la figura no tiene monturas."""
    out = {n: {"problemas": [], "avisos": []} for n in (val.get("montura") or {})}
    for key, field in (("problems", "problemas"), ("avisos", "avisos")):
        for text in val.get(key) or []:
            for n, rec in out.items():
                if isinstance(text, str) and text.startswith(n + ": "):
                    rec[field].append(text[len(n) + 2:])
    return out


def level_of(rec: dict) -> str:
    """«no» si hay problemas, «aviso» si solo hay avisos, «ok» si no hay nada (misma escala que `compat.LEVELS`)."""
    return "no" if rec.get("problemas") else "aviso" if rec.get("avisos") else "ok"
