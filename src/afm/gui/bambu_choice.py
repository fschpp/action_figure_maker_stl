"""Salida para Bambu Studio en la interfaz principal: qué colores van al 3MF / OBJ, con qué color se escribe cada uno y la vista previa.

Es la misma lógica del selector de «Armar figura desde catálogo» (`FigureBuilder.bambu_*`), pero sin catálogo: trabaja sobre el `Colorizer` que
dejó el último «Exportar» (`Session.colorizer`, con las piezas ya horneadas). Es una opción de exportación: no toca el proyecto, la receta ni la
geometría. Sin GTK (los widgets viven en `steps/generar.py` y `window.py`).
"""
from __future__ import annotations

from collections.abc import Callable

from ..bambu import DEFAULT_MAX_COLORS, DEFAULT_MIN_AREA_PCT, default_selection, norm_hex, preview_parts
from ..i18n import tr


class BambuChoice:
    """Estado y decisiones del selector. `colorizer`: función sin argumentos que da el `Colorizer` con piezas (o `None`); `names`: función que da
    `{id de rol: nombre visible}` (puede fallar o dar `{}`: las filas quedan solo con el HEX)."""

    def __init__(self, colorizer: Callable[[], object | None], names: Callable[[], dict] | None = None):
        self._colorizer = colorizer
        self._names = names or (lambda: {})
        self.enabled = False                                   # la casilla: «Exportar también <fig>_bambu.3mf / .obj»
        self.colors = DEFAULT_MAX_COLORS                       # tope de colores (filamentos) en modo automático
        self.min_pct = DEFAULT_MIN_AREA_PCT                    # % mínimo de área: los colores que cubren menos se funden con el más parecido
        self.selection: list[str] | None = None                # HEX de los candidatos que se usan; None = automático (tope y % de arriba)
        self.remap: dict[str, str] = {}                        # {HEX candidato: HEX con que se escribe}
        self._names_cache: tuple = (None, {})                  # (Colorizer, nombres) y (Collected, candidatos): la ventana pide las filas en cada refresco
        self._cand_cache: tuple = (None, [])

    # ------------------------------------------------------------------ estado
    def colorizer(self):
        """El `Colorizer` del último export si ya tiene piezas con color; si no, `None` (no hay nada que elegir)."""
        cz = self._colorizer()
        return cz if cz is not None and getattr(cz, "pieces", None) else None

    def _base(self, cz) -> list:
        """`cz.bambu_candidates()` sin recalcular mientras las piezas sean las mismas."""
        col = cz.bambu_collected()
        if self._cand_cache[0] is not col:
            self._cand_cache = (col, cz.bambu_candidates())
        return self._cand_cache[1]

    def has_colors(self) -> bool:
        cz = self.colorizer()
        return cz is not None and bool(self._base(cz))

    def reset(self) -> None:
        """Vuelve al modo automático y quita los cambios de color de la salida."""
        self.selection = None
        self.remap = {}

    def is_custom(self) -> bool:
        """¿Hay una selección o cambios de color hechos a mano?"""
        return self.selection is not None or bool(self.remap)

    def _need(self):
        cz = self.colorizer()
        if cz is None:
            raise ValueError(tr("bambu_choice.sin_figura"))
        return cz

    # ------------------------------------------------------------------ selector
    def effective_selection(self) -> list[str]:
        """HEX de los colores que van al 3MF ahora: la selección manual (solo los que siguen siendo candidatos) o, si no hay, la automática."""
        cz = self.colorizer()
        if cz is None:
            return []
        cands = self._base(cz)
        if self.selection:
            have = {c["hex"] for c in cands}
            sel = [h for h in self.selection if h in have]
            if sel:
                return sel
        return default_selection(cands, max(1, int(self.colors)), float(self.min_pct))

    def candidates(self) -> list[dict]:
        """Filas del selector: `{hex, area_pct, caras, roles, nombres, enabled, out_hex, cambiado}`, del color más al menos usado."""
        cz = self.colorizer()
        if cz is None:
            return []
        if self._names_cache[0] is not cz:
            try:
                found = dict(self._names() or {})
            except Exception:                                  # noqa: BLE001 — paleta ilegible o sin línea abierta: filas solo con HEX
                found = {}
            self._names_cache = (cz, found)
        names = self._names_cache[1]
        on = set(self.effective_selection())
        out = []
        for c in self._base(cz):
            to = self.remap.get(c["hex"], c["hex"])
            out.append({**c, "nombres": [names.get(r, r) for r in c["roles"]], "enabled": c["hex"] in on, "out_hex": to, "cambiado": to != c["hex"]})
        return out

    def set_enabled(self, hex_color: str, on: bool) -> None:
        """Marca o desmarca un color candidato. Siempre queda al menos uno; un HEX que no es candidato tampoco se acepta (`ValueError`)."""
        self._need()
        hx = norm_hex(hex_color)
        cands = [c["hex"] for c in self.candidates()]
        if hx not in cands:
            raise ValueError(tr("bambu_choice.color_desconocido", hex=hx))
        cur = set(self.effective_selection())
        cur.add(hx) if on else cur.discard(hx)
        if not cur:
            raise ValueError(tr("bambu_choice.al_menos_un_color"))
        self.selection = [h for h in cands if h in cur]

    def set_output_color(self, hex_color: str, out_hex: str) -> None:
        """Cambia con qué color se escribe el candidato `hex_color` en el 3MF / OBJ (no toca la figura ni la paleta). Volver al mismo lo quita."""
        self._need()
        hx, to = norm_hex(hex_color), norm_hex(out_hex)
        if hx not in {c["hex"] for c in self.candidates()}:
            raise ValueError(tr("bambu_choice.color_desconocido", hex=hx))
        if to == hx:
            self.remap.pop(hx, None)
        else:
            self.remap[hx] = to

    # ------------------------------------------------------------------ opciones, vista previa y escritura
    def options(self, force: bool = False) -> dict | None:
        """Opciones para `Colorizer(bambu=)` / `Session.export(bambu=)`, o `None` con la casilla apagada (`force`: las da igual, para la vista previa
        y «Exportar 3MF ahora»). Con piezas ya horneadas lleva `chosen` (lo que el selector muestra marcado) y `remap`."""
        if not self.enabled and not force:
            return None
        opt: dict = {"max_colors": max(1, int(self.colors)), "min_area_pct": float(self.min_pct)}
        cz = self.colorizer()
        if cz is not None and self.has_colors():
            if self.is_custom() or force:
                opt["chosen"] = self.effective_selection()
            have = {c["hex"] for c in self._base(cz)}
            remap = {k: v for k, v in self.remap.items() if k in have}
            if remap:
                opt["remap"] = remap
        return opt

    def preview(self, opt: dict | None = None) -> tuple[list, dict]:
        """Vista previa de lo que saldrá en el 3MF: `([(nombre, malla, rgba)], resumen)`. Las mallas van en el marco del visor principal (sin la
        orientación de impresión). `opt`: opciones ya copiadas (`options(force=True)`), para el hilo de la ventana. Puede tardar en una figura grande."""
        cz = self._need()
        res = cz.bambu_preview(self.options(force=True) if opt is None else opt, oriented=False)
        summary = {"colores": ["#%02X%02X%02X" % tuple(int(x) for x in c) for c in res.colors], "area_pct": list(res.area_pct),
                   "triangulos": int(sum(len(p.F) for p in res.pieces)), "fundidos": int(res.merged)}
        return preview_parts(res), summary

    def export(self, opt: dict | None = None) -> dict:
        """«Exportar 3MF ahora»: reescribe solo `<fig>_bambu.3mf / .obj / .mtl` con los colores de ahora, sin volver a exportar lo demás. `opt`: opciones
        ya copiadas (`options(force=True)`), para el hilo de la ventana."""
        return self._need().write_bambu(self.options(force=True) if opt is None else opt)
