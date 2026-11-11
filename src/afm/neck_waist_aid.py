"""Mira asistida del cuello y de la cintura: lo mismo que `pin_aid` y `cut_aid` hacen para el hombro y la cadera.

Un hombro o una cadera tienen su cara de corte en un plano paralelo al de simetría y el pasador se mueve por TODA la cara (dos
grados de libertad). El cuello y la cintura son distintos en dos cosas, y de ahí sale todo este módulo:

* **El pasador solo se mueve por una línea.** El plano está simetrizado (`head.neck_plane`, `waist.waist_plane`: la normal vive
  en YZ y el origen en x = 0) y el pasador va siempre en x = 0, así que la cara se recorre adelante / atrás sobre una recta. El mapa
  de calor, el imán y el fantasma de la mira trabajan sobre esa recta (`LinePinAid`).
* **El plano se mueve sobre su normal.** No hay una «distancia al plano de simetría» que barrer: el plano se desliza arriba / abajo
  por su normal, con la inclinación que dieron los 3 puntos (`AxisCutAid`; es lo que ya hace `ajuste.fit_waist`, pero contra el
  catálogo en vez de contra una pieza patrón).

Todo se lee con la misma `PinAid.evaluate` (la cobertura contra los metadatos de puertos del catálogo, `guide.coverage_of_port`):

* cuello: la cabeza (enchufe `cuello`) contra los torsos con toma `cuello`, y el torso contra las cabezas del catálogo;
* cintura: la pelvis (enchufe `cintura`) contra los torsos / torsos superiores con toma `cintura`, y al revés.

La cara del cuello sale de `head._face_from_head` (la misma que usa `build_head` y `extract`); la de la cintura, de `waist._face_on`
sobre la mitad +X del torso sin cabeza ni miembros (como `ajuste`), espejada para tener la cara entera que guarda el catálogo.

Centro del puerto: en el cuello es el centro del pasador (`head.pin_center_of`). En la cintura, el del pasador **si se eligió uno**
(`WaistSpec.pin_center`); si no, el centroide de la cara, como siempre (`extract._torso_pieces`), para no mover los puertos de las
piezas ya catalogadas. Es un nivel N0 (informado, no garantizado) y no mira el choque en pose sentada. No importa GTK.
"""
from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass

import numpy as np

from . import solids as S
from .catalog import Catalog
from .cut_aid import CutReading
from .head import HeadSpec, _face_from_head, neck_plane, propose_pin_center
from .limbs import ContactFace, PinPreview, _face_shape
from .params import Params
from .pin_aid import HEAT_MAX_POINTS, PinAid
from .planes import Plane
from .waist import WaistSpec, _face_on, waist_pin_center, waist_plane, waist_port_center

SPAN_MM = 4.0              # la cinta del plano cubre el plano actual ± esto (el cuello y la cintura se mueven más que un corte lateral)
STEP_MM = 0.5              # paso de la cinta
KEY_MM = 0.05              # el caché agrupa los desplazamientos en celdas de este lado
CACHE_MAX = 512
HEAT_LINE_STEP_MM = 0.5    # paso del mapa de calor sobre la recta del pasador
LINE_HALF_MM = 1.0e3       # la recta se corta con la cara: largo de sobra


