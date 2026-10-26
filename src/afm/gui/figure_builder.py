"""Armar una figura desde cero, solo con piezas del catálogo (sin STL ni proyecto abiertos). Lógica sin GTK.

Es la versión de ventana de `afm armar`: se elige una pieza por lugar (torso, pelvis, cabeza, brazo, pierna, mano), se
genera, se valida y se exporta con el mismo motor (`cli.run_armar` → `recipe.assemble`). La receta (`afm-receta-1`) se puede
guardar en `<catálogo>/figuras/`, que es donde `Catalog.usages` y `afm catalogo compat` la buscan."""
from __future__ import annotations

import os
import re

from ..catalog import Catalog, CatalogError
from ..recipe import PLACES, Recipe

# lugar -> tipos de pieza que acepta (el torso puede ser entero o solo la parte de arriba)
PLACE_TYPES = {"torso": ("torso", "torso_sup"), "pelvis": ("pelvis",), "cabeza": ("cabeza",), "brazo": ("brazo",),
               "pierna": ("pierna",), "mano": ("mano",)}
PLACE_ORDER = ("torso", "pelvis", "cabeza", "brazo", "pierna", "mano")
PLACE_LABELS = {"torso": "Torso", "pelvis": "Pelvis", "cabeza": "Cabeza", "brazo": "Brazo", "pierna": "Pierna", "mano": "Mano"}
NONE_LABEL = "(ninguna)"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_RANK = {"ok": 0, "aviso": 1, "no": 2}


