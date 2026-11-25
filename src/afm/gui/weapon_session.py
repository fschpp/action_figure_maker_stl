"""Sesión de importación de un arma (sin GTK): STL → escala → plano de corte → vista previa → pieza `accesorio` del catálogo.

Es el equivalente, para armas, de lo que `Session` hace con la cabeza de reemplazo: guarda el STL tal cual vino (sin girar), el
factor de escala y los 3 puntos del plano en coordenadas del STL original; cada cambio recalcula la vista previa con la
empuñadura de la línea (la de la mano con agarre del catálogo elegido). El controlador dibuja lo que esta clase devuelve.
"""
from __future__ import annotations
from ..i18n import tr

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
        self.hand_sim_error: str = ""                      # por qué no se pudo simular el arma en la mano
        self._fit_cache: tuple | None = None              # (clave, HandSim | mensaje de error): la simulación en la mano estándar

    # ------------------------------------------------------------ catálogo y carga
    def set_catalog(self, root: str, hand_ref: str | None = None) -> None:
        """Elige el catálogo de la línea; la empuñadura sale de su mano con agarre (`hand_ref`, o la más reciente)."""
        try:
            cat = Catalog.open(root)
        except (CatalogError, OSError) as exc:
            raise W.WeaponError(tr("weapon_session.no_se_pudo_abrir_el_catalogo", root=root, exc=exc)) from exc
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
            raise W.WeaponError(tr("weapon_session.la_escala_tiene_que_ser_positiva"))
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
        self._fit_cache = None
        if self.raw is None or self.spec is None:
            return
        if len(self.spec.cut_points) != 3:
            self.error = tr("weapon_session.elegi_el_plano_de_corte_3")
            return
        if self.catalog is None or self.hand is None:
            self.error = tr("weapon_session.elegi_el_catalogo_de_la_linea")
            return
        try:
            grip, hole = W.line_grip(self.hand.mecanismo["agarre"])
            self.build = W.build_weapon(self.scaled, self.spec, grip, hole)
        except W.WeaponError as exc:
            self.error = str(exc)
        except Exception as exc:                          # noqa: BLE001 — una booleana que falla no tiene que tumbar la ventana
            self.error = tr("weapon_session.no_se_pudo_armar_el_arma", exc=exc)

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

    def hand_sim(self):
        """`HandSim` del arma armada puesta en el puño de la mano estándar de la línea (None si todavía no hay arma o mano).

        Se calcula una vez por armado (se borra en `_rebuild`). Si no se puede simular, el motivo queda en `hand_sim_error`."""
        from .. import solids as S
        from .. import weapon_fit as WF

        self.hand_sim_error = ""
        if self.build is None or self.catalog is None or self.hand is None:
            return None
        if self._fit_cache is None:
            try:
                _meta, hand_mesh, fit = WF.load_hand_with_grip(self.catalog, self.hand.ref)
                sim = WF.simulate_in_hand(S.to_printable_trimesh(self.build.mesh), hand_mesh, fit, self.catalog.height_mm)
                self._fit_cache = (sim, "")
            except (WF.FitError, W.WeaponError) as exc:
                self._fit_cache = (None, str(exc))
            except Exception as exc:                          # noqa: BLE001 — una booleana que falla no tiene que tumbar la ventana
                self._fit_cache = (None, str(exc))
        self.hand_sim_error = self._fit_cache[1]
        return self._fit_cache[0]

    def hand_scene(self):
        """(mano, arma en la mano) en el marco de la mano estándar, para dibujarlas juntas; None si no hay simulación."""
        sim = self.hand_sim()
        if sim is None:
            return None
        from .. import weapon_fit as WF

        _meta, hand_mesh, _fit = WF.load_hand_with_grip(self.catalog, self.hand.ref)
        return hand_mesh, sim.weapon

    def summary(self) -> str:
        if self.raw is None:
            return ""
        lines = [tr("weapon_session.basename_scale_largo_length_mm_mm", basename=os.path.basename(self.path), scale=f"{self.scale:.4g}", length_mm=f"{self.length_mm:.1f}")]
        if self.hand is not None:
            lines.append(tr("weapon_session.empunadura_para_la_mano_ref_diametro", ref=self.hand.ref, diametro_taladro_mm=f"{self.hand.mecanismo['agarre']['diametro_taladro_mm']:g}", largo_canal_mm=f"{self.hand.mecanismo['agarre']['largo_canal_mm']:g}"))
        if self.build is not None:
            i = self.build.info
            lines.append(tr("weapon_session.resultado_v_v2_v3_mm_se", v=f"{i['dimensiones_mm'][0]:g}", v2=f"{i['dimensiones_mm'][1]:g}", v3=f"{i['dimensiones_mm'][2]:g}", volumen_mantenido_pct=f"{i['volumen_mantenido_pct']:g}", seccion_corte_mm2=f"{i['seccion_corte_mm2']:g}", giro_grados=f"{i['giro_grados']:g}"))
            lines += [tr("common.aviso_w", w=w) for w in i["avisos"]]
            sim = self.hand_sim()
            if sim is not None:
                from .weapon_sim import hand_lines

                lines += hand_lines(sim)
        elif self.error:
            lines.append(self.error[0].upper() + self.error[1:])
        return "\n".join(lines)

    def save(self, piece_id: str | None = None, new_version: bool = False, descripcion: str = ""):
        """Guarda el arma en el catálogo como pieza `accesorio`. Devuelve (meta, avisos)."""
        if self.build is None:
            raise W.WeaponError(self.error or tr("weapon_session.todavia_no_hay_arma_para_guardar"))
        return W.add_weapon_to_catalog(self.catalog, self.scaled, self.spec, self.hand.ref, piece_id, new_version, descripcion)