# ---------------------------------------------------------------------------------------------------- la cara y su recta
@dataclass
class LinePinPreview(PinPreview):
    """`limbs.PinPreview` de una cara cuyo pasador solo se mueve por la recta x = 0 del plano (cuello, cintura)."""

    along: np.ndarray | None = None      # dirección de la recta en el mundo, de costado (x = 0), hacia adelante (+Y)

    def line_dir_uv(self) -> np.ndarray:
        """La dirección de la recta en (u, v) del plano de la cara (unitaria). La recta pasa por el origen: el centro de la cara."""
        d = np.array([float(self.along @ self.plane.u), float(self.along @ self.plane.v)])
        return d / np.linalg.norm(d)

    def snap(self, uv) -> np.ndarray:
        """El punto de la recta más cercano a `uv`."""
        d = self.line_dir_uv()
        return d * float(np.asarray(uv, float) @ d)

    def on_line(self, point_world) -> np.ndarray:
        """Un punto cualquiera del mundo llevado al pasador: sobre el plano de la cara y con x = 0 (ver `head.HeadPinPreview.project`)."""
        uv = self.snap(self.plane.to_uv(np.asarray(point_world, float))[0])
        return self.plane.to_world(uv[None])[0]

    def trace(self) -> list:
        """El tramo de la recta que cae dentro de la cara (2×3, mundo): lo que se ve de canto de costado."""
        span = self.span()
        if span is None:
            return []
        d = self.line_dir_uv()
        return [self.plane.to_world(np.array([d * span[0], d * span[1]]))]

    def profile(self, center, pin) -> np.ndarray:
        """Contorno de costado del pasador (vástago + cabeza, sin chaflán), 8 puntos 3D en x = 0 (como `HeadPinPreview.profile`)."""
        c, t, a = np.asarray(center, float), self.along, np.asarray(self.face.outward, float)
        rs, rh, L, H = pin.shaft_d / 2, pin.head_d / 2, pin.shaft_len, pin.head_h
        pts = [(-rs, 0), (rs, 0), (rs, L), (rh, L), (rh, L + H), (-rh, L + H), (-rh, L), (-rs, L)]
        return np.array([c + x * t + y * a for x, y in pts])

    def span(self) -> tuple | None:
        """`(t0, t1)`: el tramo de la recta que cae dentro de la cara, en mm sobre `along` desde el centro de la cara (el más largo
        si la cara tiene varios trozos); None si la recta no toca la cara."""
        from shapely.geometry import LineString

        d = self.line_dir_uv()
        hit = self.shape.intersection(LineString([d * -LINE_HALF_MM, d * LINE_HALF_MM]))
        parts = [g for g in (list(hit.geoms) if hasattr(hit, "geoms") else [hit]) if g.geom_type == "LineString" and not g.is_empty]
        if not parts:
            return None
        g = max(parts, key=lambda q: q.length)
        t = [float(np.asarray(c) @ d) for c in g.coords]
        return min(t), max(t)


def _along(outward) -> np.ndarray:
    """Hacia adelante (+Y) sobre un plano de normal `outward` en YZ, de costado: como `head.HeadPinPreview.along`."""
    n = np.asarray(outward, float)
    if n[2] < 0:
        n = -n
    t = np.array([0.0, n[2], -n[1]])
    return t / np.linalg.norm(t)


def preview_of_face(face: ContactFace, default_center) -> LinePinPreview:
    """La vista de una cara de cuello / cintura (con sus triángulos) y el centro que se usaría sin elegir ninguno."""
    plane, shape = _face_shape(face)
    return LinePinPreview(face, plane, shape, np.asarray(default_center, float), _along(face.outward))


# ---------------------------------------------------------------------------------------------------- cuello
def neck_face(body: S.Manifold, spec: HeadSpec, shift: float = 0.0) -> ContactFace:
    """Cara de la cabeza del MODELO (la original, no la de reemplazo) sobre el plano del cuello corrido `shift` mm sobre su normal."""
    pl = neck_plane(spec)
    plane = Plane.from_normal(pl.origin + pl.normal * float(shift), pl.normal, spec.name)
    head = S.largest_component(S.intersection(body, S.halfspace(plane)))
    if head.is_empty() or head.volume() < 1.0:
        raise ValueError("el plano del cuello no corta ninguna cabeza (¿está por encima de la figura?)")
    return _face_from_head(head, plane)


def neck_center(face: ContactFace, spec: HeadSpec) -> np.ndarray:
    """Centro del puerto `cuello` sin pasador elegido: el automático (círculo inscrito mayor) o el centroide. Lo mismo que
    `head.pin_center_on` con `pin_center = None`."""
    return propose_pin_center(face) if spec.auto else np.asarray(face.center, float).copy()


def neck_preview(body: S.Manifold, spec: HeadSpec, shift: float = 0.0) -> LinePinPreview:
    face = neck_face(body, spec, shift)
    return preview_of_face(face, neck_center(face, spec))


# ---------------------------------------------------------------------------------------------------- cintura
def waist_torso(body: S.Manifold, project) -> S.Manifold:
    """La mitad +X del cuerpo sin la cabeza ni los miembros: el torso donde se corta la cintura (el de `ajuste.fit_waist`)."""
    from .ajuste import _torso_below_neck

    return _torso_below_neck(body, project)


def waist_face(torso_half: S.Manifold, spec: WaistSpec, shift: float = 0.0) -> ContactFace:
    """Cara de la pelvis sobre el plano de la cintura corrido `shift` mm sobre su normal, ENTERA (la mitad +X y su espejo)."""
    pl = waist_plane(spec)
    n = pl.normal
    plane = Plane.from_normal(pl.origin + n * float(shift), n, spec.name)
    lower = S.intersection(torso_half, S.halfspace(Plane.from_normal(plane.origin, -n)))
    if lower.is_empty() or lower.volume() < 1.0:
        raise ValueError("el plano de la cintura deja el torso sin pelvis (¿está por debajo de la figura?)")
    half = _face_on(lower, plane, n)
    tri = np.asarray(half.triangles, float)
    full = np.concatenate([tri, tri * np.array([-1.0, 1.0, 1.0])])
    return ContactFace(center=half.center, outward=n.copy(), area=2.0 * float(half.area), triangles=full)


