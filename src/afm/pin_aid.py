"""Mira asistida del pasador («bullseye»): cuánto del catálogo acepta el corte según DÓNDE se ponga el pasador.

Al armar una figura nueva para agregarla al catálogo, el centro del pasador del hombro / la cadera se elige con un clic sobre
la cara de contacto (modo PIN de la GUI). Ese centro es también el centro del puerto que se va a catalogar
(`limbs.separate_limb` corre la cara al `pin_center`), así que la compatibilidad con el resto de las piezas depende de ese clic.
Sin esta mira había que correr la «Guía de cortes» después de cada intento.

Acá se mide, para un punto cualquiera de la cara y SIN armar la figura ni cargar mallas (solo metadatos de puertos, la misma
`guide.coverage_of_port` de la guía de cortes):

* **la pieza → las tomas del catálogo** (`plug`): cuántos torsos de otras figuras aceptarían el brazo / la pierna que saldría de
  este corte;
* **el torso → los enchufes del catálogo** (`socket`): cuántos brazos / piernas de otras figuras aceptaría el torso de esta
  figura en esa misma cara.

Es un nivel N0 (informado, no garantizado), como la guía: el color es «probablemente compatible». No mira el choque en la pose
sentada (`guide.SeatedClash`): necesita las mallas y es caro para recalcularlo en cada movimiento del puntero; sigue estando en la
guía de cortes.

`PinAid.evaluate(uv)` lee un punto (con caché por celda de `CELL_MM`) y `PinAid.best_near(uv)` es el «imán»: la mejor posición
dentro de `NEAR_RADIUS_MM`. No importa GTK.
"""
from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field

import numpy as np

from . import guide as G
from .catalog import Catalog
from .extract import KIND_PORT, complement, reference_for
from .ports import PIN_OF_CLASS, Port, PortFace, PortFrame
from .params import Params

GREEN_MIN = 0.8            # fracción de piezas que lo aceptan (ok o aviso) desde la cual la mira se pinta de verde
CELL_MM = 0.25             # el caché agrupa los puntos en celdas de este lado: moverse menos que esto no recalcula
NEAR_RADIUS_MM = 2.0       # el imán busca hasta esta distancia del pasador (lo que ya hace `guide.scan_cut_offset`)
NEAR_STEP_MM = 0.5         # paso de la grilla fina del imán
NEAR_COARSE_MM = 1.0       # paso de la grilla gruesa con la que el imán elige la zona antes de afinar
CACHE_MAX = 4096

LEVELS = ("ok", "aviso", "no", "fuera", "sin_catalogo")
LEVEL_COLORS = {            # RGBA del fantasma en el visor y del recuadro
    "ok": (0.20, 0.80, 0.30, 1.0),
    "aviso": (0.98, 0.80, 0.15, 1.0),
    "no": (0.90, 0.20, 0.20, 1.0),
    "fuera": (0.90, 0.20, 0.20, 1.0),
    "sin_catalogo": (0.65, 0.65, 0.70, 1.0),
}
PARTNERS = {"hombro": ("torsos", "brazos"), "cadera": ("torsos", "piernas")}   # (quién recibe la pieza, quién recibiría el torso)


