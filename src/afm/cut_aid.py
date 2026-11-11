"""Cinta de calor del corte del miembro: cuánto del catálogo acepta el corte según A QUÉ DISTANCIA del plano de simetría se haga.

Hermana de `pin_aid` (la mira del pasador). Allá el punto a elegir era el centro del pasador sobre la cara; acá es el desplazamiento
del plano de corte (`LimbSpec.cut_offset`, el campo «Corte a … mm»). Para cada desplazamiento se vuelve a cortar SOLO el miembro
activo (mismos pasos que `guide.measure_cuts`: corte, recortes, limpieza y cara real del puerto) y se lee la cobertura contra el
catálogo en los dos sentidos (la pieza → las tomas y el torso → los enchufes), con la misma `guide.coverage_of_port`.

No mira el choque en la pose sentada (necesita el miembro opuesto y es caro): sigue estando en la guía de cortes
(`guide.measure_cuts` / `guide.scan_cut_offset`). Es un nivel N0 (informado, no garantizado).

Solo aplica con extrusión «outward» (con «symmetric» la cara sale del propio STL y mover el
plano no la cambia; ver `guide.scan_cut_offset`). No importa GTK.
"""
from __future__ import annotations

import dataclasses

import numpy as np

from . import guide as G
from . import solids as S
from .catalog import Catalog
from .limbs import _leg_cross_spans_body, effective_cut_offset, plane_on_symmetry
from .pin_aid import PARTNERS, AimReading
from .ports import PIN_OF_CLASS, Port

CELL_MM = 0.1              # el caché agrupa los desplazamientos en celdas de este paso
RANGE_MM = 3.0             # la cinta cubre ±RANGE_MM del desplazamiento actual
STEP_MM = 0.5              # paso de la cinta
MIN_OFFSET_MM = 0.5        # el mismo mínimo que `Session.set_limb_offset`
MAX_OFFSET_MM = 200.0
CACHE_MAX = 256
LUGAR_OF_KIND = {"arm": "brazo", "leg": "pierna"}


@dataclasses.dataclass
class CutReading(AimReading):
    """`AimReading` de un corte: `uv` = (desplazamiento, 0). Si el vástago sobresale de la cara el corte no sirve («fuera», rojo),
    aunque la forma de la cara coincida con alguna pieza."""

    sin_cara: bool = False           # el corte no deja cara (no se pudo cortar)

    @property
    def nivel(self) -> str:
        return "fuera" if self.sin_cara or self.margen_mm < 0 else super().nivel

    def pared(self) -> str:
        return "el corte no deja cara" if self.sin_cara else super().pared()