class FigureBuilder:
    """Elecciones de una figura nueva sobre un catálogo abierto."""

    def __init__(self, catalog_root: str, name: str = "figura-nueva"):
        self.root = os.path.abspath(catalog_root)
        self.name = name
        self.choices: dict[str, str | None] = {p: None for p in PLACE_ORDER}      # lugar -> "id@versión"
        self.options = {"clic_hombro": False, "clic_cadera": False, "torso_mirror": True}

    # ------------------------------------------------------------------ catálogo
    def catalog(self) -> Catalog:
        return Catalog.open(self.root)

    def pieces_for(self, place: str, cat: Catalog | None = None) -> list:
        """Todas las versiones de las piezas que sirven en ese lugar, por id y versión."""
        cat = cat or self.catalog()
        return sorted((m for m in cat.list(all_versions=True) if m.tipo in PLACE_TYPES[place]), key=lambda m: (m.id, m.version))

    def torso_type(self, cat: Catalog | None = None) -> str | None:
        ref = self.choices.get("torso")
        if not ref:
            return None
        try:
            return (cat or self.catalog()).meta(ref).tipo
        except CatalogError:
            return None

    @property
    def waist(self) -> bool:
        """Con un torso `torso_sup` la figura lleva cintura (y pelvis aparte); con un torso entero no."""
        return self.torso_type() == "torso_sup"

    def anchor_for(self, place: str) -> str | None:
        """Pieza ya elegida con la que `place` se acopla (para marcar cuáles de la lista encajan)."""
        c = self.choices
        if place in ("cabeza", "brazo", "pelvis"):
            return c.get("torso")
        if place == "pierna":
            return c.get("pelvis") if self.waist and c.get("pelvis") else c.get("torso")
        if place == "mano":
            return c.get("brazo")
        return None

    def fit_labels(self, place: str, cat: Catalog | None = None) -> dict[str, str]:
        """{ref: «ok» | «aviso» | «no acopla»} de las piezas de `place` respecto de la pieza elegida con la que se acopla
        (compatibilidad barata por puertos, sin generar). Vacío si todavía no hay con qué comparar."""
        cat = cat or self.catalog()
        anchor = self.anchor_for(place)
        if not anchor:
            return {}
        try:
            found = cat.compatible_with(anchor)
        except CatalogError:
            return {}
        best: dict[str, str] = {}
        for other, _na, _nb, comp in found:
            if best.get(other.ref) is None or _RANK[comp.nivel] < _RANK[best[other.ref]]:
                best[other.ref] = comp.nivel
        return {m.ref: best.get(m.ref, "no acopla") for m in self.pieces_for(place, cat)}

    def label(self, meta, fit: dict[str, str]) -> str:
        """Texto de una pieza en la lista: «brazo-a-01@2 — ok» (o sin marca si no hay con qué comparar)."""
        mark = fit.get(meta.ref)
        return meta.ref if not mark else f"{meta.ref} — {mark}"

    # ------------------------------------------------------------------ elecciones
    def set_choice(self, place: str, ref: str | None) -> None:
        if place not in PLACE_TYPES:
            raise ValueError(f"«{place}» no es un lugar (hay {', '.join(PLACE_ORDER)})")
        if ref:
            meta = self.catalog().meta(ref)           # CatalogError (ValueError) si no existe
            if meta.tipo not in PLACE_TYPES[place]:
                raise ValueError(f"«{meta.ref}» es una pieza «{meta.tipo}» y «{place}» pide «{' o '.join(PLACE_TYPES[place])}»")
            ref = meta.ref                            # siempre fija la versión
        self.choices[place] = ref or None
        if place == "torso" and not self.waist:
            self.choices["pelvis"] = None             # un torso entero no lleva pelvis aparte

    # ------------------------------------------------------------------ receta y armado
    def recipe(self) -> Recipe:
        cat = self.catalog()
        lugares = {p: self.choices.get(p) for p in PLACES}
        opciones = {"cintura": self.waist, **self.options}
        ajustes = {"mano": {"yaw": 0, "union": "solape"}} if lugares.get("mano") else {}
        return Recipe(self.name, cat.name, lugares, opciones, ajustes)

    def problems(self) -> list[str]:
        """Lo que falta o sobra antes de poder armar (vacío = listo)."""
        out = []
        if not NAME_RE.match(self.name or ""):
            out.append("el nombre de la figura admite letras, números, guiones y puntos (sin espacios)")
        if not self.choices.get("torso"):
            return out + ["elegí un torso: es la pieza que arma el cuerpo"]
        if self.waist and not self.choices.get("pelvis"):
            out.append("el torso elegido es solo la parte de arriba: elegí también una pelvis")
        if self.choices.get("mano") and not self.choices.get("brazo"):
            out.append("la mano se acopla a la muñeca de un brazo: elegí también un brazo")
        out += self.recipe().problems()
        return list(dict.fromkeys(out))

    def save_recipe(self) -> str:
        """Guarda la receta en `<catálogo>/figuras/<nombre>.receta.json` (así los usos de cada pieza la incluyen)."""
        bad = self.problems()
        if bad:
            raise ValueError("; ".join(bad))
        folder = os.path.join(self.root, "figuras")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{self.name}.receta.json")
        self.recipe().save(path)
        return path

    def generate(self, outdir: str, progress=print, orient: bool | str = False) -> dict:
        """Arma, valida y exporta en `outdir` (lo mismo que `afm armar`). Devuelve el informe: {"receta", "construccion",
        "validacion", "archivos"} más `ensamblada` (ruta del STL de la figura entera, para la vista previa). Tarda minutos."""
        from ..cli import run_armar

        bad = self.problems()
        if bad:
            raise ValueError("; ".join(bad))
        os.makedirs(outdir, exist_ok=True)
        recipe_path = os.path.join(outdir, f"{self.name}.receta.json")
        self.recipe().save(recipe_path)
        info = run_armar(recipe_path, outdir, self.name, self.root, progress=progress, orient=orient)
        info["ensamblada"] = os.path.join(outdir, info["archivos"]["ensamblada"])
        return info


def summary(info: dict) -> str:
    """Resumen legible del resultado de `FigureBuilder.generate` (validación y archivos)."""
    val = info.get("validacion", {})
    lines = ["Figura armada: " + ("sin problemas." if val.get("ok") else "CON PROBLEMAS.")]
    lines += [f"✗ {t}" for t in val.get("problems", [])[:10]]
    lines += [f"• {t}" for t in (val.get("avisos", []) + val.get("avisos_pasador", []))[:10]]
    lines.append(f"Piezas exportadas: {len(info.get('archivos', {}).get('piezas', []))} (más la figura ensamblada y la lista de piezas).")
    return "\n".join(lines)