@dataclass
class AimReading:
    """Lo que se lee en un punto de la cara: margen del pasador y cobertura de las dos direcciones."""

    uv: tuple                       # (u, v) sobre el plano de la cara (el de `PinPreview.plane`)
    clase: str                      # «hombro» | «cadera»
    dentro: bool                    # el centro cae dentro de la cara
    margen_mm: float                # entre el vástago y el borde de la cara (negativo: sobresale)
    shaft_d: float
    min_wall: float
    plug: G.Coverage | None = None
    socket: G.Coverage | None = None
    nombres: tuple = ("torsos", "miembros")

    def lados(self) -> list:
        """Las coberturas que tienen universo (una dirección sin piezas con el puerto opuesto no opina)."""
        return [c for c in (self.plug, self.socket) if c is not None and c.universo]

    @property
    def cobertura(self) -> float | None:
        """La peor de las dos direcciones (ok o aviso sobre el total). None si no hay catálogo contra el cual medir."""
        ls = self.lados()
        return min(c.cobertura for c in ls) if ls else None

    @property
    def cobertura_plena(self) -> float | None:
        ls = self.lados()
        return min(c.cobertura_plena for c in ls) if ls else None

    @property
    def nivel(self) -> str:
        """«fuera» (el centro no cae en la cara), «sin_catalogo» (nada que comparar), «no» (ninguna pieza lo acepta),
        «ok» (≥ `GREEN_MIN` lo aceptan) o «aviso» (una parte)."""
        if not self.dentro:
            return "fuera"
        cob = self.cobertura
        if cob is None:
            return "sin_catalogo"
        if cob <= 0.0:
            return "no"
        return "ok" if cob >= GREEN_MIN else "aviso"

    @property
    def color(self) -> tuple:
        return LEVEL_COLORS[self.nivel]

    def contador(self) -> str:
        """«torsos 3/4 · brazos 17/21»: cuántas piezas lo aceptan sobre el total, en cada dirección."""
        out = []
        for name, cov in zip(self.nombres, (self.plug, self.socket)):
            if cov is not None and cov.universo:
                out.append(f"{name} {cov.ok + cov.aviso}/{cov.universo}")
        return " · ".join(out)

    def pared(self) -> str:
        if not self.dentro:
            return "el centro está fuera de la cara"
        if self.margen_mm < 0:
            return f"el vástago sobresale {-self.margen_mm:.1f} mm"
        txt = f"pared {self.margen_mm:.1f} mm"
        return txt + (f" (< {self.min_wall:g})" if self.margen_mm < self.min_wall else "")

    def texto(self) -> str:
        """Una línea para la barra de estado: contador y pared."""
        c = self.contador()
        return (c + " · " if c else "sin catálogo para comparar · ") + self.pared()

    def peor_motivo(self) -> str | None:
        """El motivo más repetido entre las piezas que dan «no» o «aviso» (para decir QUÉ falla, no solo cuántas)."""
        counts: dict = {}
        for cov in self.lados():
            for f in cov.fits:
                if f.nivel != "ok" and f.motivo():
                    counts[f.motivo()] = counts.get(f.motivo(), 0) + 1
        return max(counts, key=counts.get) if counts else None

    def clave(self) -> tuple:
        """Para elegir la mejor posición: cobertura de la peor dirección, luego cuántas dan «ok» pleno y luego la pared."""
        cob, plena = self.cobertura, self.cobertura_plena
        return (-1.0 if cob is None else cob, -1.0 if plena is None else plena, min(self.margen_mm, self.min_wall))


