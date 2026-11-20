"""T46 · Lógica de la ventana «Paleta» de la línea (roles de color y sus colores). Sin GTK ni OpenGL: se prueba sin ventana.

`PaletteEditor` trabaja sobre un **borrador** de la paleta del catálogo abierto: cambiar un color, renombrar, agregar o quitar un rol solo
toca el borrador; `paleta.json` se escribe únicamente en `apply()` («Aplicar»). La vista previa de una pieza con roles se repinta con la
paleta del borrador (`lut[etiquetas]` sobre el atlas del paquete, sin hornear nada) y sin guardar.

Quitar un rol que usa alguna pieza se bloquea con la lista de piezas (como `Catalog.delete_role`); importar un JSON inválido, o uno al que
le falta un rol en uso, no cambia nada. `PIL`/`trimesh` no se importan al cargar el módulo (R3).
"""
from __future__ import annotations

import json
import os

import numpy as np

from ..i18n import tr
from ..palette import Palette, PaletteError, Role, normalize_hex

PREVIEW_CACHE = 6                      # paquetes de roles que se recuerdan (el atlas se repinta con la LUT, el paquete no se relee)


class PaletteEditError(ValueError):
    """Lo pedido no se puede hacer con la paleta (rol en uso, color inválido, JSON malo…). El mensaje está listo para mostrarse."""


def _copy(p: Palette | None) -> Palette:
    return Palette([]) if p is None else Palette.from_dict(p.to_dict())