def waist_preview(torso_half: S.Manifold, spec: WaistSpec, shift: float = 0.0) -> LinePinPreview:
    """La cara de la cintura y, como centro «por omisión», el del pasador que armaría el generador sin elegir ninguno."""
    face = waist_face(torso_half, spec, shift)
    return preview_of_face(face, waist_pin_center(face, dataclasses.replace(spec, pin_center=None)))


# ---------------------------------------------------------------------------------------------------- mira del pasador (una recta)
class LinePinAid(PinAid):
    """`PinAid` para un pasador que solo recorre la recta x = 0 de la cara (cuello, cintura).

    Lo que cambia es dónde se mira: `evaluate` lleva el punto a la recta, y el imán y el mapa de calor barren solo la recta."""

    def __init__(self, prev: LinePinPreview, lugar: str, cat: Catalog, params: Params | None = None, height_mm: float | None = None,
                 exclude_figure: str | None = None, calib: dict | None = None):
        if lugar not in ("cuello", "cintura"):
            raise ValueError(f"«{lugar}» no es ni el cuello ni la cintura")
        super().__init__(prev, lugar, cat, params, height_mm=height_mm, exclude_figure=exclude_figure, calib=calib)
        self._d = prev.line_dir_uv()

    def evaluate(self, uv):
        return super().evaluate(self.prev.snap(uv))

    def _grid(self, center, radius: float, step: float) -> list:
        c = self.prev.snap(center)
        n = int(math.floor(radius / step + 1e-9))
        return [c + self._d * (k * step) for k in range(-n, n + 1)]

    def heatmap(self, step: float = HEAT_LINE_STEP_MM) -> list:
        """Barre la recta dentro de la cara UNA vez: `[(uv, nivel)]` de los puntos donde cabe el pasador."""
        if self._heat is not None:
            return self._heat
        out = []
        span = self.prev.span()
        if span is not None:
            step = max(float(step), 1e-3)
            n = int((span[1] - span[0]) / step) + 1
            if n > HEAT_MAX_POINTS:
                step = (span[1] - span[0]) / HEAT_MAX_POINTS
            t = span[0] + step / 2
            while t < span[1]:
                q = self._d * t
                if self.prev.fit(q, self.pin.shaft_d)[0]:
                    out.append(((float(q[0]), float(q[1])), self.evaluate(q).nivel))
                t += step
        self._heat = out
        return out


# ---------------------------------------------------------------------------------------------------- mira del plano (se desliza sobre su normal)
@dataclass
class ShiftReading(CutReading):
    """Lo que se lee con el plano corrido `offset_mm` sobre su normal respecto del plano con que se armó la mira (0 = ese plano)."""

    def texto(self) -> str:
        head = f"plano {self.offset_mm:+.2f} mm sobre su normal".replace(".", ",")
        if self.aim is None:
            return head + f" · sin cara de contacto ({self.error})" if self.error else head + " · sin cara de contacto"
        return head + " · " + self.aim.texto().replace(".", ",")