class CutAid:
    """Lectura del corte de UN miembro («brazo» o «pierna») según el desplazamiento del plano. Se arma al entrar a editar el corte."""

    def __init__(self, project, body: S.Manifold, lugar: str, cat: Catalog, params=None, exclude_figure: str | None = None,
                 calib: dict | None = None):
        self.project, self.body, self.lugar, self.cat = project, body, lugar, cat
        self.params = params or project.params
        self.exclude_figure = exclude_figure or None
        self.calib = calib if calib is not None else G.load_calibration(cat.root)
        self.spec = G._limb_spec(project, lugar)
        self.motivo: str | None = None
        self.base = 0.0
        if self.spec is None:
            self.motivo = f"el proyecto no define el miembro «{lugar}»"
        elif self.spec.extrude != "outward" or _leg_cross_spans_body(self.spec):
            self.motivo = ("el desplazamiento del corte solo cuenta con extrusión «outward»: con «symmetric» la cara sale del "
                           "propio STL y mover el plano no la cambia")
        else:
            try:
                self.base = effective_cut_offset(self.spec)
            except ValueError as exc:
                self.motivo = str(exc)
        self.on_symmetry = self.spec is not None and plane_on_symmetry(self.spec.plane, self.spec.contact_axis)
        self.clase = "hombro" if self.spec is None or self.spec.kind == "arm" else "cadera"
        self.nombres = PARTNERS[self.clase]
        self.pin = getattr(self.params, PIN_OF_CLASS[self.clase])
        self._half = None
        self._cache: dict = {}
        self._ribbon: list | None = None

    @property
    def aplicable(self) -> bool:
        return self.motivo is None

    # ------------------------------------------------------------------ el corte de un miembro
    def _half_body(self):
        """La mitad de trabajo del cuerpo (recortada por el cuello si hay cabeza), una sola vez."""
        if self._half is None:
            from .head import neck_plane
            from .planes import Plane

            half = self.body.trim_by_plane((1.0, 0.0, 0.0), 0.0)
            if self.project.head is not None:
                neck = neck_plane(self.project.head)
                half = S.intersection(half, S.halfspace(Plane.from_normal(neck.origin, -neck.normal)))
            self._half = half
        return self._half

    def port_at(self, offset: float) -> Port:
        """El enchufe de la pieza que saldría con el plano de corte a `offset` mm. ValueError si ese corte no se puede hacer."""
        from .extract import _clean, _port_face, reference_for
        from .limbs import separate_limb
        from .trims import apply_trims

        spec = dataclasses.replace(self.spec, cut_offset=float(offset), pin_center=None)
        warnings: list = []
        limb, face = separate_limb(self._half_body(), spec, self.params.burr)
        limb = apply_trims(limb, self.project.trims, spec.name)
        from .ports import PortFrame

        frame = PortFrame(face.center.copy(), face.outward / np.linalg.norm(face.outward), reference_for(face.outward))
        frame, pface = _port_face(_clean(limb, self.lugar, warnings), frame, spec.name, warnings)
        return Port(self.clase, "enchufe", frame, pface)

    def _valid(self, offset: float) -> bool:
        """Con la silueta sobre el plano de simetría el campo «Corte a» admite 0,5…200 mm (`Session.set_limb_offset`); en otro
        plano el desplazamiento es relativo a él y puede ser negativo."""
        return (MIN_OFFSET_MM <= offset <= MAX_OFFSET_MM) if self.on_symmetry else abs(offset) <= MAX_OFFSET_MM

    # ------------------------------------------------------------------ lectura
    def evaluate(self, offset: float) -> CutReading:
        """Lee el corte a `offset` mm (caché por celda de `CELL_MM`). `uv` de la lectura = (offset, 0)."""
        offset = float(offset)
        key = int(round(offset / CELL_MM))
        hit = self._cache.get(key)
        if hit is not None:
            return dataclasses.replace(hit, uv=(offset, 0.0))
        r = CutReading((offset, 0.0), self.clase, True, 0.0, self.pin.shaft_d, self.params.min_wall, nombres=self.nombres,
                       sin_cara=True)
        if not self.aplicable or not self._valid(offset):
            pass
        else:
            try:
                port = self.port_at(offset)
            except ValueError:                           # un corte sin cara: queda «fuera»
                port = None
            if port is not None:
                m = G._measure_port(self.lugar, self.clase, port, self.params)
                r.sin_cara = False
                r.margen_mm = float(m.margen_pasador if m.margen_pasador is not None else 0.0)
                kw = dict(cat=self.cat, params=self.params, exclude_figure=self.exclude_figure, calib=self.calib)
                r.plug = G.coverage_of_port(port, self.params.ref_height, **kw)
                r.socket = G.coverage_of_port(m.socket_port(), self.params.ref_height, **kw)
        if len(self._cache) >= CACHE_MAX:
            self._cache.clear()
        self._cache[key] = r
        return r

    def ribbon(self, rango: float = RANGE_MM, paso: float = STEP_MM) -> list:
        """La cinta: `[(offset, lectura)]` de `base − rango` a `base + rango` cada `paso` mm (solo desplazamientos válidos).
        Se calcula UNA vez y queda guardada; vacía si el corte no aplica."""
        if self._ribbon is not None:
            return self._ribbon
        out = []
        if self.aplicable:
            n = int(round(rango / paso))
            for k in range(-n, n + 1):
                off = self.base + k * paso
                if self._valid(off):
                    out.append((off, self.evaluate(off)))
        self._ribbon = out
        return out

    def best(self) -> CutReading | None:
        """El mejor desplazamiento de la cinta (como el «imán»: cobertura, «ok» pleno, pared y, a igualdad, el que menos mueve)."""
        cands = [(r.clave() + (-abs(off - self.base),), r) for off, r in self.ribbon() if not r.sin_cara and r.margen_mm >= 0]
        return max(cands, key=lambda t: t[0])[1] if cands else None