class PinAid:
    """Lectura de la mira para el pasador de UN miembro. Se arma al entrar al modo PIN y se descarta al salir."""

    def __init__(self, prev, kind: str, cat: Catalog, params: Params | None = None, height_mm: float | None = None,
                 exclude_figure: str | None = None, calib: dict | None = None):
        if kind not in KIND_PORT:
            raise ValueError(f"clase de miembro «{kind}» sin puerto de catálogo")
        self.prev = prev
        self.cat = cat
        self.params = params or cat.params
        self.clase = KIND_PORT[kind]
        self.pin = getattr(self.params, PIN_OF_CLASS[self.clase])
        self.height_mm = height_mm
        self.exclude_figure = exclude_figure or None
        self.calib = calib if calib is not None else G.load_calibration(cat.root)
        axis = np.asarray(prev.face.outward, float)
        self.axis = axis / np.linalg.norm(axis)
        self.ref = reference_for(self.axis)
        self.nombres = PARTNERS[self.clase]
        # La cara, una sola vez, en (u, v) del puerto con origen en el centro de la cara; cada punto la corre.
        self._origin = np.asarray(prev.plane.origin, float)
        self._shape0 = self._shape_in_port(self._origin)
        self._cache: dict = {}

    # ------------------------------------------------------------------ puerto en un punto
    def _frame(self, center) -> PortFrame:
        return PortFrame(np.asarray(center, float).copy(), self.axis.copy(), self.ref.copy())

    def _shape_in_port(self, center):
        """La huella de la cara en (u, v) del puerto centrado en `center` (el mismo cálculo que `guide._face_port`)."""
        from shapely.geometry import Polygon
        from shapely.ops import unary_union

        frame, plane = self._frame(center), self.prev.plane
        geoms = list(self.prev.shape.geoms) if hasattr(self.prev.shape, "geoms") else [self.prev.shape]
        polys = []
        for g in geoms:
            if g.is_empty or g.geom_type != "Polygon":
                continue
            ext = frame.to_uv(plane.to_world(np.asarray(g.exterior.coords)))
            holes = [frame.to_uv(plane.to_world(np.asarray(h.coords))) for h in g.interiors]
            polys.append(Polygon(ext, holes).buffer(0))
        return unary_union(polys)

    def center_world(self, uv) -> np.ndarray:
        return self.prev.plane.to_world(np.asarray(uv, float)[None])[0]

    def port_at(self, uv) -> Port:
        """El puerto de enchufe que saldría del corte con el pasador en `uv` (cara corrida al nuevo centro)."""
        from shapely import affinity

        center = self.center_world(uv)
        off = self._frame(self._origin).to_uv(center)[0]
        shape = affinity.translate(self._shape0, -float(off[0]), -float(off[1]))
        return Port(self.clase, "enchufe", self._frame(center), PortFace.from_shape(shape))

    # ------------------------------------------------------------------ lectura
    def _cover(self, port: Port) -> G.Coverage:
        return G.coverage_of_port(port, self.height_mm, self.cat, self.params, exclude_figure=self.exclude_figure,
                                  calib=self.calib)

    def evaluate(self, uv) -> AimReading:
        """Lee el punto `uv`: margen del pasador y cobertura (con caché por celda de `CELL_MM`)."""
        uv = np.asarray(uv, float)
        key = (int(round(uv[0] / CELL_MM)), int(round(uv[1] / CELL_MM)))
        hit = self._cache.get(key)
        if hit is not None:
            return dataclasses.replace(hit, uv=(float(uv[0]), float(uv[1])))
        inside, margin = self.prev.fit(uv, self.pin.shaft_d)
        r = AimReading((float(uv[0]), float(uv[1])), self.clase, inside, float(margin), self.pin.shaft_d,
                       self.params.min_wall, nombres=self.nombres)
        if inside:
            try:
                port = self.port_at(uv)
                r.plug = self._cover(port)
                r.socket = self._cover(complement(port))
            except ValueError:               # una cara degenerada en ese punto: se lee solo el margen
                r.plug = r.socket = None
        if len(self._cache) >= CACHE_MAX:
            self._cache.clear()
        self._cache[key] = r
        return r

    # ------------------------------------------------------------------ imán
    def _grid(self, center, radius: float, step: float) -> list:
        c = np.asarray(center, float)
        n = int(math.floor(radius / step + 1e-9))
        pts = []
        for i in range(-n, n + 1):
            for j in range(-n, n + 1):
                d = np.array([i * step, j * step])
                if np.hypot(*d) <= radius + 1e-9:
                    pts.append(c + d)
        return pts

    def _best(self, pts: list, around) -> AimReading | None:
        best, best_key = None, None
        for q in pts:
            if not self.prev.fit(q, self.pin.shaft_d)[0]:
                continue
            r = self.evaluate(q)
            k = r.clave() + (-float(np.linalg.norm(q - around)),)       # a igualdad, el más cercano al pasador actual
            if best_key is None or k > best_key:
                best, best_key = r, k
        return best

    def best_near(self, uv, radius: float = NEAR_RADIUS_MM) -> AimReading | None:
        """La mejor posición dentro de `radius` mm de `uv` (el «imán»): primero una grilla gruesa para elegir la zona y después
        una fina alrededor. None si ningún punto del disco cae dentro de la cara."""
        c = np.asarray(uv, float)
        coarse = self._best(self._grid(c, radius, NEAR_COARSE_MM), c)
        if coarse is None:
            return None
        fine = self._best(self._grid(np.asarray(coarse.uv), NEAR_COARSE_MM, NEAR_STEP_MM), c)
        return fine if fine is not None and fine.clave() >= coarse.clave() else coarse

    # ------------------------------------------------------------------ dibujo
    def reference_faces(self, reading: AimReading) -> list:
        """Las caras de las piezas del catálogo con las que el punto se acopla (las tomas), vistas desde el puerto del
        enchufe y centradas en el pasador: `[(ref, nivel, shapely)]` en (u, v) del puerto. Sirve al recuadro para ver el desajuste.

        La cara de la toma se ve espejada en v desde el enchufe (`ports.face_iou`)."""
        from shapely import affinity

        out = []
        if reading.plug is None:
            return out
        for f in reading.plug.fits:
            meta = self.cat.meta(f.ref)
            q = meta.puertos.get(f.puerto)
            if q is None or q.face is None:
                continue
            out.append((f.ref, f.nivel, affinity.scale(q.face.shape(), 1.0, -1.0, origin=(0, 0))))
        return out

    def own_face(self, reading: AimReading):
        """La cara de ESTE corte en (u, v) del puerto, centrada en el pasador."""
        return self.port_at(reading.uv).face.shape()
