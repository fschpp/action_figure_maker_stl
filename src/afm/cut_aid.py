"""Mira asistida del corte («cinta de calor»): cuánto del catálogo acepta el corte según DÓNDE se ponga el plano del miembro.

Al armar una figura nueva para agregarla al catálogo, la distancia del corte al plano de simetría («Corte a …» de la GUI,
`LimbSpec.cut_offset`) decide la forma y el tamaño de la cara de contacto del brazo o la pierna, y con eso con cuántas piezas
de otras figuras se acopla. Hasta ahora había que correr la «Guía de cortes» (`guide.scan_cut_offset`) para saberlo.

Es la misma idea que `pin_aid.PinAid`, con una dimensión más cara: el centro del pasador era un punto sobre una cara ya
calculada; acá cada posición del plano cambia la FORMA de la cara. Se mide cada posición sin armar la figura (solo
`limbs.contact_preview`, que corta el miembro de la mitad de trabajo, y la lectura de `PinAid.evaluate` contra los metadatos de
puertos del catálogo), con caché por posición absoluta. Una lectura sobre una figura de ~10 000 triángulos tarda decenas de
milisegundos.

* `CutAid.reading_at(offset)`: una posición (cara, margen del pasador, cobertura de las dos direcciones, nivel).
* `CutAid.strip(center)`: la cinta, `±SPAN_MM` alrededor del corte actual cada `STEP_MM`: lo que pinta la franja verde / amarilla /
  roja de la GUI y lo que permite apuntar sin probar a ciegas.
* `CutAid.best(center)`: el «imán», la mejor posición de la cinta (y si mejora algo respecto de la actual).

Se fija el pasador automático en cada posición (el desplazamiento del corte ya lo reinicia: `Session.set_limb_offset`) y NO se
mira el choque en pose sentada (`guide.SeatedClash`): necesita las mallas y sigue estando en la guía de cortes. Es un nivel
N0 (informado, no garantizado). No importa GTK.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass

import numpy as np

from . import guide as G
from . import limbs as L
from .catalog import Catalog
from .extract import KIND_PORT
from .params import Params
from .pin_aid import LEVEL_COLORS, AimReading, PinAid

SPAN_MM = 3.0              # la cinta cubre el corte actual ± esto
STEP_MM = 0.25             # paso de la cinta
MIN_OFFSET_MM = 0.5        # el mismo mínimo que `Session.set_limb_offset`
KEY_MM = 0.05              # el caché agrupa las posiciones en celdas de este lado
CACHE_MAX = 2048


def unsupported(spec) -> str | None:
    """Por qué el desplazamiento del corte de `spec` no se puede medir / editar, o None si sí. Las mismas condiciones que
    `guide.scan_cut_offset` (solo cuenta con la extrusión «outward») y `Session.set_limb_offset` (plano de simetría)."""
    if spec.kind not in KIND_PORT:
        return f"clase de miembro «{spec.kind}» sin puerto de catálogo"
    if len(spec.polygon) < 3:
        return "dibujá primero la silueta del miembro"
    if spec.extrude != "outward" or L._leg_cross_spans_body(spec):
        return ("el desplazamiento del corte solo cuenta con extrusión «outward»; con «symmetric» la cara de contacto sale del "
                "propio STL y mover el plano no la cambia")
    if not L.plane_on_symmetry(spec.plane, spec.contact_axis):
        return "el plano del miembro no está sobre el plano de simetría: el desplazamiento no aplica"
    return None


@dataclass
class CutReading:
    """Lo que se lee con el plano de corte a `offset_mm` del plano de simetría."""

    offset_mm: float
    aim: AimReading | None = None        # margen del pasador y cobertura (el mismo objeto que lee la mira del pasador)
    prev: object = None                  # `limbs.PinPreview`: la cara que saldría de este corte (para dibujar la sección)
    error: str | None = None             # por qué no hay cara en esta posición

    @property
    def nivel(self) -> str:
        """Como `AimReading.nivel`; sin cara, «fuera»."""
        return "fuera" if self.aim is None else self.aim.nivel

    @property
    def color(self) -> tuple:
        return LEVEL_COLORS[self.nivel]

    def contador(self) -> str:
        return "" if self.aim is None else self.aim.contador()

    def texto(self) -> str:
        """Una línea para la barra de estado: «corte a 12,5 mm · torsos 3/4 · brazos 17/21 · pared 1,4 mm»."""
        head = f"corte a {self.offset_mm:.2f} mm".replace(".", ",")
        if self.aim is None:
            return head + f" · sin cara de contacto ({self.error})" if self.error else head + " · sin cara de contacto"
        return head + " · " + self.aim.texto().replace(".", ",")

    def clave(self) -> tuple:
        """Para elegir la mejor posición: la de `AimReading.clave`; sin cara, peor que cualquiera."""
        return (-2.0, -2.0, -99.0) if self.aim is None else self.aim.clave()

    def rings_world(self) -> list:
        """El contorno de la sección (exterior y agujeros) en el mundo, para dibujarlo; vacío sin cara."""
        return [] if self.prev is None else self.prev.rings_world()


class CutAid:
    """Lectura de la mira para el corte de UN miembro. Se arma al elegir el miembro y se descarta si cambia la figura."""

    def __init__(self, half, spec, cat: Catalog, params: Params | None = None, height_mm: float | None = None,
                 exclude_figure: str | None = None, calib: dict | None = None):
        motivo = unsupported(spec)
        if motivo:
            raise ValueError(motivo)
        self.half = half
        self.spec = spec
        self.cat = cat
        self.params = params or cat.params
        self.height_mm = height_mm
        self.exclude_figure = exclude_figure or None
        self.calib = calib if calib is not None else G.load_calibration(cat.root)     # una sola vez, no en cada lectura
        self._cache: dict = {}
        self._strips: dict = {}

    # ------------------------------------------------------------------ una posición
    def reading_at(self, offset: float) -> CutReading:
        """Lee el plano de corte a `offset` mm del plano de simetría (con caché por celda de `KEY_MM`)."""
        offset = float(offset)
        key = int(round(offset / KEY_MM))
        hit = self._cache.get(key)
        if hit is not None:
            return dataclasses.replace(hit, offset_mm=offset)
        r = self._read(offset)
        if len(self._cache) >= CACHE_MAX:
            self._cache.clear()
        self._cache[key] = r
        return r

    def _read(self, offset: float) -> CutReading:
        if offset < MIN_OFFSET_MM - 1e-9:
            return CutReading(offset, error=f"el corte no puede estar a menos de {MIN_OFFSET_MM:g} mm del plano de simetría")
        spec = dataclasses.replace(self.spec, cut_offset=offset, pin_center=None)
        try:
            prev = L.contact_preview(self.half, spec, self.params.burr)
        except ValueError as exc:
            return CutReading(offset, error=str(exc))
        try:
            aid = self._pin_aid(prev)
            return CutReading(offset, aid.evaluate(prev.project(prev.default_center)), prev)
        except ValueError as exc:             # cara degenerada: se ve la sección pero no se puede leer contra el catálogo
            return CutReading(offset, prev=prev, error=str(exc))

    def _pin_aid(self, prev) -> PinAid:
        return PinAid(prev, self.spec.kind, self.cat, self.params, height_mm=self.height_mm, exclude_figure=self.exclude_figure,
                      calib=self.calib)

    # ------------------------------------------------------------------ recuadro de la sección
    def box_data(self, offset: float) -> dict | None:
        """Lo que dibuja el recuadro (`pin_aid_view.draw_aim_box`) para el corte a `offset`: la sección de ESTE corte y, encima, el contorno
        de la cara de cada pieza del catálogo con la que se acoplaría, coloreado por nivel, para ver el desajuste. El círculo naranja es
        el pasador automático. None si en esa posición no hay cara o no se puede leer contra el catálogo."""
        from .pin_aid_view import aim_box_data

        r = self.reading_at(offset)
        if r.prev is None or r.aim is None:
            return None
        aid = self._pin_aid(r.prev)
        data = aim_box_data(aid, aid.evaluate(r.prev.project(r.prev.default_center)))
        data["texto"] = r.texto()
        return data

    # ------------------------------------------------------------------ cinta e imán
    def strip(self, center: float, span: float = SPAN_MM, step: float = STEP_MM) -> list:
        """La cinta: `[CutReading]` de `center − span` a `center + span` cada `step` mm (sin pasar del mínimo). Las posiciones ya
        leídas salen del caché, así que mover el corte medio paso solo mide los extremos nuevos."""
        n = int(round(span / step))
        return [self.reading_at(center + k * step) for k in range(-n, n + 1) if center + k * step >= MIN_OFFSET_MM - 1e-9]

    def best(self, center: float, span: float = SPAN_MM, step: float = STEP_MM) -> tuple:
        """`(mejor, mejora)`: la mejor lectura de la cinta (a igualdad, la más cercana al corte actual) y si es estrictamente mejor
        que la actual. `(None, False)` si ninguna posición tiene cara."""
        cur = self.reading_at(center)
        cands = [r for r in self.strip(center, span, step) if r.aim is not None]
        if not cands:
            return None, False
        best = max(cands, key=lambda r: r.clave() + (-abs(r.offset_mm - center),))
        return best, bool(cur.aim is None or best.clave() > cur.clave())


# ---------------------------------------------------------------------------------------------------- choque en pose sentada
def seated_summary(measure, offset: float, tol: float | None = None) -> dict:
    """Resume el choque brazo–pierna en la pose sentada de UN corte medido con `guide.measure_cuts` (el miembro de esta figura sobre
    los torsos del catálogo y su torso con los miembros del catálogo). Solo se calcula sobre la posición elegida, no en cada
    movimiento: necesita las mallas de las piezas.

    `{"offset", "choques": [(ref, mm³)], "total", "texto", "choca"}`; `total` = piezas medidas. Sin catálogo o sin cara: `choques` vacío y
    `total` 0 (el texto lo dice)."""
    tol = G.SIT_CLASH_TOL if tol is None else tol
    choques: list = []
    total = 0
    for cov in (getattr(measure, "as_plug", None), getattr(measure, "as_socket", None)):
        if cov is None:
            continue
        for f in cov.fits:
            if f.choque_mm3 is None:
                continue
            total += 1
            if f.choque_mm3 > tol:
                choques.append((f.ref, float(f.choque_mm3)))
    head = f"corte a {offset:.2f} mm".replace(".", ",")
    if not total:
        texto = head + " · pose sentada: no se pudo medir contra ninguna pieza del catálogo"
    elif choques:
        peor = max(v for _r, v in choques)
        texto = head + f" · pose sentada: {len(choques)} de {total} piezas chocan (la peor {peor:.1f} mm³)".replace(".", ",")
    else:
        texto = head + f" · pose sentada: sin choque con las {total} piezas medidas"
    return {"offset": float(offset), "choques": choques, "total": total, "texto": texto, "choca": bool(choques)}
