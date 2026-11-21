"""M15: vista previa de una montura (arma y puente en otro color, aviso en rojo si no sale) y mapa de zonas del flanco (plan §7, puntos 3–5).

Lógica pura, sin GTK. Trabaja sobre la **vista conjunta** (`FigureBuilder.preview()`: piezas rígidas en su puerto, sin armar nada), la misma
que se usa para elegir el ancla con el ratón (M14), así que el marco es el de `build`.

* `mount_preview`: arma el puente de la montura sobre la pelvis o el torso de la vista conjunta (`mount.apply_mounts`, el mismo código que
  usa el armado) y devuelve el arma enfundada y el puente como mallas de colores. Si no sale (no toca la pieza, no entra en la hoja…)
  devuelve el motivo y `ok=False`.
* `zone_map`: el mapa de espacio libre del flanco (`mount_map`) contra los miembros **de pie** de la vista conjunta. El armado mide además
  todo el giro de cadera y brazos (`pipeline.mount_flank_map`): este mapa es una guía rápida para elegir, no una validación.

Nada de esto reemplaza a `_validate_mount`, que corre al «Armar y exportar» y deja el estado de cada montura.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .. import mount as MT
from .. import mount_map as MM
from .. import solids as S
from ..i18n import tr
from .mount_pick import MountPickError, host_part_name

WEAPON_COLOR = (0.25, 0.55, 0.95, 1.0)       # el arma enfundada
BRIDGE_COLOR = (1.0, 0.62, 0.15, 1.0)        # el puente de la montura
LEVEL_COLORS = {0: (0.84, 0.27, 0.25, 1.0), 1: (0.95, 0.75, 0.20, 1.0), 2: (0.27, 0.67, 0.38, 1.0)}   # no cabe / justo / cabe
LEVEL_NAMES = {0: "zona_no", 1: "zona_justo", 2: "zona_cabe"}
LIMB_PARTS = ("brazo", "mano", "pierna")      # miembros de la vista conjunta (el otro lado lleva «_der»)
DEFAULT_BLADE_T = 0.8                          # mm: el espesor de hoja que usa el mapa de la línea de comandos si no se conoce el arma


@dataclass
class MountPreview:
    ok: bool
    text: str                                  # qué mostrar: el aviso si no sale, o las notas del puente
    parts: list = field(default_factory=list)  # [(nombre, malla, rgba)] para sumar a la vista conjunta
    avisos: list = field(default_factory=list)


@dataclass
class ZoneMap:
    fm: MM.FlankMap
    overlay: dict                              # {nombre: (puntos Nx3, rgba)} para `render_parts(overlay=…)`
    text: str                                  # resumen y lo que pasa justo en el ancla elegida


def _weapon_mesh(builder, spec):
    try:
        meta, mesh = builder.catalog().load(spec.arma)
    except Exception as exc:                   # noqa: BLE001  (arma borrada del catálogo, archivo ilegible…)
        raise MountPickError(tr("figure_window.pv_sin_arma", arma=spec.arma, exc=exc)) from exc
    return mesh


def _preview_params(builder):
    from ..recipe import recipe_params

    return recipe_params(builder.recipe(), builder.catalog())


def mount_preview(builder, index: int, parts=None) -> MountPreview:
    """Arma y puente de la montura `index` puestos sobre la vista conjunta. `parts` = `builder.preview()[0]` si ya se tiene."""
    spec = builder._mount_at(index)
    parts = parts if parts is not None else builder.preview()[0]
    meshes = {n: m for n, m, *_c in parts}
    try:
        host = host_part_name(spec.lugar, meshes)
        weapon = S.to_manifold(_weapon_mesh(builder, spec))
        min_wall = float(_preview_params(builder).min_wall)
        _pieces, built = MT.apply_mounts({host: S.to_manifold(meshes[host])}, [(spec, weapon)], min_wall)
    except MT.MountError as exc:
        return MountPreview(False, str(exc))
    except MountPickError as exc:
        return MountPreview(False, str(exc))
    except S.MeshNotManifold as exc:
        return MountPreview(False, tr("figure_window.pv_no_cierra", exc=exc))
    mb = next(iter(built.values()))
    avisos = list(mb.info.get("avisos") or [])
    out = [(f"montura_arma_{index}", S.to_trimesh(mb.lazo.weapon), WEAPON_COLOR),
           (f"montura_puente_{index}", S.to_trimesh(MT.mount_solid(mb.lazo)), BRIDGE_COLOR)]
    ab = mb.info.get("abertura_mm") or [0, 0]
    head = tr("figure_window.pv_sale", w=f"{ab[0]:g}", t=f"{ab[1]:g}", alto=f"{mb.info.get('alto_sobre_superficie_mm', 0):g}")
    return MountPreview(True, "\n".join([head] + [f"- {a}" for a in avisos]), out, avisos)


def _need_mm(builder, spec) -> float:
    """Altura que ocupa el puente de esta arma sobre el flanco (`mount_map.needed_height`)."""
    p = _preview_params(builder)
    try:
        t = MT.analyze_blade(S.to_manifold(_weapon_mesh(builder, spec))).blade_thickness
    except (MT.MountError, MountPickError, S.MeshNotManifold):
        t = DEFAULT_BLADE_T
    return MM.needed_height(t, clearance=spec.holgura, wall=spec.wall(p.min_wall), member_clearance=p.clearance)


def free_at(fm: MM.FlankMap, ancla) -> tuple[float, float] | None:
    """(altura libre, distancia en mm) del punto del mapa más cercano a `ancla`; None si el mapa no tiene puntos de ese lado."""
    if len(fm.points) == 0:
        return None
    d = np.linalg.norm(fm.points - np.asarray(ancla, float), axis=1)
    k = int(np.argmin(d))
    return float(fm.free_mm[k]), float(d[k])


def zone_map(builder, index: int, parts=None, n_points: int = 3000) -> ZoneMap:
    """Mapa de zonas del flanco de la pieza que lleva la montura `index`, contra los miembros de pie de la vista conjunta."""
    spec = builder._mount_at(index)
    parts = parts if parts is not None else builder.preview()[0]
    meshes = {n: m for n, m, *_c in parts}
    host = host_part_name(spec.lugar, meshes)
    side = "" if spec.lado == "izq" else "_der"
    obstacles = [meshes[f"{n}{side}"] for n in LIMB_PARTS if f"{n}{side}" in meshes]
    if "cabeza" in meshes:
        obstacles.append(meshes["cabeza"])
    need = _need_mm(builder, spec)
    fm = MM.flank_map(meshes[host], obstacles, need, host_name=host, lado=spec.lado, n_points=n_points)
    lv = fm.levels()
    overlay = {LEVEL_NAMES[k]: (fm.points[lv == k], LEVEL_COLORS[k]) for k in (0, 1, 2) if (lv == k).any()}
    summary = MM.summarize(fm)
    lines = [tr("figure_window.pv_mapa_resumen", need=f"{need:.2f}", pct=f"{summary['cabe_pct']:g}")]
    here = free_at(fm, spec.ancla)
    if here is not None:
        free, dist = here
        if dist > 3.0:
            lines.append(tr("figure_window.pv_ancla_fuera_del_flanco", dist=f"{dist:.1f}"))
        elif free >= need:
            lines.append(tr("figure_window.pv_ancla_cabe", free=f"{free:.1f}", need=f"{need:.2f}"))
        else:
            lines.append(tr("figure_window.pv_ancla_no_cabe", free=f"{free:.1f}", need=f"{need:.2f}"))
    lines.append(tr("figure_window.pv_mapa_nota"))
    return ZoneMap(fm, overlay, "\n".join(lines))
