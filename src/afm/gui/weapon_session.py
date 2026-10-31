"""Sesión de importación de un arma (sin GTK): STL → escala → plano de corte → vista previa → pieza `accesorio` del catálogo.

Es el equivalente, para armas, de lo que `Session` hace con la cabeza de reemplazo: guarda el STL tal cual vino (sin girar), el
factor de escala y los 3 puntos del plano en coordenadas del STL original; cada cambio recalcula la vista previa con la
empuñadura de la línea (la de la mano con agarre del catálogo elegido). El controlador dibuja lo que esta clase devuelve.
"""
from __future__ import annotations

import os

import numpy as np
import trimesh

from .. import weapon as W
from ..catalog import Catalog, CatalogError


class WeaponSession:
    def __init__(self) -> None:
        self.path: str | None = None
        self.raw: trimesh.Trimesh | None = None          # el STL en sus unidades, reparado, sin girar
        self.scale: float = 1.0
        self.spec: W.WeaponSpec | None = None
        self.build: W.WeaponBuild | None = None
        self.error: str = ""                              # por qué no hay vista previa (plano ausente, sección chica…)
        self.catalog: Catalog | None = None
        self.hand = None                                  # pieza `mano` con agarre de la que sale la empuñadura
        self._scaled: trimesh.Trimesh | None = None

    # ------------------------------------------------------------ catálogo y carga
    def set_catalog(self, root: str, hand_ref: str | None = None) -> None:
        """Elige el catálogo de la línea; la empuñadura sale de su mano con agarre (`hand_ref`, o la más reciente)."""
        try:
            cat = Catalog.open(root)
        except (CatalogError, OSError) as exc:
            raise W.WeaponError(f"no se pudo abrir el catálogo «{root}»: {exc}") from exc
        self.hand = W.line_hand(cat, hand_ref)            # WeaponError si no hay ninguna mano con agarre
        self.catalog = cat
        self._rebuild()

    @property
    def loaded(self) -> bool:
        return self.raw is not None

    def load(self, path: str, length_mm: float | None = None, progress=None) -> None:
        """Lee el STL (en sus unidades) y deja la escala en `length_mm` para la dimensión mayor; por defecto 0,40 × la altura de la línea."""
        mesh = W.load_weapon_mesh(path, 1.0, progress)
        self.raw, self.path = mesh, path
        self.spec = W.WeaponSpec(path)
        self._scaled = None
        target = length_mm if length_mm else W.suggest_length(self.catalog.height_mm if self.catalog else 100.0)
        self.set_length(target)

    # ------------------------------------------------------------ escala
    @property
    def scaled(self) -> trimesh.Trimesh:
        if self._scaled is None:
            m = self.raw.copy()
            m.apply_scale(self.scale)
            self._scaled = m
        return self._scaled

    @property
    def length_mm(self) -> float:
        return W.longest_dimension(self.scaled)

    def set_scale(self, factor: float) -> None:
        if factor <= 0:
            raise W.WeaponError("la escala tiene que ser positiva")
        self.scale = float(factor)
        self.spec.scale = self.scale
        self._scaled = None
        self._rebuild()

    def set_length(self, mm: float) -> None:
        self.set_scale(W.scale_for_length(self.raw, mm))

    # ------------------------------------------------------------ plano de corte
    def set_cut(self, points_shown) -> None:
        """3 puntos del plano tomados sobre el arma que se ve (ya escalada): se guardan en coordenadas del STL original."""
        pts = np.asarray(points_shown, float) / self.scale
        self.spec.cut_points = tuple(tuple(map(float, q)) for q in pts)
        self.spec.flip = False
        self._rebuild()

    def clear_cut(self) -> None:
        self.spec.cut_points = ()
        self._rebuild()

    def flip(self) -> None:
        self.spec.flip = not self.spec.flip
        self._rebuild()

    def set_roll(self, degrees: float | None) -> None:
        self.spec.roll = None if degrees is None else float(degrees)
        self._rebuild()

    # ------------------------------------------------------------ resultado
    def _rebuild(self) -> None:
        self.build, self.error = None, ""
        if self.raw is None or self.spec is None:
            return
        if len(self.spec.cut_points) != 3:
            self.error = "elegí el plano de corte (3 puntos sobre el arma)"
            return
        if self.catalog is None or self.hand is None:
            self.error = "elegí el catálogo de la línea (necesita una mano con agarre)"
            return
        try:
            grip, hole = W.line_grip(self.hand.mecanismo["agarre"])
            self.build = W.build_weapon(self.scaled, self.spec, grip, hole)
        except W.WeaponError as exc:
            self.error = str(exc)
        except Exception as exc:                          # noqa: BLE001 — una booleana que falla no tiene que tumbar la ventana
            self.error = f"no se pudo armar el arma: {exc}"

    def plane(self):
        """El plano de corte (marco del arma escalada) o None."""
        if self.spec is None or len(self.spec.cut_points) != 3:
            return None
        try:
            return W.cut_plane(self.spec)
        except W.WeaponError:
            return None

    def result_mesh(self) -> trimesh.Trimesh | None:
        """El arma con la empuñadura nueva, puesta donde estaba el arma original (para dibujarla encima)."""
        if self.build is None:
            return None
        from .. import solids as S

        m = S.to_trimesh(self.build.mesh)
        m.apply_transform(self.build.to_mesh_frame)
        return m

    def summary(self) -> str:
        if self.raw is None:
            return ""
        lines = [f"{os.path.basename(self.path)} · ×{self.scale:.4g} · largo {self.length_mm:.1f} mm"]
        if self.hand is not None:
            lines.append(f"Empuñadura para la mano {self.hand.ref} (Ø{self.hand.mecanismo['agarre']['diametro_taladro_mm']:g} mm, "
                         f"canal {self.hand.mecanismo['agarre']['largo_canal_mm']:g} mm)")
        if self.build is not None:
            i = self.build.info
            lines.append(f"Resultado: {i['dimensiones_mm'][0]:g} × {i['dimensiones_mm'][1]:g} × {i['dimensiones_mm'][2]:g} mm · "
                         f"se conserva el {i['volumen_mantenido_pct']:g} % · sección de corte {i['seccion_corte_mm2']:g} mm² · giro {i['giro_grados']:g}°")
            lines += [f"AVISO: {w}" for w in i["avisos"]]
        elif self.error:
            lines.append(self.error[0].upper() + self.error[1:])
        return "\n".join(lines)

    def save(self, piece_id: str | None = None, new_version: bool = False, descripcion: str = ""):
        """Guarda el arma en el catálogo como pieza `accesorio`. Devuelve (meta, avisos)."""
        if self.build is None:
            raise W.WeaponError(self.error or "todavía no hay arma para guardar")
        return W.add_weapon_to_catalog(self.catalog, self.scaled, self.spec, self.hand.ref, piece_id, new_version, descripcion)