class AxisCutAid:
    """Lectura de la mira para el plano del cuello o de la cintura: cuánto del catálogo acepta el corte según cuánto se deslice.

    `base` es el plano con el que se armó la mira; las posiciones se miden en mm sobre su normal respecto de él, y `center` en
    `strip` / `best` es la del plano actual (0 si no se movió). Se arma al elegir el plano y se descarta si cambia la figura."""

    def __init__(self, lugar: str, make_preview, center_of, cat: Catalog, params: Params | None = None,
                 height_mm: float | None = None, exclude_figure: str | None = None, calib: dict | None = None):
        if lugar not in ("cuello", "cintura"):
            raise ValueError(f"«{lugar}» no es ni el cuello ni la cintura")
        from . import guide as G

        self.lugar = lugar
        self._make = make_preview                    # shift -> LinePinPreview (ValueError si no hay cara)
        self._center_of = center_of                  # LinePinPreview -> centro del puerto (mundo) que se lee
        self.cat = cat
        self.params = params or cat.params
        self.height_mm = height_mm
        self.exclude_figure = exclude_figure or None
        self.calib = calib if calib is not None else G.load_calibration(cat.root)
        self._cache: dict = {}

    base: Plane | None = None        # el plano con que se armó la mira (simetrizado): el 0 de los desplazamientos

    def shift_of(self, origin) -> float:
        """Cuánto está corrido, sobre la normal, un plano de origen `origin` respecto del plano base (0 = el mismo)."""
        if self.base is None:
            return 0.0
        return float((np.asarray(origin, float) - self.base.origin) @ self.base.normal)

    def _pin_aid(self, prev: LinePinPreview) -> LinePinAid:
        return LinePinAid(prev, self.lugar, self.cat, self.params, height_mm=self.height_mm, exclude_figure=self.exclude_figure,
                          calib=self.calib)

    def reading_at(self, shift: float) -> ShiftReading:
        shift = float(shift)
        key = int(round(shift / KEY_MM))
        hit = self._cache.get(key)
        if hit is not None:
            return dataclasses.replace(hit, offset_mm=shift)
        r = self._read(shift)
        if len(self._cache) >= CACHE_MAX:
            self._cache.clear()
        self._cache[key] = r
        return r

    def _read(self, shift: float) -> ShiftReading:
        try:
            prev = self._make(shift)
        except ValueError as exc:
            return ShiftReading(shift, error=str(exc))
        try:
            aid = self._pin_aid(prev)
            return ShiftReading(shift, aid.evaluate(prev.project(self._center_of(prev))), prev)
        except ValueError as exc:             # cara degenerada: se ve la sección pero no se puede leer contra el catálogo
            return ShiftReading(shift, prev=prev, error=str(exc))

    def box_data(self, shift: float) -> dict | None:
        """Lo que dibuja el recuadro (`pin_aid_view.draw_aim_box`) para el plano a `shift` mm: la sección y, encima, las caras del
        catálogo con las que se acoplaría. None si en esa posición no hay cara o no se puede leer contra el catálogo."""
        from .pin_aid_view import aim_box_data

        r = self.reading_at(shift)
        if r.prev is None or r.aim is None:
            return None
        aid = self._pin_aid(r.prev)
        data = aim_box_data(aid, aid.evaluate(r.prev.project(self._center_of(r.prev))))
        data["texto"] = r.texto()
        return data

    def strip(self, center: float = 0.0, span: float = SPAN_MM, step: float = STEP_MM) -> list:
        """La cinta: `[ShiftReading]` de `center − span` a `center + span` cada `step` mm."""
        n = int(round(span / step))
        return [self.reading_at(center + k * step) for k in range(-n, n + 1)]

    def best(self, center: float = 0.0, span: float = SPAN_MM, step: float = STEP_MM) -> tuple:
        """`(mejor, mejora)`: la mejor lectura de la cinta (a igualdad, la más cercana al plano actual) y si es estrictamente mejor
        que la actual. `(None, False)` si ninguna posición tiene cara."""
        cur = self.reading_at(center)
        cands = [r for r in self.strip(center, span, step) if r.aim is not None]
        if not cands:
            return None, False
        best = max(cands, key=lambda r: r.clave() + (-abs(r.offset_mm - center),))
        return best, bool(cur.aim is None or best.clave() > cur.clave())


def neck_cut_aid(body: S.Manifold, spec: HeadSpec, cat: Catalog, params: Params, height_mm: float | None = None,
                 exclude_figure: str | None = None) -> AxisCutAid:
    """La mira del plano del cuello: se mide la cabeza ORIGINAL del modelo (con una de reemplazo, esa se escala a esta base)."""
    spec = dataclasses.replace(spec, replacement_path=None, pin_center=None)
    aid = AxisCutAid("cuello", lambda s: neck_preview(body, spec, s), lambda prev: prev.default_center, cat, params, height_mm,
                     exclude_figure)
    aid.base = neck_plane(spec)
    return aid


def waist_cut_aid(torso_half: S.Manifold, spec: WaistSpec, cat: Catalog, params: Params, height_mm: float | None = None,
                  exclude_figure: str | None = None) -> AxisCutAid:
    """La mira del plano de la cintura. Se lee con el centro del puerto por omisión (el centroide de la cara): al deslizar el plano
    el pasador elegido se descarta (`Session.shift_plane`), igual que el desplazamiento del corte de un miembro lo reinicia."""
    spec = dataclasses.replace(spec, pin_center=None)
    aid = AxisCutAid("cintura", lambda s: waist_preview(torso_half, spec, s),
                     lambda prev: waist_port_center(prev.face, spec), cat, params, height_mm, exclude_figure)
    aid.base = waist_plane(spec)
    return aid