class PaletteEditor:
    def __init__(self, ctrl):
        self.ctrl = ctrl
        self.saved: Palette | None = None            # lo que hay en `paleta.json` (None = la línea no tiene paleta)
        self.draft: Palette = Palette([])            # lo que se está editando
        self._pkgs: dict = {}                        # ref -> (sha256, paquete restringido a roles usados, similitud 4×4)
        self.reload()

    # ------------------------------------------------------------------ catálogo
    def _cat(self):
        cat = self.ctrl.catalog_handle()
        if cat is None:
            raise PaletteEditError(tr("common.no_hay_un_catalogo_abierto"))
        return cat

    def reload(self) -> None:
        """Vuelve a leer `paleta.json` y descarta el borrador."""
        self._pkgs.clear()
        cat = self.ctrl.catalog_handle()
        if cat is None:
            self.saved, self.draft = None, Palette([])
            return
        try:
            self.saved = cat.palette()
        except ValueError as exc:                    # CatalogError: paleta.json ilegible
            self.saved, self.draft = None, Palette([])
            raise PaletteEditError(str(exc)) from exc
        self.draft = _copy(self.saved)

    @property
    def has_catalog(self) -> bool:
        return self.ctrl.catalog_handle() is not None

    @property
    def exists(self) -> bool:
        """¿La línea ya tiene `paleta.json`?"""
        return self.saved is not None

    @property
    def dirty(self) -> bool:
        """¿Hay cambios sin aplicar?"""
        return self.draft.to_dict() != _copy(self.saved).to_dict()

    # ------------------------------------------------------------------ roles
    def usage_map(self) -> dict[str, list[str]]:
        """`{rol: [refs de las piezas (todas las versiones) que lo usan]}`, de las fichas del catálogo (`color.roles`)."""
        out: dict[str, list[str]] = {}
        for m in self.ctrl.catalog_pieces():
            for rid in ((m.color or {}).get("roles") or []):
                out.setdefault(rid, []).append(m.ref)
        return {k: sorted(v) for k, v in out.items()}

    def rows(self) -> list[dict]:
        """Una fila por rol del borrador: `id, nombre, hex, hex_guardado (None si es nuevo), cambiado, piezas (refs), n_piezas, anclas`."""
        usage = self.usage_map()
        saved = {r.id: r for r in (self.saved.roles if self.saved else [])}
        out = []
        for r in self.draft.roles:
            old = saved.get(r.id)
            refs = usage.get(r.id, [])
            out.append({"id": r.id, "nombre": r.nombre, "hex": r.hex, "hex_guardado": None if old is None else old.hex,
                        "cambiado": old is None or old.hex != r.hex or old.nombre != r.nombre,
                        "piezas": refs, "n_piezas": len(refs), "anclas": list(r.anclas)})
        return out

    def usage_notice(self, role_id: str) -> str:
        """Aviso de cuántas piezas recolorea cambiar ese rol (lo ven todas las que lo usan)."""
        refs = self.usage_map().get(role_id, [])
        if not refs:
            return tr("palette_panel.sin_piezas", rol=role_id)
        return tr("palette_panel.recolorea", rol=role_id, n=len(refs), piezas=", ".join(refs))

    def set_hex(self, role_id: str, hex_text: str) -> str:
        """Cambia el color del rol en el borrador (no guarda). Devuelve el `#RRGGBB` normalizado."""
        try:
            hx = normalize_hex(str(hex_text).strip(), tr("palette_panel.color_del_rol", rol=role_id))
            self.draft = self.draft.with_overrides({role_id: hx})
        except PaletteError as exc:
            raise PaletteEditError(str(exc)) from exc
        return hx

    def rename(self, role_id: str, nombre: str) -> None:
        """Cambia el nombre visible (el id, lo que guardan las piezas, no cambia)."""
        nombre = (nombre or "").strip()
        if not nombre:
            raise PaletteEditError(tr("palette_panel.nombre_vacio"))
        try:
            self.draft.get(role_id)
            self.draft = Palette([Role(r.id, nombre if r.id == role_id else r.nombre, r.hex, list(r.anclas)) for r in self.draft.roles])
        except PaletteError as exc:
            raise PaletteEditError(str(exc)) from exc

    def add(self, role_id: str, hex_text: str, nombre: str | None = None) -> None:
        role_id = (role_id or "").strip()
        if role_id in self.draft.ids():
            raise PaletteEditError(tr("palette_panel.rol_existe", rol=role_id))
        try:
            self.draft = Palette(self.draft.roles + [Role(role_id, (nombre or "").strip() or role_id, str(hex_text).strip(), [])])
        except PaletteError as exc:
            raise PaletteEditError(str(exc)) from exc

    def remove(self, role_id: str) -> None:
        """Quita el rol del borrador. Si alguna pieza lo usa se niega (el mensaje lista cuáles); un rol nuevo, sin aplicar, se quita sin más."""
        try:
            self.draft.get(role_id)
        except PaletteError as exc:
            raise PaletteEditError(str(exc)) from exc
        refs = self.usage_map().get(role_id, [])
        if refs:
            raise PaletteEditError(tr("palette_panel.rol_en_uso", rol=role_id, n=len(refs), piezas=", ".join(refs)))
        self.draft = Palette([r for r in self.draft.roles if r.id != role_id])

    # ------------------------------------------------------------------ guardar / descartar
    def apply(self) -> bool:
        """Escribe `paleta.json` con el borrador («Aplicar»). `False` si no había nada que aplicar. Nada más se reescribe (ni fichas ni STL)."""
        if not self.dirty:
            return False
        cat = self._cat()
        usage = self.usage_map()
        lost = {r: refs for r, refs in usage.items() if r not in self.draft.ids()}
        if lost:                                      # otra ventana pudo haber catalogado una pieza con un rol que el borrador ya no tiene
            r, refs = sorted(lost.items())[0]
            raise PaletteEditError(tr("palette_panel.rol_en_uso", rol=r, n=len(refs), piezas=", ".join(refs)))
        try:
            cat.set_palette(self.draft)
        except ValueError as exc:                     # CatalogError
            raise PaletteEditError(str(exc)) from exc
        self.reload()
        return True

    def revert(self) -> None:
        """Descarta el borrador y vuelve a lo guardado."""
        self.draft = _copy(self.saved)

    # ------------------------------------------------------------------ importar / exportar
    def export_json(self, path: str) -> str:
        """Escribe el borrador (lo que se ve) como JSON `afm-paleta-1`, para llevarlo a otra línea."""
        if not self.draft.roles:
            raise PaletteEditError(tr("palette_panel.paleta_vacia"))
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(self.draft.to_dict(), fh, indent=2, ensure_ascii=False)
                fh.write("\n")
        except OSError as exc:
            raise PaletteEditError(f"{path}: {exc}") from exc
        return path

    def import_json(self, path: str) -> None:
        """Reemplaza la paleta de la línea por la de un JSON `afm-paleta-1` y la guarda. Si el archivo no sirve, o le falta un rol que
        usan piezas, no cambia nada. Con cambios sin aplicar se niega (importar descartaría el borrador)."""
        if self.dirty:
            raise PaletteEditError(tr("palette_panel.hay_cambios"))
        cat = self._cat()
        try:
            cat.import_palette(path)
        except ValueError as exc:                     # CatalogError: JSON ilegible, formato malo o roles en uso que faltan
            raise PaletteEditError(str(exc)) from exc
        self.reload()

    # ------------------------------------------------------------------ vista previa
    def preview_refs(self) -> list[str]:
        """Piezas del catálogo (todas las versiones) con color por roles: las que se pueden ver pintadas."""
        from ..parts import color_mode

        return sorted(m.ref for m in self.ctrl.catalog_pieces() if m.color and color_mode(m.color) == "roles")

    def _package(self, ref: str):
        """`(paquete, similitud 4×4)` de la pieza `ref`; se recuerdan los últimos `PREVIEW_CACHE` mientras no cambie el archivo."""
        from ..colorpiece import _load_roles

        cat = self._cat()
        try:
            meta = cat.meta(ref)
            path = cat.color_file(ref)                # valida existencia y hash
        except ValueError as exc:
            raise PaletteEditError(str(exc)) from exc
        if path is None or not meta.color or "similitud" not in meta.color:
            raise PaletteEditError(tr("palette_panel.pieza_sin_roles", ref=ref))
        sha = (meta.color or {}).get("sha256", "")
        hit = self._pkgs.get(ref)
        if hit is not None and hit[0] == sha:
            return hit[1], hit[2]
        try:
            pkg = _load_roles(path)
        except (ValueError, OSError) as exc:
            raise PaletteEditError(f"{ref}: {exc}") from exc
        if len(self._pkgs) >= PREVIEW_CACHE:
            self._pkgs.pop(next(iter(self._pkgs)))
        sim = np.asarray(meta.color["similitud"], float)
        self._pkgs[ref] = (sha, pkg, sim)
        return pkg, sim

    def preview_lut(self, ref: str) -> np.ndarray:
        """Tabla (256, 3) uint8 de la pieza con la paleta del **borrador**: la fila `i` es el color del rol `i` del paquete."""
        pkg, _sim = self._package(ref)
        try:
            return self.draft.lut(pkg.roles)
        except PaletteError as exc:
            raise PaletteEditError(str(exc)) from exc

    def preview_atlas(self, ref: str) -> np.ndarray:
        """Atlas (H, W, 3) uint8 de la pieza pintado con el borrador (`lut[etiquetas]`: no hornea nada, ~45 ms a 2048²)."""
        pkg, _sim = self._package(ref)
        return np.ascontiguousarray(self.preview_lut(ref)[np.asarray(pkg.labels, np.uint8)])

    def preview_parts(self, ref: str) -> list:
        """`[TexturedPart]` de la pieza para `PreviewRenderer.render_textured`, en el marco del STL de la pieza (`similitud` FBX → STL) y
        pintada con el borrador."""
        import trimesh

        from .render import TexturedPart

        pkg, A = self._package(ref)
        image = self.preview_atlas(ref)
        V = np.asarray(pkg.vertices, float) @ A[:3, :3].T + A[:3, 3]
        F = np.asarray(pkg.faces, np.int64)
        uv = np.asarray(pkg.corner_uv, np.float32)
        if np.linalg.det(A[:3, :3]) < 0:              # un espejo invierte las caras: se las da vuelta para que las normales apunten afuera
            F, uv = F[:, ::-1], uv[:, ::-1]
        return [TexturedPart(ref, trimesh.Trimesh(V, F, process=False), np.ascontiguousarray(uv), image)]
