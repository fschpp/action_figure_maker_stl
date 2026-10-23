"""Recetas de armado (`afm-receta-1`) y armado de una figura solo con piezas de catálogo (Fase 6 del plan de piezas
intercambiables, §5 y Apéndice A).

Una receta dice, para un personaje de una línea, qué pieza va en cada lugar (`id@versión`), qué opciones de armado se usan
(cintura, clic de hombro/cadera, espejado del torso) y los ajustes de la mano. `assemble` abre el catálogo de la línea,
sintetiza el huésped con el torso y acopla el resto; el perfil de la línea (`linea.json`) da los `Params`.
"""
from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field

from . import pipeline as P
from .assembly import AssemblyError, PartChoice, synthesize_host
from .catalog import Catalog, CatalogError, default_root, parse_ref
from .params import Params

RECIPE_FORMAT = "afm-receta-1"
PLACES = ("cabeza", "torso", "pelvis", "brazo", "pierna", "mano")
OPTIONS = {"cintura": False, "clic_hombro": False, "clic_cadera": False, "torso_mirror": True}
HAND_ADJUSTS = ("yaw", "union", "solape")


class RecipeError(ValueError):
    """La receta está mal formada o pide algo que no se puede armar."""


@dataclass
class Recipe:
    personaje: str
    linea: str
    lugares: dict = field(default_factory=dict)          # lugar -> "id" | "id@versión" | None
    opciones: dict = field(default_factory=dict)
    ajustes: dict = field(default_factory=dict)

    # ------------------------------------------------------------------ formato
    def to_dict(self) -> dict:
        return {"formato": RECIPE_FORMAT, "personaje": self.personaje, "linea": self.linea,
                "lugares": {k: self.lugares.get(k) for k in PLACES}, "opciones": {**OPTIONS, **self.opciones},
                "ajustes": self.ajustes}

    @classmethod
    def from_dict(cls, d: dict) -> "Recipe":
        if d.get("formato") != RECIPE_FORMAT:
            raise RecipeError(f"formato de receta desconocido: {d.get('formato')!r} (se espera {RECIPE_FORMAT})")
        for k in ("personaje", "linea"):
            if not d.get(k):
                raise RecipeError(f"a la receta le falta «{k}»")
        r = cls(d["personaje"], d["linea"], dict(d.get("lugares") or {}), dict(d.get("opciones") or {}), dict(d.get("ajustes") or {}))
        bad = [k for k in r.problems()]
        if bad:
            raise RecipeError("; ".join(bad))
        return r

    @classmethod
    def load(cls, path: str) -> "Recipe":
        with open(path, encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, ensure_ascii=False, indent=2)

    def problems(self) -> list[str]:
        out = [f"lugar desconocido «{k}» (hay {', '.join(PLACES)})" for k in self.lugares if k not in PLACES]
        out += [f"opción desconocida «{k}» (hay {', '.join(OPTIONS)})" for k in self.opciones if k not in OPTIONS]
        out += [f"ajuste desconocido «{k}» (solo hay «mano»)" for k in self.ajustes if k != "mano"]
        out += [f"ajuste de mano desconocido «{k}» (hay {', '.join(HAND_ADJUSTS)})" for k in (self.ajustes.get("mano") or {})
                if k not in HAND_ADJUSTS]
        un = (self.ajustes.get("mano") or {}).get("union")
        if un is not None and un not in ("solape", "giro"):
            out.append(f"ajuste de mano: union «{un}» (hay solape, giro)")
        for k, v in self.lugares.items():
            if v is not None:
                try:
                    parse_ref(v)
                except CatalogError as exc:
                    out.append(f"{k}: {exc}")
        if not self.lugares.get("torso"):
            out.append("falta el lugar «torso»: sin figura de origen es el que arma el cuerpo")
        if self.options()["cintura"] and not self.lugares.get("pelvis"):
            out.append("con cintura hace falta una pieza en el lugar «pelvis»")
        if not self.options()["cintura"] and self.lugares.get("pelvis"):
            out.append("hay una pieza en «pelvis» pero la opción «cintura» está apagada")
        return out

    def options(self) -> dict:
        return {**OPTIONS, **self.opciones}

    def parts_keys(self) -> dict:
        """Lugar de la receta -> clave de `build(parts=)`: con cintura el torso es `torso_sup`."""
        keys = {}
        for lugar, ref in self.lugares.items():
            if ref:
                keys[lugar] = "torso_sup" if (lugar == "torso" and self.options()["cintura"]) else lugar
        return keys


@dataclass
class Assembled:
    res: P.BuildResult
    params: Params
    limbs: list
    joints: list
    recipe: Recipe
    catalog: Catalog | None = None


def recipe_params(recipe: Recipe, cat: Catalog) -> Params:
    """`Params` de la línea más las opciones de la receta."""
    o = recipe.options()
    return dataclasses.replace(cat.params, shoulder_detent=bool(o["clic_hombro"]), hip_detent=bool(o["clic_cadera"]),
                               torso_mirror=bool(o["torso_mirror"]))


def hand_spec(recipe: Recipe):
    from .hand import DEFAULT_OVERLAP_MM, HandSpec
    a = recipe.ajustes.get("mano")
    if not a or not recipe.lugares.get("mano"):
        return None
    return HandSpec(path="", yaw=float(a.get("yaw", 0.0)), join=a.get("union", "solape"),
                    overlap=float(a.get("solape", DEFAULT_OVERLAP_MM)))


def load_choices(recipe: Recipe, catalog_root: str | None = None) -> tuple[Catalog, dict]:
    root = catalog_root or default_root(recipe.linea)
    try:
        cat = Catalog.open(root)
        if cat.name != recipe.linea:
            raise RecipeError(f"la receta es de la línea «{recipe.linea}» y el catálogo es el de «{cat.name}»")
        choices = {key: PartChoice.from_catalog(cat, recipe.lugares[lugar]) for lugar, key in recipe.parts_keys().items()}
    except CatalogError as exc:
        raise RecipeError(str(exc)) from exc
    return cat, choices


def assemble(recipe: Recipe, catalog_root: str | None = None, progress=None, sit: bool = True) -> Assembled:
    """Arma la figura de la receta solo con piezas del catálogo de su línea."""
    cat, choices = load_choices(recipe, catalog_root)
    p = recipe_params(recipe, cat)
    try:
        host = synthesize_host(choices, p)
        res = P.build(host.body, host.limbs, p, None, sit, progress, None, host.head, None, host.waist, hand_spec(recipe),
                      host.parts, host_faces=host.faces)
    except AssemblyError as exc:
        raise RecipeError(str(exc)) from exc
    return Assembled(res, p, host.limbs, res.joint_specs, recipe, cat)
